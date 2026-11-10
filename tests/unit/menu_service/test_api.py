from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from menu_service.app import create_app
from menu_service.config import MenuSettings
from menu_service.service import MenuService
from tests.conftest import FakeIndex


@pytest.fixture
def index() -> FakeIndex:
    """Fake index recording the refreshed pizzas."""
    return FakeIndex()


@pytest.fixture
def client(writable_menu_db: Path, index: FakeIndex) -> TestClient:
    """Menu service app over a private menu database (no admin token)."""
    service = MenuService(writable_menu_db, index)
    return TestClient(create_app(MenuSettings(menu_admin_token=None), service))


NEW_PIZZA = {"table": "pizzas", "fields": {"sabor": "Portuguesa", "descricao": "Clássica.", "ingredientes": "Ovo."}}


def test_add_pizza_with_price_appears_in_menu_and_is_embedded(client: TestClient, index: FakeIndex) -> None:
    """A pizza becomes orderable once it has a price, and each write re-embeds it."""
    created = client.put("/menu/items", json=NEW_PIZZA)
    assert created.status_code == 200, created.text
    body = created.json()
    pizza_id = body["item"]["id"]
    assert body["action"] == "created" and body["reindexed_pizza_ids"] == [pizza_id]
    assert "Portuguesa" not in [p["flavor"] for p in client.get("/menu").json()["pizzas"]]  # no price yet

    price = {"pizza_id": pizza_id, "tamanho_id": 3, "borda_id": 1, "preco": "52.00"}
    assert client.put("/menu/items", json={"table": "precos", "fields": price}).json()["action"] == "created"
    portuguesa = next(p for p in client.get("/menu").json()["pizzas"] if p["flavor"] == "Portuguesa")
    assert portuguesa["min_price"] == portuguesa["max_price"] == "52.00"
    assert index.refreshed == [{pizza_id}, {pizza_id}]

    quote = client.get("/menu/quote", params={"flavor": "portuguesa", "size": "grande"}).json()
    assert quote["status"] == "ok" and quote["price"] == "52.00"


def test_edit_pizza(client: TestClient) -> None:
    """With an id the pizza is partially updated."""
    response = client.put("/menu/items", json={"table": "pizzas", "fields": {"id": 4, "descricao": "Nova descrição."}})
    assert response.status_code == 200
    assert response.json()["action"] == "updated"
    assert response.json()["item"]["sabor"] == "Calabresa"
    calabresa = client.get("/menu/flavors/resolve", params={"text": "calabresa"}).json()["pizza"]
    assert calabresa["description"] == "Nova descrição."


@pytest.mark.parametrize(
    ("payload", "status", "error_code"),
    [
        ({"table": "precos", "fields": {"pizza_id": 1, "tamanho_id": 1, "borda_id": 9, "preco": "10"}}, 422, None),
        ({"table": "tamanhos", "fields": {"tamanho": "Grande"}}, 409, "duplicate"),
        ({"table": "bordas", "fields": {"id": 99, "tipo": "X"}}, 404, "not_found"),
        ({"table": "pizzas", "fields": {"sabor": "Só o nome"}}, 422, None),  # creating needs every column
        ({"table": "clientes", "fields": {"id": 1}}, 422, None),  # unknown table
        ({"table": "precos", "fields": {"pizza_id": 1, "tamanho_id": 1, "borda_id": 1, "preco": "-1"}}, 422, None),
    ],
)
def test_upsert_errors(client: TestClient, payload: dict, status: int, error_code: str | None) -> None:
    """Invalid writes are rejected with a clear status and leave the menu as it was."""
    response = client.put("/menu/items", json=payload)
    assert response.status_code == status, response.text
    if error_code:
        assert response.json()["error_code"] == error_code


def test_price_with_unknown_reference_lists_it(client: TestClient) -> None:
    """The 422 for a price names the reference that does not exist."""
    payload = {"table": "precos", "fields": {"pizza_id": 1, "tamanho_id": 1, "borda_id": 9, "preco": "10"}}
    body = client.put("/menu/items", json=payload).json()
    assert body == {**body, "error_code": "reference_not_found", "missing": {"borda_id": 9}}


def test_delete_crust_cascades_and_reindexes(client: TestClient, index: FakeIndex) -> None:
    """Deleting a crust removes its prices and refreshes the pizzas that used it."""
    response = client.request("DELETE", "/menu/items", json={"table": "bordas", "id": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == {"id": 2, "tipo": "Recheada com Cheddar"}
    assert body["cascaded_prices"] > 0
    assert body["removed_pizza_ids"] == []
    assert set(body["reindexed_pizza_ids"]) == index.refreshed[-1]
    menu = client.get("/menu").json()
    assert "Recheada com Cheddar" not in menu["crusts"]
    assert all(p["crust"] != "Recheada com Cheddar" for pizza in menu["pizzas"] for p in pizza["prices"])


def test_delete_pizza_and_last_price(client: TestClient) -> None:
    """A deleted pizza, or one that loses its last price, is reported as removed from the index."""
    deleted = client.request("DELETE", "/menu/items", json={"table": "pizzas", "id": 6}).json()
    assert deleted["removed_pizza_ids"] == [6] and deleted["cascaded_prices"] == 3

    new_id = client.put("/menu/items", json=NEW_PIZZA).json()["item"]["id"]
    key = {"pizza_id": new_id, "tamanho_id": 1, "borda_id": 1}
    client.put("/menu/items", json={"table": "precos", "fields": {**key, "preco": "20"}})
    body = client.request("DELETE", "/menu/items", json={"table": "precos", "id": key}).json()
    assert body["removed_pizza_ids"] == [new_id]


def test_delete_not_found(client: TestClient) -> None:
    """Deleting a missing row is a 404."""
    response = client.request("DELETE", "/menu/items", json={"table": "tamanhos", "id": 99})
    assert response.status_code == 404


def test_index_failure_rolls_back_the_write(client: TestClient, index: FakeIndex) -> None:
    """When re-embedding fails the change is rolled back and the API answers 503."""
    index.fail_refresh = True
    response = client.request("DELETE", "/menu/items", json={"table": "pizzas", "id": 1})
    assert response.status_code == 503
    assert response.json()["error_code"] == "index_unavailable"
    assert "Margherita" in [p["flavor"] for p in client.get("/menu").json()["pizzas"]]


def test_admin_token(writable_menu_db: Path) -> None:
    """With MENU_ADMIN_TOKEN set, writes need the X-Admin-Token header; reads stay open."""
    app = create_app(MenuSettings(menu_admin_token="s3cret"), MenuService(writable_menu_db, FakeIndex()))
    with TestClient(app) as client:
        assert client.put("/menu/items", json=NEW_PIZZA).status_code == 401
        assert client.put("/menu/items", json=NEW_PIZZA, headers={"X-Admin-Token": "nope"}).status_code == 401
        assert client.put("/menu/items", json=NEW_PIZZA, headers={"X-Admin-Token": "s3cret"}).status_code == 200
        assert client.get("/menu").status_code == 200


def test_read_endpoints(client: TestClient) -> None:
    """Resolve, quote, search and health answer with the contract shapes."""
    resolved = client.get("/menu/flavors/resolve", params={"text": "sushi"}).json()
    assert resolved["pizza"] is None and "Calabresa" in resolved["options"]

    unavailable = client.get(
        "/menu/quote", params={"flavor": "Margherita", "size": "Pequena", "crust": "cheddar"}
    ).json()
    assert unavailable["status"] == "combination_unavailable"
    assert unavailable["alternatives"]

    assert client.get("/menu/quote", params={"flavor": "Calabresa", "size": "gigante"}).json()["status"] == (
        "unrecognized_option"
    )

    search = client.post("/menu/search", json={"query": "algo doce", "limit": 1}).json()
    assert search["method"] == "semantic"
    assert search["results"][0]["pizza"]["flavor"] == "Doce de Leite com Coco"
    assert search["results"][0]["pizza"]["max_price"] == "45.00"

    assert client.get("/health").json() == {"status": "ok", "index": "ready"}
