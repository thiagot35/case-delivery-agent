"""Transactional writes to `menu.db`: upsert and delete per table, reference checks and the `precos` cascade.

Every function takes an open connection so the caller (`MenuService`) controls the transaction: it re-embeds the
affected pizzas before committing and rolls back when that fails.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

Table = Literal["pizzas", "tamanhos", "bordas", "precos"]
EntityTable = Literal["pizzas", "tamanhos", "bordas"]

# Writable columns of the tables keyed by `id`.
ENTITY_COLUMNS: dict[str, tuple[str, ...]] = {
    "pizzas": ("sabor", "descricao", "ingredientes"),
    "tamanhos": ("tamanho",),
    "bordas": ("tipo",),
}
# Column of `precos` that references each entity table.
PRICE_FOREIGN_KEYS: dict[str, str] = {"pizzas": "pizza_id", "tamanhos": "tamanho_id", "bordas": "borda_id"}
PRICE_KEY_COLUMNS = ("pizza_id", "tamanho_id", "borda_id")


class MenuStoreError(Exception):
    """Base class for menu write failures."""


class ItemNotFoundError(MenuStoreError):
    """The row to update or delete does not exist."""

    def __init__(self, table: str, key: Any):
        """Keep the table and the key that was not found."""
        super().__init__(f"{table} item {key} not found")
        self.table = table
        self.key = key


class DuplicateValueError(MenuStoreError):
    """A UNIQUE column (`tamanhos.tamanho`, `bordas.tipo`) already holds the value."""

    def __init__(self, table: str, detail: str):
        """Keep the table and SQLite's constraint message."""
        super().__init__(f"duplicate value in {table}: {detail}")
        self.table = table
        self.detail = detail


class ReferenceNotFoundError(MenuStoreError):
    """A `precos` row references a pizza, size or crust that does not exist."""

    def __init__(self, missing: dict[str, int]):
        """Keep the missing references, e.g. `{"borda_id": 9}`."""
        super().__init__(f"referenced items not found: {missing}")
        self.missing = missing


@dataclass
class WriteResult:
    """What a write changed: the stored row and the pizzas whose vector document must be refreshed."""

    table: str
    action: Literal["created", "updated", "deleted"]
    item: dict[str, Any]
    affected_pizza_ids: set[int] = field(default_factory=set)
    cascaded_prices: int = 0


@contextmanager
def transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection with foreign keys on; commit when the block succeeds, roll back when it raises."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- Upsert ----------------------------------------------------------------------------------------------


def upsert(conn: sqlite3.Connection, table: Table, fields: dict[str, Any]) -> WriteResult:
    """Insert (no `id`) or update (with `id`) a row; `precos` is keyed by (pizza_id, tamanho_id, borda_id)."""
    if table == "precos":
        return _upsert_price(conn, fields)
    return _upsert_entity(conn, table, fields)


def _upsert_entity(conn: sqlite3.Connection, table: EntityTable, fields: dict[str, Any]) -> WriteResult:
    """Insert or partially update a `pizzas`, `tamanhos` or `bordas` row."""
    columns = ENTITY_COLUMNS[table]
    values = {column: fields[column] for column in columns if fields.get(column) is not None}
    item_id = fields.get("id")
    try:
        if item_id is None:
            placeholders = ", ".join("?" for _ in values)
            cursor = conn.execute(
                f"INSERT INTO {table} ({', '.join(values)}) VALUES ({placeholders})", tuple(values.values())
            )
            item_id, action = cursor.lastrowid, "created"
        else:
            if _fetch(conn, table, int(item_id)) is None:
                raise ItemNotFoundError(table, item_id)
            assignments = ", ".join(f"{column} = ?" for column in values)
            conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*values.values(), int(item_id)))
            action = "updated"
    except sqlite3.IntegrityError as exc:
        raise DuplicateValueError(table, str(exc)) from exc

    assert item_id is not None
    if table == "pizzas":
        affected = {int(item_id)}
    elif action == "created":
        affected = set()  # a new size/crust has no prices yet
    else:
        affected = _pizzas_referencing(conn, table, int(item_id))  # their documents list the renamed value
    return WriteResult(table, action, _fetch(conn, table, int(item_id)) or {}, affected)


def _upsert_price(conn: sqlite3.Connection, fields: dict[str, Any]) -> WriteResult:
    """Insert or update a `precos` row after checking that its pizza, size and crust exist."""
    key = {column: int(fields[column]) for column in PRICE_KEY_COLUMNS}
    missing = {
        column: value
        for table, column in PRICE_FOREIGN_KEYS.items()
        if _fetch(conn, table, (value := key[column])) is None
    }
    if missing:
        raise ReferenceNotFoundError(missing)

    price = float(Decimal(str(fields["preco"])))
    exists = _fetch_price(conn, key) is not None
    conn.execute(
        "INSERT INTO precos (pizza_id, tamanho_id, borda_id, preco) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (pizza_id, tamanho_id, borda_id) DO UPDATE SET preco = excluded.preco",
        (key["pizza_id"], key["tamanho_id"], key["borda_id"], price),
    )
    return WriteResult("precos", "updated" if exists else "created", _fetch_price(conn, key) or {}, {key["pizza_id"]})


# --- Delete ----------------------------------------------------------------------------------------------


def delete(conn: sqlite3.Connection, table: Table, key: int | dict[str, int]) -> WriteResult:
    """Delete a row; deleting a pizza, size or crust also deletes the `precos` rows that reference it."""
    if table == "precos":
        assert isinstance(key, dict)
        return _delete_price(conn, key)
    assert isinstance(key, int)
    return _delete_entity(conn, table, key)


def _delete_entity(conn: sqlite3.Connection, table: EntityTable, item_id: int) -> WriteResult:
    """Delete a `pizzas`, `tamanhos` or `bordas` row and cascade to `precos`."""
    row = _fetch(conn, table, item_id)
    if row is None:
        raise ItemNotFoundError(table, item_id)
    # Collected before the delete: pizzas that lose prices need their documents refreshed (or removed).
    affected = {item_id} if table == "pizzas" else _pizzas_referencing(conn, table, item_id)
    cascaded = conn.execute(f"DELETE FROM precos WHERE {PRICE_FOREIGN_KEYS[table]} = ?", (item_id,)).rowcount
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
    return WriteResult(table, "deleted", row, affected, cascaded_prices=cascaded)


def _delete_price(conn: sqlite3.Connection, key: dict[str, int]) -> WriteResult:
    """Delete one `precos` row identified by its composite key."""
    row = _fetch_price(conn, key)
    if row is None:
        raise ItemNotFoundError("precos", key)
    conn.execute(
        "DELETE FROM precos WHERE pizza_id = ? AND tamanho_id = ? AND borda_id = ?",
        (key["pizza_id"], key["tamanho_id"], key["borda_id"]),
    )
    return WriteResult("precos", "deleted", row, {key["pizza_id"]})


# --- Helpers ---------------------------------------------------------------------------------------------


def _fetch(conn: sqlite3.Connection, table: str, item_id: int) -> dict[str, Any] | None:
    """Return a row of an `id`-keyed table as a dict, or None."""
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row is not None else None


def _fetch_price(conn: sqlite3.Connection, key: dict[str, int]) -> dict[str, Any] | None:
    """Return a `precos` row as a dict, or None."""
    row = conn.execute(
        "SELECT * FROM precos WHERE pizza_id = ? AND tamanho_id = ? AND borda_id = ?",
        (key["pizza_id"], key["tamanho_id"], key["borda_id"]),
    ).fetchone()
    return dict(row) if row is not None else None


def _pizzas_referencing(conn: sqlite3.Connection, table: str, item_id: int) -> set[int]:
    """Return the ids of pizzas that have prices using the given size or crust."""
    column = PRICE_FOREIGN_KEYS[table]
    return {row[0] for row in conn.execute(f"SELECT DISTINCT pizza_id FROM precos WHERE {column} = ?", (item_id,))}
