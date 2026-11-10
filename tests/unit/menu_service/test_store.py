import sqlite3
from pathlib import Path

import pytest

from menu_service import store


def count(db: Path, sql: str, *args: object) -> int:
    """Run a COUNT query against the database."""
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def test_insert_and_update_pizza(writable_menu_db: Path) -> None:
    """A pizza without id is created; with id it is partially updated; both refresh that pizza."""
    with store.transaction(writable_menu_db) as conn:
        created = store.upsert(
            conn, "pizzas", {"sabor": "Portuguesa", "descricao": "Clássica.", "ingredientes": "Ovo."}
        )
    assert created.action == "created"
    new_id = created.item["id"]
    assert created.affected_pizza_ids == {new_id}

    with store.transaction(writable_menu_db) as conn:
        updated = store.upsert(conn, "pizzas", {"id": new_id, "descricao": "Com ovos e presunto."})
    assert updated.action == "updated"
    assert updated.item == {
        "id": new_id,
        "sabor": "Portuguesa",
        "descricao": "Com ovos e presunto.",
        "ingredientes": "Ovo.",
    }


def test_rename_size_refreshes_pizzas_using_it(writable_menu_db: Path) -> None:
    """Renaming a size refreshes every pizza with a price in that size; a new size refreshes none."""
    with store.transaction(writable_menu_db) as conn:
        renamed = store.upsert(conn, "tamanhos", {"id": 1, "tamanho": "Broto"})
        added = store.upsert(conn, "bordas", {"tipo": "Recheada com Chocolate"})
    assert renamed.affected_pizza_ids == {1, 2, 3, 4, 5, 6}
    assert added.action == "created" and added.affected_pizza_ids == set()


def test_price_upsert(writable_menu_db: Path) -> None:
    """A price is created for a new key and updated for an existing one."""
    key = {"pizza_id": 6, "tamanho_id": 3, "borda_id": 2}
    with store.transaction(writable_menu_db) as conn:
        created = store.upsert(conn, "precos", {**key, "preco": "55.50"})
        updated = store.upsert(conn, "precos", {**key, "preco": "57.00"})
    assert created.action == "created" and updated.action == "updated"
    assert updated.item == {**key, "preco": 57.0}
    assert updated.affected_pizza_ids == {6}


def test_price_requires_existing_references(writable_menu_db: Path) -> None:
    """A price for an unknown pizza, size or crust is rejected, listing every missing reference."""
    with store.transaction(writable_menu_db) as conn, pytest.raises(store.ReferenceNotFoundError) as exc:
        store.upsert(conn, "precos", {"pizza_id": 99, "tamanho_id": 1, "borda_id": 42, "preco": "10.00"})
    assert exc.value.missing == {"pizza_id": 99, "borda_id": 42}
    assert count(writable_menu_db, "SELECT COUNT(*) FROM precos") == 38


def test_duplicate_and_not_found(writable_menu_db: Path) -> None:
    """UNIQUE names conflict; updating or deleting a missing row is not found."""
    with store.transaction(writable_menu_db) as conn, pytest.raises(store.DuplicateValueError):
        store.upsert(conn, "tamanhos", {"tamanho": "Grande"})
    with store.transaction(writable_menu_db) as conn, pytest.raises(store.ItemNotFoundError):
        store.upsert(conn, "bordas", {"id": 99, "tipo": "Nova"})
    with store.transaction(writable_menu_db) as conn, pytest.raises(store.ItemNotFoundError):
        store.delete(conn, "precos", {"pizza_id": 6, "tamanho_id": 3, "borda_id": 3})


@pytest.mark.parametrize(
    ("table", "item_id", "column", "affected"),
    [
        ("pizzas", 1, "pizza_id", {1}),
        ("tamanhos", 1, "tamanho_id", {1, 2, 3, 4, 5, 6}),  # Pequena
        ("bordas", 3, "borda_id", {1, 2, 3, 4, 5}),  # Catupiry: the sweet pizza has none
    ],
)
def test_delete_cascades_to_prices(
    writable_menu_db: Path, table: store.EntityTable, item_id: int, column: str, affected: set[int]
) -> None:
    """Deleting a pizza, size or crust also deletes the prices that reference it."""
    referencing = count(writable_menu_db, f"SELECT COUNT(*) FROM precos WHERE {column} = ?", item_id)
    assert referencing > 0
    with store.transaction(writable_menu_db) as conn:
        result = store.delete(conn, table, item_id)
    assert result.action == "deleted" and result.item["id"] == item_id
    assert result.cascaded_prices == referencing
    assert result.affected_pizza_ids == affected
    assert count(writable_menu_db, f"SELECT COUNT(*) FROM precos WHERE {column} = ?", item_id) == 0
    assert count(writable_menu_db, f"SELECT COUNT(*) FROM {table} WHERE id = ?", item_id) == 0


def test_delete_price(writable_menu_db: Path) -> None:
    """A single price is deleted by its composite key."""
    key = {"pizza_id": 1, "tamanho_id": 1, "borda_id": 1}
    with store.transaction(writable_menu_db) as conn:
        result = store.delete(conn, "precos", key)
    assert result.item == {**key, "preco": 25.0}
    assert result.affected_pizza_ids == {1}
    assert count(writable_menu_db, "SELECT COUNT(*) FROM precos") == 37


def test_transaction_rolls_back_on_error(writable_menu_db: Path) -> None:
    """A failure inside the block leaves the database unchanged."""
    with pytest.raises(RuntimeError), store.transaction(writable_menu_db) as conn:
        store.delete(conn, "pizzas", 1)
        raise RuntimeError("index down")
    assert count(writable_menu_db, "SELECT COUNT(*) FROM pizzas WHERE id = 1") == 1
    assert count(writable_menu_db, "SELECT COUNT(*) FROM precos") == 38
