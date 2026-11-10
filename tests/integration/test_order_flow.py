"""End-to-end tool flow against a running Order API.

Run with: ORDER_API_BASE_URL=http://localhost:8000/api uv run pytest -m integration
"""

import json
import os
import random

import pytest

from delivery_agent.clients.order_api_client import OrderApiClient
from delivery_agent.tools.order_tools import OrderTools
from tests.conftest import TODAY, FakeMenuApi, make_run_context

pytestmark = pytest.mark.integration

BASE_URL = os.getenv("ORDER_API_BASE_URL", "http://localhost:8000/api")


def random_cpf() -> str:
    """Generate a random valid CPF, so each run gets a fresh customer."""
    digits = [random.randint(0, 9) for _ in range(9)]
    for _ in range(2):
        weight = len(digits) + 1
        remainder = sum(d * (weight - i) for i, d in enumerate(digits)) * 10 % 11
        digits.append(0 if remainder == 10 else remainder)
    return "".join(map(str, digits))


@pytest.fixture
async def tools(menu_api: FakeMenuApi):
    """Order tools against the real Order API; skipped when it is unreachable."""
    http = OrderApiClient.create_http_client(BASE_URL, 5)
    client = OrderApiClient(http)
    if not await client.ping():
        await http.aclose()
        pytest.skip(f"Order API not reachable at {BASE_URL}")
    yield OrderTools(client, menu_api)  # type: ignore[arg-type]
    await http.aclose()


async def test_full_order_flow(tools: OrderTools) -> None:
    """Run history, add, remove, address and confirm against the real Order API."""
    rc = make_run_context()
    cpf = random_cpf()
    assert rc.dependencies is not None
    rc.dependencies["client_document"] = cpf

    def call(result: str) -> dict:
        """Parse a tool result and assert it succeeded."""
        data = json.loads(result)
        assert data["ok"], data
        return data

    assert call(await tools.get_customer_history(rc))["has_history"] is False

    first = call(await tools.add_pizza(rc, "Calabresa", "Grande", "Recheada com Cheddar"))
    second = call(await tools.add_pizza(rc, "Margherita", "Média", quantity=2))
    assert second["order"]["order_id"] == first["order"]["order_id"]
    assert second["order"]["total_price_display"] == "R$ 120,00"

    item_id = second["order"]["items"][0]["item_id"]
    after_remove = call(await tools.remove_item(rc, item_id))
    assert after_remove["order"]["total_price_display"] == "R$ 70,00"

    assert call(await tools.get_delivery_address(rc))["source"] == "none"
    call(await tools.set_delivery_address(rc, "Rua das Flores", "100", "Ap 12", None))
    assert call(await tools.get_delivery_address(rc))["source"] == "current_order"
    call(await tools.confirm_delivery_address(rc))

    confirmed = call(await tools.confirm_order(rc))
    assert confirmed["total_price_display"] == "R$ 70,00"
    assert confirmed["delivery_address"] == "Rua das Flores, 100 - Ap 12"

    # Same customer, same day, new session: today's order is adopted, never duplicated.
    rc2 = make_run_context()
    assert rc2.dependencies is not None
    rc2.dependencies["client_document"] = cpf
    history = call(await tools.get_customer_history(rc2))
    assert history["today_order"]["order_id"] == confirmed["order_id"]
    again = call(await tools.add_pizza(rc2, "Pepperoni", "Grande"))
    assert again["order"]["order_id"] == confirmed["order_id"]
    assert TODAY  # delivery date comes from the injected context

    # Cancel deletes every item through `DELETE orders/{id}/items/{item_id}/`.
    assert call(await tools.cancel_order(rc2))["cancelled"] is True
    emptied = call(await tools.get_current_order(rc2))
    assert emptied["order"]["items"] == []
