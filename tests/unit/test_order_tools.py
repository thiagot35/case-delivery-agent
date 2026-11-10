import asyncio
import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from delivery_agent.domain.models import DeliveryAddress
from delivery_agent.tools.order_tools import OrderTools
from tests.conftest import CPF, NAME, TODAY, FakeMenuApi, FakeOrderApi, make_run_context

YESTERDAY = date(2026, 11, 5)
ADDRESS = DeliveryAddress(street_name="Rua das Flores", number="100", complement="Ap 12", reference_point="Praça")


@pytest.fixture
def tools(order_api: FakeOrderApi, menu_api: FakeMenuApi) -> OrderTools:
    """Order tools wired to the in-memory Order API and the in-process Menu service."""
    return OrderTools(order_api, menu_api)  # type: ignore[arg-type]


def parse(result: str) -> dict[str, Any]:
    """Parse a tool result."""
    return json.loads(result)


# --- add_pizza ---------------------------------------------------------------------------------------------


async def test_first_add_creates_order_then_appends(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """The first add creates the order; the next ones append to it."""
    rc = make_run_context()
    first = parse(await tools.add_pizza(rc, "calabreza", "grande", "cheddar"))
    assert first["ok"]
    assert first["added"]["name"] == "Pizza Calabresa - Grande - Borda Recheada com Cheddar"
    assert first["added"]["unit_price_display"] == "R$ 50,00"
    assert first["order"]["total_price_display"] == "R$ 50,00"
    assert "create_order" in order_api.call_names()

    second = parse(await tools.add_pizza(rc, "Margherita", "Média", quantity=2))
    assert second["ok"]
    assert second["order"]["total_price_display"] == "R$ 120,00"
    assert order_api.call_names().count("create_order") == 1
    assert "add_items" in order_api.call_names()

    state = rc.session_state
    assert state is not None
    assert state["order_id"] == first["order"]["order_id"]
    assert state["order_status"] == "open"
    assert state["flow_step"] == "building"
    assert state["order_snapshot"]["total_price"] == "120.00"
    assert len(state["order_snapshot"]["items"]) == 2


async def test_parallel_adds_create_a_single_order(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Regression: the model may call add_pizza twice in parallel in the same turn."""
    rc = make_run_context()
    results = await asyncio.gather(
        tools.add_pizza(rc, "Calabresa", "Grande", "Recheada com Cheddar"),
        tools.add_pizza(rc, "Margherita", "Média", quantity=2),
    )
    assert all(parse(r)["ok"] for r in results)
    assert order_api.call_names().count("create_order") == 1
    assert len(order_api.orders) == 1
    order = next(iter(order_api.orders.values()))
    assert order.total_price == Decimal("120.00")


async def test_price_always_from_menu(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Item prices always come from the menu database."""
    rc = make_run_context()
    await tools.add_pizza(rc, "Doce de Leite com Coco", "Pequena")
    order = next(iter(order_api.orders.values()))
    assert order.items[0].unit_price == Decimal("25.00")
    assert order.client_name == NAME and order.client_document == CPF and order.delivery_date == TODAY


@pytest.mark.parametrize("quantity", [0, -1, 21, "abc"])
async def test_invalid_quantity_never_calls_api(tools: OrderTools, order_api: FakeOrderApi, quantity: Any) -> None:
    """Out-of-range quantities fail without calling the Order API."""
    result = parse(await tools.add_pizza(make_run_context(), "Calabresa", "Grande", quantity=quantity))
    assert result["error_code"] == "invalid_quantity"
    assert order_api.calls == []


async def test_unavailable_combination_lists_alternatives(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Unavailable combinations fail with the valid alternatives."""
    result = parse(await tools.add_pizza(make_run_context(), "Margherita", "Pequena", "Recheada com Catupiry"))
    assert result["error_code"] == "combination_unavailable"
    assert {"size": "Pequena", "crust": "Tradicional", "price_display": "R$ 25,00"} in result["alternatives"]
    assert order_api.calls == []


async def test_unknown_flavor_and_ambiguous(tools: OrderTools) -> None:
    """Unknown flavors fail; semantic-only matches ask for confirmation."""
    not_found = parse(await tools.add_pizza(make_run_context(), "sushi", "Grande"))
    assert not_found["error_code"] == "flavor_not_found"
    assert "Calabresa" in not_found["options"]

    ambiguous = parse(await tools.add_pizza(make_run_context(), "aquela doce", "Grande"))
    assert ambiguous["error_code"] == "flavor_ambiguous"
    assert ambiguous["suggestion"] == "Doce de Leite com Coco"


async def test_unknown_size(tools: OrderTools) -> None:
    """Unrecognized sizes fail with the valid alternatives."""
    result = parse(await tools.add_pizza(make_run_context(), "Calabresa", "gigante"))
    assert result["error_code"] == "combination_unavailable"


async def test_adopts_today_order_instead_of_duplicating(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """An existing order for today is reused, not duplicated."""
    existing = order_api.seed(TODAY, [("Pizza Calabresa - Grande - Borda Tradicional", 1, "47")])
    rc = make_run_context()
    result = parse(await tools.add_pizza(rc, "Pepperoni", "Média"))
    assert result["ok"]
    assert result["order"]["order_id"] == existing.id
    assert "create_order" not in order_api.call_names()
    assert len(order_api.orders[existing.id].items) == 2


async def test_unique_race_adopts_existing(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """A unique-constraint race adopts the order created by the other request."""
    rc = make_run_context()
    original_find = order_api.find_orders
    calls = {"n": 0}

    async def find_racing(document: str, delivery_date: date | None = None):
        """Report no order on the first lookup, as if another request created it right after."""
        calls["n"] += 1
        if calls["n"] == 1:  # lookup before create: nothing yet, then another request creates it
            order_api.seed(TODAY, [])
            return []
        return await original_find(document, delivery_date)

    order_api.find_orders = find_racing  # type: ignore[method-assign]
    result = parse(await tools.add_pizza(rc, "Calabresa", "Grande"))
    assert result["ok"]
    assert len(order_api.orders) == 1
    assert result["order"]["total_price_display"] == "R$ 47,00"


async def test_stale_order_id_recovers(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """A stale order id in the state is dropped and the order recreated."""
    rc = make_run_context({"order_id": 999, "order_status": "open"})
    result = parse(await tools.add_pizza(rc, "Calabresa", "Grande"))
    assert result["ok"]
    assert rc.session_state is not None and rc.session_state["order_id"] != 999


async def test_api_unavailable_returns_friendly_error(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Order API outages become an order_api_unavailable result."""
    order_api.unavailable = True
    result = parse(await tools.add_pizza(make_run_context(), "Calabresa", "Grande"))
    assert result["error_code"] == "order_api_unavailable"


async def test_missing_customer_context(tools: OrderTools) -> None:
    """Tools fail safely when the identity dependencies are missing."""
    rc = make_run_context()
    rc.dependencies = {}
    assert parse(await tools.get_current_order(rc))["error_code"] == "missing_customer_context"


# --- remove / current ----------------------------------------------------------------------------------------


async def test_remove_item_and_reopen_confirmed(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Removing an item reopens a confirmed order."""
    rc = make_run_context()
    added = parse(await tools.add_pizza(rc, "Calabresa", "Grande", quantity=2))
    item_id = added["order"]["items"][0]["item_id"]
    assert rc.session_state is not None
    rc.session_state["order_status"] = "confirmed"

    result = parse(await tools.remove_item(rc, item_id))
    assert result["ok"]
    assert result["order"]["items"] == []
    assert result["order"]["total_price_display"] == "R$ 0,00"
    assert rc.session_state["order_status"] == "open"

    missing = parse(await tools.remove_item(rc, 12345))
    assert missing["error_code"] == "item_not_found"


async def test_tools_require_order(tools: OrderTools) -> None:
    """Order-dependent tools fail with no_open_order before the first item."""
    rc = make_run_context()
    for result in (
        await tools.remove_item(rc, 1),
        await tools.get_current_order(rc),
        await tools.get_delivery_address(rc),
        await tools.confirm_delivery_address(rc),
        await tools.set_delivery_address(rc, "Rua A", "1"),
        await tools.confirm_order(rc),
    ):
        assert parse(result)["error_code"] == "no_open_order"


# --- history / repeat ----------------------------------------------------------------------------------------


async def test_history_for_new_customer(tools: OrderTools) -> None:
    """A customer without orders gets an empty history."""
    rc = make_run_context()
    result = parse(await tools.get_customer_history(rc))
    assert result == {"ok": True, "has_history": False, "today_order": None, "last_order": None}


async def test_history_returns_last_order_and_flavors(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """History returns the last previous order and the flavors ordered."""
    order_api.seed(date(2026, 10, 1), [("Pizza Pepperoni - Média - Borda Tradicional", 1, "38")])
    last = order_api.seed(YESTERDAY, [("Pizza Calabresa - Grande - Borda Tradicional", 2, "47")], ADDRESS)
    rc = make_run_context()
    result = parse(await tools.get_customer_history(rc))
    assert result["last_order"]["order_id"] == last.id
    assert result["last_order"]["total_price_display"] == "R$ 94,00"
    assert "delivery_address" not in result["last_order"]  # address only at the address step
    assert result["today_order"] is None
    assert rc.session_state is not None
    assert rc.session_state["last_order_id"] == last.id
    assert rc.session_state["previous_flavors"] == ["Calabresa", "Pepperoni"]
    assert rc.session_state["order_id"] is None


async def test_history_adopts_today_order(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """History adopts today's order into the session."""
    today = order_api.seed(TODAY, [("Pizza Calabresa - Grande - Borda Tradicional", 1, "47")])
    rc = make_run_context()
    result = parse(await tools.get_customer_history(rc))
    assert result["today_order"]["order_id"] == today.id
    assert rc.session_state is not None and rc.session_state["order_id"] == today.id


async def test_repeat_last_order_reprices_and_copies_address(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Repeating re-prices items, reports changes and copies the address."""
    order_api.seed(
        YESTERDAY,
        [
            ("Pizza Calabresa - Grande - Borda Tradicional", 2, "40"),  # price changed since then
            ("Coca-Cola 2L", 1, "12"),  # foreign item, skipped
        ],
        ADDRESS,
    )
    rc = make_run_context()
    await tools.get_customer_history(rc)
    result = parse(await tools.repeat_last_order(rc))
    assert result["ok"]
    assert result["added_items"] == ["Pizza Calabresa - Grande - Borda Tradicional"]
    assert result["skipped_items"] == ["Coca-Cola 2L"]
    assert result["price_changes"] == [
        {"name": "Pizza Calabresa - Grande - Borda Tradicional", "old": "R$ 40,00", "new": "R$ 47,00"}
    ]
    assert result["order"]["total_price_display"] == "R$ 94,00"
    assert result["order"]["delivery_address"] == "Rua das Flores, 100 - Ap 12 (Praça)"
    new_order = order_api.orders[result["order"]["order_id"]]
    assert new_order.delivery_date == TODAY


async def test_repeat_without_history(tools: OrderTools) -> None:
    """Repeating without a previous order fails with no_history."""
    assert parse(await tools.repeat_last_order(make_run_context()))["error_code"] == "no_history"


# --- address step (tool-first) -----------------------------------------------------------------------------


async def test_address_source_none_then_set(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """With no known address, a new one is saved with set_delivery_address."""
    rc = make_run_context()
    await tools.add_pizza(rc, "Calabresa", "Grande")
    found = parse(await tools.get_delivery_address(rc))
    assert found == {"ok": True, "source": "none", "address": None}

    invalid = parse(await tools.set_delivery_address(rc, "Rua A", ""))
    assert invalid["error_code"] == "invalid_address"

    saved = parse(await tools.set_delivery_address(rc, "Rua A", "10", "", "Perto do mercado"))
    assert saved["ok"]
    assert saved["address_display"] == "Rua A, 10 (Perto do mercado)"
    assert rc.session_state is not None and rc.session_state["address_set"] is True


async def test_address_from_last_order_confirmed(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Confirming the last order's address writes it to the current order."""
    order_api.seed(date(2026, 10, 1), [("Pizza Pepperoni - Média - Borda Tradicional", 1, "38")], ADDRESS)
    order_api.seed(YESTERDAY, [("Pizza Calabresa - Grande - Borda Tradicional", 1, "47")])  # no address
    rc = make_run_context()
    await tools.add_pizza(rc, "Calabresa", "Grande")

    found = parse(await tools.get_delivery_address(rc))
    assert found["source"] == "last_order"
    assert found["address_display"] == "Rua das Flores, 100 - Ap 12 (Praça)"

    before = order_api.call_names().count("update_address")
    confirmed = parse(await tools.confirm_delivery_address(rc))
    assert confirmed["ok"]
    assert order_api.call_names().count("update_address") == before + 1
    assert order_api.orders[rc.session_state["order_id"]].delivery_address == ADDRESS  # type: ignore[index]
    assert rc.session_state["address_candidate"] is None  # type: ignore[index]


async def test_address_from_current_order_no_write(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Confirming the current order's own address makes no API write."""
    rc = make_run_context()
    await tools.add_pizza(rc, "Calabresa", "Grande")
    await tools.set_delivery_address(rc, "Rua B", "5")
    found = parse(await tools.get_delivery_address(rc))
    assert found["source"] == "current_order"

    before = order_api.call_names().count("update_address")
    assert parse(await tools.confirm_delivery_address(rc))["ok"]
    assert order_api.call_names().count("update_address") == before


async def test_confirm_address_requires_candidate(tools: OrderTools) -> None:
    """Confirming an address without fetching a candidate first fails."""
    rc = make_run_context()
    await tools.add_pizza(rc, "Calabresa", "Grande")
    assert parse(await tools.confirm_delivery_address(rc))["error_code"] == "no_address_candidate"


# --- confirm / cancel ----------------------------------------------------------------------------------------


async def test_confirm_requires_items_and_address(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """confirm_order requires items and an address."""
    rc = make_run_context()
    added = parse(await tools.add_pizza(rc, "Calabresa", "Grande"))
    assert parse(await tools.confirm_order(rc))["error_code"] == "missing_address"

    await tools.set_delivery_address(rc, "Rua A", "1")
    await tools.remove_item(rc, added["order"]["items"][0]["item_id"])
    assert parse(await tools.confirm_order(rc))["error_code"] == "missing_items"

    await tools.add_pizza(rc, "Calabresa", "Grande")
    confirmed = parse(await tools.confirm_order(rc))
    assert confirmed["ok"]
    assert confirmed["total_price_display"] == "R$ 47,00"
    assert rc.session_state is not None
    assert rc.session_state["order_status"] == "confirmed"
    assert rc.session_state["order_snapshot"]["status"] == "confirmed"
    assert rc.session_state["confirmed_at"]

    # A write after confirmation reopens the order (EC-10).
    await tools.add_pizza(rc, "Pepperoni", "Grande")
    assert rc.session_state["order_status"] == "open"


async def test_cancel_order(tools: OrderTools, order_api: FakeOrderApi) -> None:
    """Cancelling removes every item and marks the order cancelled."""
    rc = make_run_context()
    await tools.add_pizza(rc, "Calabresa", "Grande", quantity=2)
    await tools.add_pizza(rc, "Pepperoni", "Grande")
    result = parse(await tools.cancel_order(rc))
    assert result["ok"]
    assert rc.session_state is not None
    order = order_api.orders[rc.session_state["order_id"]]
    assert order.items == []
    assert rc.session_state["order_status"] == "cancelled"
    assert rc.session_state["order_snapshot"]["status"] == "cancelled"

    # Ordering again the same day reuses the same order and reopens it.
    again = parse(await tools.add_pizza(rc, "Calabresa", "Média"))
    assert again["order"]["order_id"] == order.id
    assert rc.session_state["order_status"] == "open"


async def test_menu_service_down(tools: OrderTools, menu_api: FakeMenuApi, order_api: FakeOrderApi) -> None:
    """When the Menu service is down no item is priced or added, and the model gets menu_unavailable."""
    menu_api.unavailable = True
    result = parse(await tools.add_pizza(make_run_context(), "Calabresa", "Grande"))
    assert result["error_code"] == "menu_unavailable"
    assert order_api.orders == {}
