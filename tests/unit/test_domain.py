from datetime import date
from decimal import Decimal

import pytest

from delivery_agent.domain.cpf import is_valid_cpf, mask_cpf, normalize_cpf
from delivery_agent.domain.formatting import (
    build_item_name,
    format_address,
    format_brl,
    menu_summary,
    order_view,
    parse_item_name,
)
from delivery_agent.domain.models import (
    DeliveryAddress,
    Menu,
    MenuPizza,
    MenuPrice,
    Order,
    OrderItem,
    OrderSnapshot,
)


def make_pizza(pizza_id: int, flavor: str, prices: list[tuple[str, str, str]]) -> MenuPizza:
    """Menu pizza with (size, crust, price) rows."""
    return MenuPizza(
        id=pizza_id,
        flavor=flavor,
        description="d",
        ingredients="i",
        prices=[MenuPrice(size=s, crust=c, price=Decimal(p)) for s, c, p in prices],
    )


def test_menu_summary_has_starting_prices() -> None:
    """Each size starts at its cheapest price over every flavor and crust; flavors keep their own starting price."""
    menu = Menu(
        pizzas=[
            make_pizza(1, "Calabresa", [("Pequena", "Tradicional", "27"), ("Grande", "Tradicional", "47")]),
            make_pizza(2, "Margherita", [("Pequena", "Tradicional", "25"), ("Grande", "Recheada", "44")]),
        ],
        sizes=["Pequena", "Grande"],
        crusts=["Tradicional", "Recheada"],
    )
    assert menu_summary(menu) == {
        "flavors": [
            {"flavor": "Calabresa", "from_price_display": "R$ 27,00"},
            {"flavor": "Margherita", "from_price_display": "R$ 25,00"},
        ],
        "from_price_by_size": [
            {"size": "Pequena", "from_price_display": "R$ 25,00"},
            {"size": "Grande", "from_price_display": "R$ 44,00"},
        ],
        "crusts": ["Tradicional", "Recheada"],
    }


def test_menu_summary_skips_a_size_without_prices() -> None:
    """A size no flavor is sold in, and a flavor without prices, are left out of the summary."""
    menu = Menu(
        pizzas=[make_pizza(1, "Calabresa", [("Média", "Tradicional", "37")]), make_pizza(2, "Nova", [])],
        sizes=["Pequena", "Média"],
        crusts=["Tradicional"],
    )
    summary = menu_summary(menu)
    assert summary["from_price_by_size"] == [{"size": "Média", "from_price_display": "R$ 37,00"}]
    assert [f["flavor"] for f in summary["flavors"]] == ["Calabresa"]


# Valid CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person);
# the invalid ones are made up to fail validation.
@pytest.mark.parametrize("cpf", ["529.982.247-25", "52998224725", " 529 982 247 25 "])
def test_valid_cpf(cpf: str) -> None:
    """Valid CPFs are accepted with or without punctuation."""
    assert is_valid_cpf(cpf)
    assert normalize_cpf(cpf) == "52998224725"


@pytest.mark.parametrize("cpf", ["", "123", "11111111111", "52998224724", "5299822472a5", "529982247250"])
def test_invalid_cpf(cpf: str) -> None:
    """Malformed, repeated-digit and wrong-check-digit CPFs are rejected."""
    assert not is_valid_cpf(cpf)


def test_mask_cpf() -> None:
    """Only the last 4 digits of a CPF stay visible."""
    assert mask_cpf("52998224725") == "***.***.*47-25"
    assert mask_cpf("x") == "***"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("47"), "R$ 47,00"), (1234.5, "R$ 1.234,50"), ("0.1", "R$ 0,10"), (Decimal("-3"), "-R$ 3,00")],
)
def test_format_brl(value: object, expected: str) -> None:
    """Money is formatted as pt-BR reais."""
    assert format_brl(value) == expected  # type: ignore[arg-type]


def test_item_name_round_trip() -> None:
    """Item names built from a combination parse back to it."""
    name = build_item_name("Frango com Catupiry", "Média", "Recheada com Cheddar")
    assert name == "Pizza Frango com Catupiry - Média - Borda Recheada com Cheddar"
    parsed = parse_item_name(name)
    assert parsed is not None
    assert (parsed.flavor, parsed.size, parsed.crust) == ("Frango com Catupiry", "Média", "Recheada com Cheddar")


def test_parse_foreign_item_name() -> None:
    """Names not created by this agent are not parsed."""
    assert parse_item_name("Coca-Cola 2L") is None


def test_format_address() -> None:
    """Addresses render on one line with optional parts."""
    assert format_address(None) is None
    full = DeliveryAddress(street_name="Rua A", number="10", complement="Ap 1", reference_point="Praça")
    assert format_address(full) == "Rua A, 10 - Ap 1 (Praça)"
    assert format_address(DeliveryAddress(street_name="Rua A", number="10", complement="  ")) == "Rua A, 10"


def test_order_view_and_snapshot() -> None:
    """Order views and snapshots carry items, subtotals and the total."""
    order = Order(
        id=7,
        client_name="Maria",
        client_document="52998224725",
        delivery_date=date(2026, 11, 6),
        items=[OrderItem(id=1, name="Pizza X - Média - Borda Tradicional", quantity=2, unit_price=Decimal("35"))],
        total_price=Decimal("70"),
    )
    view = order_view(order, "open")
    assert view["total_price_display"] == "R$ 70,00"
    assert view["items"][0]["subtotal_display"] == "R$ 70,00"
    assert view["items_count"] == 2

    snapshot = OrderSnapshot.from_order(order, "confirmed")
    assert snapshot.items[0].subtotal == Decimal("70.00")
    assert snapshot.model_dump(mode="json")["total_price"] == "70.00"
