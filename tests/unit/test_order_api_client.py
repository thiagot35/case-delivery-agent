import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from delivery_agent.clients import order_api_client as client_module
from delivery_agent.clients.errors import (
    ItemNotFoundError,
    OrderAlreadyExistsError,
    OrderApiUnavailableError,
    OrderApiValidationError,
    OrderNotFoundError,
)
from delivery_agent.clients.order_api_client import OrderApiClient
from delivery_agent.domain.models import CustomerContext, DeliveryAddress, OrderItemIn

BASE = "http://orders.test/api"
# CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person).
CUSTOMER = CustomerContext(client_name="Maria Silva", client_document="52998224725")
ITEM = OrderItemIn(name="Pizza Calabresa - Grande - Borda Tradicional", quantity=1, unit_price=Decimal("47"))


def order_json(order_id: int = 1, **overrides: object) -> dict:
    """Order API JSON for one order, with optional field overrides."""
    data = {
        "id": order_id,
        "client_name": "Maria Silva",
        "client_document": "52998224725",
        "delivery_date": "2026-11-06",
        "delivery_address": None,
        "created_at": "2026-11-06T12:00:00Z",
        "updated_at": "2026-11-06T12:00:00Z",
        "items": [{"id": 10, "name": ITEM.name, "quantity": 1, "unit_price": 47.0}],
        "total_price": 47.0,
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the retry backoff to keep tests fast."""
    monkeypatch.setattr(client_module, "GET_RETRY_BACKOFF_SECONDS", 0)


@pytest.fixture
async def client():
    """Order API client pointed at the mocked base URL."""
    http = OrderApiClient.create_http_client(BASE, 1)
    yield OrderApiClient(http)
    await http.aclose()


@respx.mock
async def test_create_order_payload(client: OrderApiClient) -> None:
    """create_order sends the expected payload and parses the order."""
    route = respx.post(f"{BASE}/orders/").mock(return_value=httpx.Response(201, json=order_json()))
    address = DeliveryAddress(street_name="Rua A", number="1")
    order = await client.create_order(CUSTOMER, date(2026, 11, 6), [ITEM], address)

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "client_name": "Maria Silva",
        "client_document": "52998224725",
        "delivery_date": "2026-11-06",
        "items": [{"name": ITEM.name, "quantity": 1, "unit_price": "47.00"}],
        "delivery_address": {"street_name": "Rua A", "number": "1", "complement": None, "reference_point": None},
    }
    assert order.id == 1
    assert order.total_price == Decimal("47.0")


@respx.mock
async def test_create_order_unique_violation(client: OrderApiClient) -> None:
    """The unique-constraint 400 maps to OrderAlreadyExistsError."""
    body = {"non_field_errors": ["The fields client_name, client_document, delivery_date must make a unique set."]}
    respx.post(f"{BASE}/orders/").mock(return_value=httpx.Response(400, json=body))
    with pytest.raises(OrderAlreadyExistsError):
        await client.create_order(CUSTOMER, date(2026, 11, 6), [ITEM])


@respx.mock
async def test_create_order_validation_error_not_retried(client: OrderApiClient) -> None:
    """Other 400 errors map to OrderApiValidationError, not retried."""
    route = respx.post(f"{BASE}/orders/").mock(return_value=httpx.Response(400, json={"items": ["bad"]}))
    with pytest.raises(OrderApiValidationError) as exc:
        await client.create_order(CUSTOMER, date(2026, 11, 6), [ITEM])
    assert not isinstance(exc.value, OrderAlreadyExistsError)
    assert route.call_count == 1


@respx.mock
async def test_post_not_retried_on_5xx(client: OrderApiClient) -> None:
    """POST requests are never retried on 5xx."""
    route = respx.post(f"{BASE}/orders/").mock(return_value=httpx.Response(503))
    with pytest.raises(OrderApiUnavailableError):
        await client.create_order(CUSTOMER, date(2026, 11, 6), [ITEM])
    assert route.call_count == 1


@respx.mock
async def test_get_order_not_found(client: OrderApiClient) -> None:
    """A 404 on get_order maps to OrderNotFoundError."""
    respx.get(f"{BASE}/orders/9/").mock(return_value=httpx.Response(404, json={"detail": "x"}))
    with pytest.raises(OrderNotFoundError):
        await client.get_order(9)


@respx.mock
async def test_get_retried_once_on_5xx(client: OrderApiClient) -> None:
    """GET requests are retried once after a 5xx."""
    route = respx.get(f"{BASE}/orders/1/").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json=order_json())]
    )
    order = await client.get_order(1)
    assert order.id == 1
    assert route.call_count == 2


@respx.mock
async def test_get_retry_exhausted_on_timeout(client: OrderApiClient) -> None:
    """Two GET timeouts raise OrderApiUnavailableError."""
    route = respx.get(f"{BASE}/orders/1/").mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(OrderApiUnavailableError):
        await client.get_order(1)
    assert route.call_count == 2


@respx.mock
async def test_find_orders_sorted_desc_with_params(client: OrderApiClient) -> None:
    """find_orders sends the filters and sorts orders by id descending."""
    route = respx.get(f"{BASE}/orders/filter/").mock(
        return_value=httpx.Response(200, json=[order_json(1), order_json(3), order_json(2)])
    )
    orders = await client.find_orders("52998224725", date(2026, 11, 6))
    assert [o.id for o in orders] == [3, 2, 1]
    params = route.calls.last.request.url.params
    assert params["client_document"] == "52998224725"
    assert params["delivery_date"] == "2026-11-06"


@respx.mock
async def test_add_items(client: OrderApiClient) -> None:
    """add_items sends the items payload."""
    route = respx.patch(f"{BASE}/orders/1/add-items/").mock(
        return_value=httpx.Response(200, json={"detail": "1 itens adicionados com sucesso."})
    )
    await client.add_items(1, [ITEM])
    assert json.loads(route.calls.last.request.content)["items"][0]["unit_price"] == "47.00"


@respx.mock
async def test_add_items_errors(client: OrderApiClient) -> None:
    """add_items maps 404 and 400 to typed errors."""
    respx.patch(f"{BASE}/orders/1/add-items/").mock(return_value=httpx.Response(400, json={"errors": [{}]}))
    respx.patch(f"{BASE}/orders/2/add-items/").mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(OrderApiValidationError):
        await client.add_items(1, [ITEM])
    with pytest.raises(OrderNotFoundError):
        await client.add_items(2, [ITEM])


@respx.mock
async def test_remove_item(client: OrderApiClient) -> None:
    """remove_item succeeds on 204 and maps 404 to ItemNotFoundError."""
    route = respx.delete(f"{BASE}/orders/1/items/10/").mock(return_value=httpx.Response(204))
    respx.delete(f"{BASE}/orders/1/items/11/").mock(return_value=httpx.Response(404, json={}))
    await client.remove_item(1, 10)
    # The API's 204 carries a body, so the connection must not be reused after a delete.
    assert route.calls.last.request.headers["Connection"] == "close"
    with pytest.raises(ItemNotFoundError):
        await client.remove_item(1, 11)


@respx.mock
async def test_update_address_sends_all_keys(client: OrderApiClient) -> None:
    """update_address always sends all four address keys."""
    route = respx.patch(f"{BASE}/orders/1/update-address/").mock(return_value=httpx.Response(200, json={}))
    await client.update_address(1, DeliveryAddress(street_name="Rua B", number="2", complement="Casa"))
    assert json.loads(route.calls.last.request.content) == {
        "delivery_address": {"street_name": "Rua B", "number": "2", "complement": "Casa", "reference_point": None}
    }


@respx.mock
async def test_update_address_not_found(client: OrderApiClient) -> None:
    """A 404 on update_address maps to OrderNotFoundError."""
    respx.patch(f"{BASE}/orders/5/update-address/").mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(OrderNotFoundError):
        await client.update_address(5, DeliveryAddress(street_name="Rua B", number="2"))


@respx.mock
async def test_ping(client: OrderApiClient) -> None:
    """ping returns True when the API answers."""
    respx.get(f"{BASE}/orders/filter/").mock(return_value=httpx.Response(200, json=[]))
    assert await client.ping()


@respx.mock
async def test_ping_unreachable(client: OrderApiClient) -> None:
    """ping returns False when the API is unreachable."""
    respx.get(f"{BASE}/orders/filter/").mock(side_effect=httpx.ConnectError("refused"))
    assert not await client.ping()
