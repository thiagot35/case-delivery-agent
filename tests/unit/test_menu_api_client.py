import json
from decimal import Decimal

import httpx
import pytest
import respx

from delivery_agent.clients.errors import MenuServiceError, MenuServiceUnavailableError
from delivery_agent.clients.menu_api_client import MenuApiClient

BASE = "http://menu.test"

PIZZA = {
    "id": 4,
    "flavor": "Calabresa",
    "description": "Saborosa.",
    "ingredients": "Calabresa, cebola.",
    "min_price": "27.00",
    "max_price": "51.00",
    "prices": [{"size": "Grande", "crust": "Tradicional", "price": "47.00"}],
}


@pytest.fixture
async def client():
    """Client bound to the mocked Menu service base URL."""
    http = MenuApiClient.create_http_client(BASE, 1)
    yield MenuApiClient(http)
    await http.aclose()


@respx.mock
async def test_reads_parse_the_contract(client: MenuApiClient) -> None:
    """Menu, resolve, quote and search responses are parsed into the agent DTOs."""
    respx.get(f"{BASE}/menu").respond(json={"pizzas": [PIZZA], "sizes": ["Grande"], "crusts": ["Tradicional"]})
    resolve = respx.get(f"{BASE}/menu/flavors/resolve").respond(
        json={"pizza": PIZZA, "score": 100, "method": "exact", "needs_confirmation": False, "options": []}
    )
    quote = respx.get(f"{BASE}/menu/quote").respond(
        json={"status": "ok", "pizza": PIZZA, "size": "Grande", "crust": "Tradicional", "price": "47.00"}
    )
    search = respx.post(f"{BASE}/menu/search").respond(
        json={"query": "q", "method": "semantic", "results": [{"pizza": PIZZA, "relevance": 0.8}]}
    )

    menu = await client.get_menu()
    assert menu.pizzas[0].max_price == Decimal("51.00")
    assert (await client.resolve_flavor("calabreza")).pizza is not None
    assert resolve.calls.last.request.url.params["text"] == "calabreza"
    assert (await client.quote("Calabresa", "Grande")).price == Decimal("47.00")
    assert "crust" not in quote.calls.last.request.url.params
    hits = await client.search("linguiça", limit=2)
    assert hits[0].pizza.flavor == "Calabresa"
    assert json.loads(search.calls.last.request.content) == {"query": "linguiça", "limit": 2}


@respx.mock
async def test_errors_are_typed(client: MenuApiClient) -> None:
    """Transport errors and 5xx are 'unavailable'; other 4xx are generic Menu service errors."""
    respx.get(f"{BASE}/menu").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(MenuServiceUnavailableError):
        await client.get_menu()

    respx.get(f"{BASE}/menu/quote").respond(status_code=503)
    with pytest.raises(MenuServiceUnavailableError):
        await client.quote("Calabresa", "Grande")

    respx.post(f"{BASE}/menu/search").respond(status_code=422, json={"detail": "bad"})
    with pytest.raises(MenuServiceError):
        await client.search("x")


@respx.mock
async def test_health_and_ping(client: MenuApiClient) -> None:
    """health returns the body, or None when the service is unreachable."""
    route = respx.get(f"{BASE}/health").respond(json={"status": "ok", "index": "ready"})
    assert await client.health() == {"status": "ok", "index": "ready"}
    route.mock(side_effect=httpx.ConnectTimeout("slow"))
    assert await client.ping() is False
