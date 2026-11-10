"""pt-BR formatting helpers: money, menu, item names, addresses and order views for the agent."""

import re
from decimal import Decimal
from typing import Any

from delivery_agent.domain.models import DeliveryAddress, Menu, Order, ParsedItemName, to_money

_ITEM_NAME = re.compile(r"^Pizza (?P<flavor>.+?) - (?P<size>.+?) - Borda (?P<crust>.+)$")


def format_brl(value: Decimal | float | int | str) -> str:
    """Format a value as Brazilian currency: `R$ 1.234,50`."""
    amount = to_money(value)
    sign = "-" if amount < 0 else ""
    integer, cents = f"{abs(amount):.2f}".split(".")
    integer = f"{int(integer):,}".replace(",", ".")
    return f"{sign}R$ {integer},{cents}"


def menu_summary(menu: Menu) -> dict[str, Any]:
    """Short menu (specs/007-short-replies): flavor names with starting prices, the cheapest price of each size and
    the crusts. No descriptions, ingredients or price table, so the model can't send the full menu."""
    by_size: dict[str, Decimal] = {}
    for pizza in menu.pizzas:
        for row in pizza.prices:
            by_size[row.size] = min(by_size.get(row.size, row.price), row.price)
    return {
        "flavors": [
            {"flavor": pizza.flavor, "from_price_display": format_brl(min(row.price for row in pizza.prices))}
            for pizza in menu.pizzas
            if pizza.prices
        ],
        "from_price_by_size": [
            {"size": size, "from_price_display": format_brl(by_size[size])} for size in menu.sizes if size in by_size
        ],
        "crusts": menu.crusts,
    }


def build_item_name(flavor: str, size: str, crust: str) -> str:
    """Item name convention stored in the Order API (parseable by `parse_item_name`)."""
    return f"Pizza {flavor} - {size} - Borda {crust}"


def parse_item_name(name: str) -> ParsedItemName | None:
    """Parse a name built by `build_item_name`. Returns None for names created by other clients."""
    match = _ITEM_NAME.match(name.strip())
    if not match:
        return None
    return ParsedItemName(**{k: v.strip() for k, v in match.groupdict().items()})


def format_address(address: DeliveryAddress | None) -> str | None:
    """Render an address as one pt-BR line, or None when there is no address."""
    if address is None:
        return None
    text = f"{address.street_name}, {address.number}"
    if address.complement:
        text += f" - {address.complement}"
    if address.reference_point:
        text += f" ({address.reference_point})"
    return text


def order_view(order: Order, status: str) -> dict[str, Any]:
    """Compact, pre-formatted view of an order returned to the model by the tools."""
    return {
        "order_id": order.id,
        "status": status,
        "delivery_date": order.delivery_date.isoformat(),
        "items": [
            {
                "item_id": item.id,
                "name": item.name,
                "quantity": item.quantity,
                "unit_price_display": format_brl(item.unit_price),
                "subtotal_display": format_brl(item.unit_price * item.quantity),
            }
            for item in order.items
        ],
        "items_count": sum(item.quantity for item in order.items),
        "delivery_address": format_address(order.delivery_address),
        "total_price_display": format_brl(order.total_price),
    }
