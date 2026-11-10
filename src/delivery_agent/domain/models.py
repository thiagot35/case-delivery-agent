"""Domain models shared by the Menu service client, the Order API client, the tools and the HTTP API."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CENT = Decimal("0.01")


def to_money(value: Decimal | float | int | str) -> Decimal:
    """Convert a value to a Decimal rounded half-up to cents."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# --- Menu (DTOs of the Menu service API: contracts/menu-service-api.md) ------------------------------------


class MenuPrice(BaseModel):
    size: str
    crust: str
    price: Decimal


class MenuPizza(BaseModel):
    id: int
    flavor: str
    description: str
    ingredients: str
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    prices: list[MenuPrice] = Field(default_factory=list)


class Menu(BaseModel):
    pizzas: list[MenuPizza]
    sizes: list[str]
    crusts: list[str]


class FlavorMatch(BaseModel):
    pizza: MenuPizza | None
    score: float
    method: Literal["exact", "fuzzy", "semantic", "none"]
    needs_confirmation: bool = False
    options: list[str] = Field(default_factory=list)


class MenuQuote(BaseModel):
    status: Literal["ok", "flavor_not_found", "flavor_ambiguous", "unrecognized_option", "combination_unavailable"]
    pizza: MenuPizza | None = None
    size: str | None = None
    crust: str | None = None
    price: Decimal | None = None
    alternatives: list[MenuPrice] = Field(default_factory=list)
    suggestion: str | None = None
    options: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)
    crusts: list[str] = Field(default_factory=list)


class MenuSearchHit(BaseModel):
    pizza: MenuPizza
    relevance: float


# --- Customer / orders ------------------------------------------------------------------------------------


class CustomerContext(BaseModel):
    client_name: str
    client_document: str

    @property
    def first_name(self) -> str:
        """First word of the customer's name, used to greet them."""
        return self.client_name.strip().split()[0] if self.client_name.strip() else ""


class DeliveryAddress(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    street_name: str = Field(min_length=1, max_length=255)
    number: str = Field(min_length=1, max_length=20)
    complement: str | None = Field(default=None, max_length=255)
    reference_point: str | None = Field(default=None, max_length=255)

    @field_validator("complement", "reference_point", mode="before")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """Store blank optional fields as None."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class OrderItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=1, le=20)
    unit_price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)


class OrderItem(BaseModel):
    id: int
    name: str
    quantity: int
    unit_price: Decimal


class Order(BaseModel):
    id: int
    client_name: str
    client_document: str
    delivery_date: date
    delivery_address: DeliveryAddress | None = None
    items: list[OrderItem] = Field(default_factory=list)
    total_price: Decimal = Decimal("0")
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ParsedItemName(BaseModel):
    flavor: str
    size: str
    crust: str


# --- Snapshots (session state + HTTP responses) -----------------------------------------------------------

OrderStatus = Literal["none", "open", "confirmed", "cancelled"]


class OrderSnapshotItem(BaseModel):
    item_id: int
    name: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


class OrderSnapshot(BaseModel):
    order_id: int
    status: OrderStatus
    delivery_date: date
    items: list[OrderSnapshotItem]
    delivery_address: DeliveryAddress | None
    total_price: Decimal

    @classmethod
    def from_order(cls, order: Order, status: OrderStatus) -> "OrderSnapshot":
        """Build a snapshot (items, subtotals, address, total) from an Order API order."""
        return cls(
            order_id=order.id,
            status=status,
            delivery_date=order.delivery_date,
            items=[
                OrderSnapshotItem(
                    item_id=item.id,
                    name=item.name,
                    quantity=item.quantity,
                    unit_price=to_money(item.unit_price),
                    subtotal=to_money(item.unit_price * item.quantity),
                )
                for item in order.items
            ],
            delivery_address=order.delivery_address,
            total_price=to_money(order.total_price),
        )
