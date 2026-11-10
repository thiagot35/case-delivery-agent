"""Money helpers for the Menu service (kept local so the package does not depend on the agent)."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_money(value: Decimal | float | int | str) -> Decimal:
    """Convert a value to a Decimal rounded half-up to cents."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def format_brl(value: Decimal | float | int | str) -> str:
    """Format a value as Brazilian currency: `R$ 1.234,50`."""
    amount = to_money(value)
    sign = "-" if amount < 0 else ""
    integer, cents = f"{abs(amount):.2f}".split(".")
    integer = f"{int(integer):,}".replace(",", ".")
    return f"{sign}R$ {integer},{cents}"
