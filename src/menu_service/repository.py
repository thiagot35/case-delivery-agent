"""Read-only, in-memory snapshot of the pizza knowledge base.

The menu is tiny, so it is loaded into memory at once. The Menu service builds a new snapshot after every write and
swaps it atomically, so readers never see a half-applied change.
"""

import sqlite3
from decimal import Decimal
from pathlib import Path

from menu_service.formatting import to_money
from menu_service.models import Crust, Pizza, PriceEntry, PriceQuote, Size

_PRICES_SQL = """
SELECT p.id, p.sabor, t.id, t.tamanho, b.id, b.tipo, pr.preco
FROM precos pr
JOIN pizzas p ON p.id = pr.pizza_id
JOIN tamanhos t ON t.id = pr.tamanho_id
JOIN bordas b ON b.id = pr.borda_id
ORDER BY p.id, t.id, b.id
"""


class MenuRepository:
    """Snapshot of pizzas, sizes, crusts and prices with lookup helpers."""

    def __init__(self, pizzas: list[Pizza], sizes: list[Size], crusts: list[Crust], prices: list[PriceEntry]):
        """Index the loaded rows by pizza id."""
        self._pizzas = {pizza.id: pizza for pizza in pizzas}
        self._sizes = list(sizes)
        self._crusts = list(crusts)
        self._prices: dict[int, list[PriceEntry]] = {pizza_id: [] for pizza_id in self._pizzas}
        for entry in prices:
            self._prices.setdefault(entry.pizza_id, []).append(entry)

    @classmethod
    def from_path(cls, db_path: Path) -> "MenuRepository":
        """Load a snapshot from the menu database file."""
        conn = sqlite3.connect(db_path)
        try:
            return cls.from_connection(conn)
        finally:
            conn.close()

    @classmethod
    def from_connection(cls, conn: sqlite3.Connection) -> "MenuRepository":
        """Load a snapshot through an open connection (it also sees that connection's uncommitted writes)."""
        pizzas = [
            Pizza(id=row[0], flavor=row[1], description=row[2], ingredients=row[3])
            for row in conn.execute("SELECT id, sabor, descricao, ingredientes FROM pizzas ORDER BY id")
        ]
        sizes = [Size(id=row[0], name=row[1]) for row in conn.execute("SELECT id, tamanho FROM tamanhos ORDER BY id")]
        crusts = [Crust(id=row[0], name=row[1]) for row in conn.execute("SELECT id, tipo FROM bordas ORDER BY id")]
        prices = [
            PriceEntry(
                pizza_id=row[0],
                flavor=row[1],
                size_id=row[2],
                size=row[3],
                crust_id=row[4],
                crust=row[5],
                price=to_money(row[6]),
            )
            for row in conn.execute(_PRICES_SQL)
        ]
        return cls(pizzas, sizes, crusts, prices)

    # --- Queries -----------------------------------------------------------------------------------------

    def list_pizzas(self) -> list[Pizza]:
        """Return every pizza, ordered by id (including pizzas that have no price yet)."""
        return list(self._pizzas.values())

    def orderable_pizzas(self) -> list[Pizza]:
        """Return the pizzas that have at least one price: the ones shown to customers and indexed."""
        return [pizza for pizza in self._pizzas.values() if self._prices.get(pizza.id)]

    def get_pizza(self, pizza_id: int) -> Pizza | None:
        """Return the pizza with `pizza_id`, or None."""
        return self._pizzas.get(pizza_id)

    def get_pizza_by_flavor(self, flavor: str) -> Pizza | None:
        """Return the pizza whose flavor matches exactly (case-insensitive), or None."""
        return next((p for p in self._pizzas.values() if p.flavor.lower() == flavor.lower()), None)

    def list_sizes(self) -> list[str]:
        """Return the size names in menu order."""
        return [size.name for size in self._sizes]

    def list_crusts(self) -> list[str]:
        """Return the crust names in menu order."""
        return [crust.name for crust in self._crusts]

    def price_matrix(self, pizza_id: int) -> list[PriceEntry]:
        """Return every valid size/crust/price combination of a pizza."""
        return list(self._prices.get(pizza_id, []))

    def min_price(self, pizza_id: int) -> Decimal | None:
        """Return the cheapest price of a pizza, or None when it has no prices."""
        return min((e.price for e in self._prices.get(pizza_id) or []), default=None)

    def max_price(self, pizza_id: int) -> Decimal | None:
        """Return the most expensive price of a pizza, or None when it has no prices."""
        return max((e.price for e in self._prices.get(pizza_id) or []), default=None)

    def get_price(self, pizza_id: int, size: str, crust: str) -> PriceQuote:
        """Exact price for a combination, or the valid alternatives for the flavor when unavailable."""
        entries = self._prices.get(pizza_id, [])
        for entry in entries:
            if entry.size.lower() == size.lower() and entry.crust.lower() == crust.lower():
                return PriceQuote(available=True, entry=entry)
        return PriceQuote(available=False, alternatives=entries)
