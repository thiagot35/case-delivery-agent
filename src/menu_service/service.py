"""Menu service core: reads over the current snapshot and writes that keep `menu.db` and the vector index in step."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from menu_service import store
from menu_service.models import Pizza, PriceEntry
from menu_service.protocols import MenuIndex
from menu_service.repository import MenuRepository
from menu_service.resolver import MenuResolver, normalize
from menu_service.schemas import (
    DeleteResponse,
    MenuResponse,
    PizzaView,
    PriceRow,
    QuoteResponse,
    ResolveResponse,
    SearchHit,
    SearchResponse,
    UpsertResponse,
)

logger = logging.getLogger(__name__)


class IndexUnavailableError(Exception):
    """The vector index could not be updated, so the menu write was rolled back."""


def price_rows(entries: list[PriceEntry]) -> list[PriceRow]:
    """Convert price entries to the API rows."""
    return [PriceRow(size=e.size, crust=e.crust, price=e.price) for e in entries]


class MenuService:
    """Owns `menu.db` and the vector index; every menu read and write of the system goes through it."""

    def __init__(self, db_path: Path, index: MenuIndex | None = None, semantic_max_distance: float = 0.75):
        """Keep the database path and the optional index, and load the first snapshot."""
        self._db_path = db_path
        self._index = index
        self._semantic_max_distance = semantic_max_distance
        self._write_lock = threading.Lock()
        self._state = self._load()

    @property
    def index_ready(self) -> bool:
        """Tell whether semantic search is available."""
        return self._index is not None and self._index.ready

    def sync_index(self) -> None:
        """Bring the vector index in line with the current menu (startup)."""
        if self._index is not None:
            self._index.sync(self._state[0])

    # =====================================================================================================
    # Reads
    # =====================================================================================================

    def menu(self) -> MenuResponse:
        """Return every orderable pizza with its prices, plus the size and crust names."""
        repository, _ = self._state
        return MenuResponse(
            pizzas=[self._view(repository, pizza) for pizza in repository.orderable_pizzas()],
            sizes=repository.list_sizes(),
            crusts=repository.list_crusts(),
        )

    def resolve_flavor(self, text: str) -> ResolveResponse:
        """Resolve free text to a flavor (exact, fuzzy, then semantic needing confirmation)."""
        repository, resolver = self._state
        match = resolver.resolve_flavor(text)
        return ResolveResponse(
            pizza=self._view(repository, match.pizza) if match.pizza else None,
            score=match.score,
            method=match.method,
            needs_confirmation=match.needs_confirmation,
            options=[] if match.pizza else [p.flavor for p in repository.orderable_pizzas()],
        )

    def quote(self, flavor: str, size: str, crust: str | None) -> QuoteResponse:
        """Resolve flavor, size and crust and return the exact price, or why it can't be priced."""
        repository, resolver = self._state
        match = resolver.resolve_flavor(flavor)
        if match.pizza is None:
            return QuoteResponse(status="flavor_not_found", options=[p.flavor for p in repository.orderable_pizzas()])
        pizza = self._view(repository, match.pizza)
        if match.needs_confirmation:
            return QuoteResponse(status="flavor_ambiguous", pizza=pizza, suggestion=match.pizza.flavor)

        resolved_size, resolved_crust = resolver.resolve_size(size), resolver.resolve_crust(crust)
        if resolved_size is None or resolved_crust is None:
            return QuoteResponse(
                status="unrecognized_option",
                pizza=pizza,
                alternatives=pizza.prices,
                sizes=repository.list_sizes(),
                crusts=repository.list_crusts(),
            )
        quote = repository.get_price(match.pizza.id, resolved_size, resolved_crust)
        if not quote.available or quote.entry is None:
            return QuoteResponse(
                status="combination_unavailable",
                pizza=pizza,
                size=resolved_size,
                crust=resolved_crust,
                alternatives=price_rows(quote.alternatives),
            )
        return QuoteResponse(
            status="ok", pizza=pizza, size=quote.entry.size, crust=quote.entry.crust, price=quote.entry.price
        )

    def search(self, query: str, limit: int = 3) -> SearchResponse:
        """Rank pizzas for a description: embeds the query and searches the vector index, else keyword overlap."""
        repository, _ = self._state
        if self._index is not None:
            try:
                ranked = [
                    (pizza, round(1 - distance, 3))
                    for pizza_id, distance in self._index.search(query, limit)
                    if (pizza := repository.get_pizza(pizza_id)) is not None
                ]
                if ranked:
                    return self._search_response(repository, query, "semantic", ranked)
            except Exception:
                logger.exception("Semantic search failed, falling back to keyword ranking")
        return self._search_response(repository, query, "keyword", self._keyword_rank(repository, query)[:limit])

    # =====================================================================================================
    # Writes
    # =====================================================================================================

    def upsert(self, table: store.Table, fields: dict[str, Any]) -> UpsertResponse:
        """Add or edit a row, re-embed the affected pizzas and publish the new snapshot."""
        result, reindexed = self._write(lambda conn: store.upsert(conn, table, fields))
        assert result.action != "deleted"
        return UpsertResponse(table=table, action=result.action, item=result.item, reindexed_pizza_ids=reindexed)

    def delete(self, table: store.Table, key: int | dict[str, int]) -> DeleteResponse:
        """Delete a row (cascading to prices), refresh the affected documents and publish the new snapshot."""
        result, reindexed = self._write(lambda conn: store.delete(conn, table, key))
        removed = [pizza_id for pizza_id in reindexed if not self._is_indexed(pizza_id)]
        return DeleteResponse(
            table=table,
            deleted=result.item,
            cascaded_prices=result.cascaded_prices,
            reindexed_pizza_ids=[pizza_id for pizza_id in reindexed if pizza_id not in removed],
            removed_pizza_ids=removed,
        )

    def _write(self, apply: Callable[..., store.WriteResult]) -> tuple[store.WriteResult, list[int]]:
        """Apply a change in a transaction; commit only after the index accepted the new documents."""
        with self._write_lock:
            with store.transaction(self._db_path) as conn:
                result = apply(conn)
                reindexed = sorted(result.affected_pizza_ids) if self._index is not None else []
                if reindexed:
                    assert self._index is not None
                    try:
                        self._index.refresh(MenuRepository.from_connection(conn), set(reindexed))
                    except Exception as exc:
                        logger.exception("Vector index update failed: rolling back the menu change")
                        raise IndexUnavailableError(str(exc)) from exc
            self._state = self._load()
        logger.info("menu %s %s: reindexed pizzas %s", result.table, result.action, reindexed)
        return result, reindexed

    # =====================================================================================================
    # Helpers
    # =====================================================================================================

    def _load(self) -> tuple[MenuRepository, MenuResolver]:
        """Read a fresh snapshot and its resolver (swapped as one tuple so readers never mix two versions)."""
        repository = MenuRepository.from_path(self._db_path)
        return repository, MenuResolver(repository, self._index, self._semantic_max_distance)

    def _is_indexed(self, pizza_id: int) -> bool:
        """Tell whether a pizza still has a document (it exists and has at least one price)."""
        repository, _ = self._state
        return bool(repository.get_pizza(pizza_id) and repository.price_matrix(pizza_id))

    @staticmethod
    def _view(repository: MenuRepository, pizza: Pizza) -> PizzaView:
        """Build the API view of a pizza with its prices and price range."""
        return PizzaView(
            id=pizza.id,
            flavor=pizza.flavor,
            description=pizza.description,
            ingredients=pizza.ingredients,
            min_price=repository.min_price(pizza.id),
            max_price=repository.max_price(pizza.id),
            prices=price_rows(repository.price_matrix(pizza.id)),
        )

    @staticmethod
    def _keyword_rank(repository: MenuRepository, query: str) -> list[tuple[Pizza, float]]:
        """Fallback when the vector index is unavailable: token overlap with the pizza text."""
        tokens = {t for t in normalize(query).split() if len(t) > 2}
        scored = []
        for pizza in repository.orderable_pizzas():
            text = normalize(f"{pizza.flavor} {pizza.description} {pizza.ingredients}")
            scored.append((pizza, float(sum(1 for token in tokens if token in text))))
        return sorted(scored, key=lambda item: item[1], reverse=True)

    def _search_response(
        self, repository: MenuRepository, query: str, method: str, ranked: list[tuple[Pizza, float]]
    ) -> SearchResponse:
        """Wrap ranked pizzas in the search response."""
        return SearchResponse(
            query=query,
            method=method,  # type: ignore[arg-type]
            results=[SearchHit(pizza=self._view(repository, pizza), relevance=score) for pizza, score in ranked],
        )
