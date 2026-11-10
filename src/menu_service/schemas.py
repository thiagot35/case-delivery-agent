"""HTTP contract of the Menu service (specs/002-menu-service/contracts/menu-service-api.md)."""

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# =========================================================================================================
# Write requests: the table to change and its fields (column names of menu.db)
# =========================================================================================================

Text = Annotated[str, Field(min_length=1, max_length=500)]
Id = Annotated[int, Field(ge=1)]


class _EntityFields(BaseModel):
    """Fields of an `id`-keyed table: without `id` the row is created, with `id` it is partially updated."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: Id | None = None

    @model_validator(mode="after")
    def _check_required(self) -> "_EntityFields":
        """Creating needs every column; updating needs at least one."""
        columns = [name for name in type(self).model_fields if name != "id"]
        given = [name for name in columns if getattr(self, name) is not None]
        if self.id is None and len(given) != len(columns):
            raise ValueError(f"creating requires: {', '.join(columns)}")
        if self.id is not None and not given:
            raise ValueError(f"updating requires at least one of: {', '.join(columns)}")
        return self


class PizzaFields(_EntityFields):
    """Columns of `pizzas`."""

    sabor: Text | None = None
    descricao: Text | None = None
    ingredientes: Text | None = None


class SizeFields(_EntityFields):
    """Columns of `tamanhos`."""

    tamanho: Text | None = None


class CrustFields(_EntityFields):
    """Columns of `bordas`."""

    tipo: Text | None = None


class PriceKey(BaseModel):
    """Composite key of `precos`."""

    model_config = ConfigDict(extra="forbid")

    pizza_id: Id
    tamanho_id: Id
    borda_id: Id


class PriceFields(PriceKey):
    """Columns of `precos`: the key plus the price (created when the key is new, updated otherwise)."""

    preco: Decimal = Field(gt=0, max_digits=8, decimal_places=2)


class UpsertPizza(BaseModel):
    """Add or edit a pizza."""

    table: Literal["pizzas"]
    fields: PizzaFields


class UpsertSize(BaseModel):
    """Add or edit a size."""

    table: Literal["tamanhos"]
    fields: SizeFields


class UpsertCrust(BaseModel):
    """Add or edit a crust."""

    table: Literal["bordas"]
    fields: CrustFields


class UpsertPrice(BaseModel):
    """Add or edit a price."""

    table: Literal["precos"]
    fields: PriceFields


UpsertRequest = Annotated[UpsertPizza | UpsertSize | UpsertCrust | UpsertPrice, Field(discriminator="table")]


class DeleteEntity(BaseModel):
    """Delete a pizza, size or crust (and every price that references it)."""

    table: Literal["pizzas", "tamanhos", "bordas"]
    id: Id


class DeletePrice(BaseModel):
    """Delete one price, identified by its composite key."""

    table: Literal["precos"]
    id: PriceKey


DeleteRequest = Annotated[DeleteEntity | DeletePrice, Field(discriminator="table")]


class UpsertResponse(BaseModel):
    """Result of an add/edit."""

    table: str
    action: Literal["created", "updated"]
    item: dict[str, Any]
    reindexed_pizza_ids: list[int]


class DeleteResponse(BaseModel):
    """Result of a delete."""

    table: str
    deleted: dict[str, Any]
    cascaded_prices: int
    reindexed_pizza_ids: list[int]
    removed_pizza_ids: list[int]


# =========================================================================================================
# Read responses (consumed by the agent)
# =========================================================================================================


class PriceRow(BaseModel):
    """One valid size/crust combination and its price."""

    size: str
    crust: str
    price: Decimal


class PizzaView(BaseModel):
    """An orderable pizza with its price range and every valid combination."""

    id: int
    flavor: str
    description: str
    ingredients: str
    min_price: Decimal | None
    max_price: Decimal | None
    prices: list[PriceRow]


class MenuResponse(BaseModel):
    """The whole orderable menu."""

    pizzas: list[PizzaView]
    sizes: list[str]
    crusts: list[str]


class ResolveResponse(BaseModel):
    """Flavor resolution; `options` lists the flavors when nothing matched."""

    pizza: PizzaView | None
    score: float
    method: Literal["exact", "fuzzy", "semantic", "none"]
    needs_confirmation: bool
    options: list[str] = Field(default_factory=list)


QuoteStatus = Literal["ok", "flavor_not_found", "flavor_ambiguous", "unrecognized_option", "combination_unavailable"]


class QuoteResponse(BaseModel):
    """Resolution + pricing of a flavor/size/crust request."""

    status: QuoteStatus
    pizza: PizzaView | None = None
    size: str | None = None
    crust: str | None = None
    price: Decimal | None = None
    alternatives: list[PriceRow] = Field(default_factory=list)
    suggestion: str | None = None
    options: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)
    crusts: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    """Semantic search over the menu."""

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=6)


class SearchHit(BaseModel):
    """A search result with its relevance (1 - distance for semantic results, keyword overlap otherwise)."""

    pizza: PizzaView
    relevance: float


class SearchResponse(BaseModel):
    """Search results, most relevant first; `method` tells whether the vector index answered."""

    query: str
    method: Literal["semantic", "keyword"]
    results: list[SearchHit]


class HealthResponse(BaseModel):
    """Liveness of the service and whether the vector index is loaded."""

    status: Literal["ok"] = "ok"
    index: Literal["ready", "missing"]
