"""Internal menu models built from `menu.db`."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class Pizza(BaseModel):
    """A row of `pizzas`."""

    id: int
    flavor: str
    description: str
    ingredients: str


class Size(BaseModel):
    """A row of `tamanhos`."""

    id: int
    name: str


class Crust(BaseModel):
    """A row of `bordas`."""

    id: int
    name: str


class PriceEntry(BaseModel):
    """A row of `precos` joined with the pizza, size and crust names."""

    pizza_id: int
    flavor: str
    size_id: int
    size: str
    crust_id: int
    crust: str
    price: Decimal


class PriceQuote(BaseModel):
    """Price lookup result: the entry when the combination exists, else the flavor's valid alternatives."""

    available: bool
    entry: PriceEntry | None = None
    alternatives: list[PriceEntry] = Field(default_factory=list)


class FlavorMatch(BaseModel):
    """Outcome of resolving free text to a pizza."""

    pizza: Pizza | None
    score: float
    method: Literal["exact", "fuzzy", "semantic", "none"]
    needs_confirmation: bool = False
