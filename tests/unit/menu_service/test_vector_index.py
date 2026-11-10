import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

from agno.knowledge.embedder.base import Embedder

from menu_service import store
from menu_service.repository import MenuRepository
from menu_service.resolver import normalize
from menu_service.vector_index import MenuVectorIndex, build_documents

DIMENSIONS = 128


@dataclass
class HashingEmbedder(Embedder):
    """Deterministic bag-of-words embedder (no network) for testing the LanceDB integration."""

    dimensions: int = DIMENSIONS

    def get_embedding(self, text: str) -> list[float]:
        """Embed text as a normalized bag of hashed tokens (deterministic, offline)."""
        vector = [0.0] * DIMENSIONS
        for token in re.findall(r"[a-z]{3,}", normalize(text)):
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def get_embedding_and_usage(self, text: str):  # type: ignore[override]
        """Return the embedding with no usage data."""
        return self.get_embedding(text), None

    async def async_get_embedding(self, text: str) -> list[float]:
        """Async variant of get_embedding."""
        return self.get_embedding(text)

    async def async_get_embedding_and_usage(self, text: str):  # type: ignore[override]
        """Async variant of get_embedding_and_usage."""
        return self.get_embedding(text), None


def test_build_documents(repository: MenuRepository) -> None:
    """One chunk per pizza with flavor, ingredients, availability, pizza_id and the price range."""
    documents = build_documents(repository)
    assert len(documents) == 6
    assert len({d.id for d in documents}) == 6
    sweet = next(d for d in documents if d.meta_data["flavor"] == "Doce de Leite com Coco")
    assert "Bordas: Tradicional." in sweet.content
    assert "A partir de R$ 25,00" in sweet.content
    margherita = next(d for d in documents if d.meta_data["flavor"] == "Margherita")
    assert margherita.id == "pizza-1"
    assert margherita.meta_data == {"pizza_id": 1, "flavor": "Margherita", "min_price": 25.0, "max_price": 49.0}


def test_unpriced_pizza_is_not_indexed(writable_menu_db: Path) -> None:
    """A pizza without prices gets no document."""
    with store.transaction(writable_menu_db) as conn:
        store.upsert(conn, "pizzas", {"sabor": "Portuguesa", "descricao": "Clássica.", "ingredientes": "Ovo."})
    assert len(build_documents(MenuRepository.from_path(writable_menu_db))) == 6


def stored(index: MenuVectorIndex) -> dict[str, dict]:
    """Stored rows of the index keyed by content hash."""
    return {row["content_hash"]: row for row in index._stored_rows()}


def test_sync_search_and_refresh(tmp_path: Path, writable_menu_db: Path) -> None:
    """sync builds one row per pizza, refresh re-embeds/removes single pizzas, and nothing is duplicated."""
    index = MenuVectorIndex(tmp_path / "lancedb", HashingEmbedder())
    assert not index.ready
    repository = MenuRepository.from_path(writable_menu_db)

    index.sync(repository)
    assert index.ready
    assert set(stored(index)) == {f"pizza-{i}" for i in range(1, 7)}

    results = index.search("gorgonzola parmesão provolone", limit=2)
    assert len(results) == 2
    assert results[0][0] == 3  # Quatro Queijos
    assert 0 <= results[0][1] < results[1][1]

    index.sync(repository)  # unchanged menu: no duplicates
    assert len(index._stored_rows()) == 6

    with store.transaction(writable_menu_db) as conn:
        store.upsert(conn, "precos", {"pizza_id": 4, "tamanho_id": 3, "borda_id": 3, "preco": "99.90"})
        store.delete(conn, "pizzas", 6)
    changed = MenuRepository.from_path(writable_menu_db)
    index.refresh(changed, {4, 6})
    rows = stored(index)
    assert "pizza-6" not in rows
    assert rows["pizza-4"]["meta_data"]["max_price"] == 99.9
    assert len(rows) == 5


def test_sync_removes_rows_of_older_index_versions(tmp_path: Path, repository: MenuRepository) -> None:
    """Rows stored under another content hash (the old SQL-hash scheme) are deleted by sync."""
    index = MenuVectorIndex(tmp_path / "lancedb", HashingEmbedder())
    index._db.create()
    index._db.insert(content_hash="old-sql-hash", documents=build_documents(repository))
    assert len(index._stored_rows()) == 6

    index.sync(repository)
    assert set(stored(index)) == {f"pizza-{i}" for i in range(1, 7)}
