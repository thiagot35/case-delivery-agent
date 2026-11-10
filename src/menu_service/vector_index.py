"""Semantic index of the menu (LanceDB through Agno).

Chunking: one document per menu item (pizza), never split. Each document is stored under
`content_hash = "pizza-{id}"`, so a single pizza can be re-embedded (upsert) or removed without touching the others.
"""

import json
import logging
from pathlib import Path
from typing import Any

from agno.knowledge.document import Document
from agno.knowledge.embedder.base import Embedder
from agno.vectordb.distance import Distance
from agno.vectordb.lancedb import LanceDb

from menu_service.formatting import format_brl
from menu_service.models import Pizza
from menu_service.protocols import MenuIndex
from menu_service.repository import MenuRepository

logger = logging.getLogger(__name__)

TABLE_NAME = "menu"


def document_id(pizza_id: int) -> str:
    """Stable document id (and LanceDB content hash) of a pizza."""
    return f"pizza-{pizza_id}"


def build_document(repository: MenuRepository, pizza: Pizza) -> Document | None:
    """The single chunk of one pizza: flavor, description, ingredients, availability and price range.

    Returns None for a pizza without prices (it cannot be ordered, so it is not indexed).
    """
    entries = repository.price_matrix(pizza.id)
    min_price, max_price = repository.min_price(pizza.id), repository.max_price(pizza.id)
    if not entries or min_price is None or max_price is None:
        return None
    sizes = sorted({e.size for e in entries}, key=repository.list_sizes().index)
    crusts = sorted({e.crust for e in entries}, key=repository.list_crusts().index)
    content = (
        f"{pizza.flavor}. {pizza.description} Ingredientes: {pizza.ingredients}. "
        f"Tamanhos: {', '.join(sizes)}. Bordas: {', '.join(crusts)}. "
        f"A partir de {format_brl(min_price)}."
    )
    return Document(
        id=document_id(pizza.id),
        name=pizza.flavor,
        content=content,
        meta_data={
            "pizza_id": pizza.id,
            "flavor": pizza.flavor,
            "min_price": float(min_price),
            "max_price": float(max_price),
        },
    )


def build_documents(repository: MenuRepository) -> list[Document]:
    """One document per orderable pizza."""
    return [doc for pizza in repository.list_pizzas() if (doc := build_document(repository, pizza)) is not None]


class MenuVectorIndex(MenuIndex):
    """LanceDB implementation of the `MenuIndex` protocol used by `MenuService` and `MenuResolver`."""

    def __init__(self, path: Path, embedder: Embedder):
        """Open (or prepare) the LanceDB table at `path` using `embedder`."""
        self._path = path
        self._db = LanceDb(uri=str(path), table_name=TABLE_NAME, embedder=embedder, distance=Distance.cosine)

    @property
    def ready(self) -> bool:
        """Tell whether the LanceDB table exists and holds documents."""
        return self._db.exists() and self._db.get_count() > 0

    def sync(self, repository: MenuRepository) -> None:
        """Make the index match the whole menu: upsert new/changed documents and delete orphan rows."""
        self._ensure_table()
        expected = {doc.id: doc for doc in build_documents(repository)}
        rows = self._stored_rows()
        stored = {row["content_hash"]: row for row in rows}
        changed = [
            doc
            for doc_id, doc in expected.items()
            if (row := stored.get(doc_id)) is None or (row["content"], row["meta_data"]) != (doc.content, doc.meta_data)
        ]
        # Rows of older index versions (keyed by another hash) are orphans too.
        orphans = [row["row_id"] for row in rows if row["content_hash"] not in expected]
        self._upsert(changed)
        for row_id in orphans:
            self._db.delete_by_id(row_id)
        logger.info(
            "Menu vector index synced at %s: %d documents, %d upserted, %d removed",
            self._path,
            len(expected),
            len(changed),
            len(orphans),
        )

    def refresh(self, repository: MenuRepository, pizza_ids: set[int]) -> None:
        """Re-embed the given pizzas; remove the ones deleted from the menu or left without prices."""
        self._ensure_table()
        documents: list[Document] = []
        for pizza_id in sorted(pizza_ids):
            pizza = repository.get_pizza(pizza_id)
            doc = build_document(repository, pizza) if pizza is not None else None
            if doc is None:
                self._remove(pizza_id)
            else:
                documents.append(doc)
        self._upsert(documents)

    def search(self, query: str, limit: int = 3) -> list[tuple[int, float]]:
        """Return `(pizza_id, cosine_distance)` pairs ordered by relevance (0 = identical)."""
        rows = self._db.vector_search(query, limit=limit) or []
        results = []
        for row in rows:
            payload = json.loads(row["payload"])
            results.append((int(payload["meta_data"]["pizza_id"]), float(row.get("_distance", 1.0))))
        return results

    # --- Internals ---------------------------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """Create the LanceDB table when it does not exist yet."""
        if not self._db.exists():
            self._db.create()

    def _upsert(self, documents: list[Document]) -> None:
        """Embed and store each document under its own content hash (embedding happens before the old row goes)."""
        for doc in documents:
            assert doc.id is not None
            self._db.upsert(content_hash=doc.id, documents=[doc])

    def _remove(self, pizza_id: int) -> None:
        """Delete the document of a pizza, if present."""
        for row in self._stored_rows():
            if row["content_hash"] == document_id(pizza_id):
                self._db.delete_by_id(row["row_id"])

    def _stored_rows(self) -> list[dict[str, Any]]:
        """Return every stored row with its row id, content hash, content and metadata."""
        table = self._db.table
        count = self._db.get_count() if table is not None else 0
        if table is None or count == 0:
            return []
        rows = []
        for row in table.search().select(["id", "payload"]).limit(count).to_list():
            payload = json.loads(row["payload"])
            rows.append(
                {
                    "row_id": row["id"],
                    "content_hash": str(payload.get("content_hash")),
                    "content": payload.get("content"),
                    "meta_data": payload.get("meta_data"),
                }
            )
        return rows
