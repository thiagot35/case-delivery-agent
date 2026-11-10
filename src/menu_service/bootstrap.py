"""Seed `menu.db` from the knowledge-base SQL script on first start."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_menu_db(sql_path: Path, db_path: Path) -> bool:
    """Create `db_path` from `sql_path` only when the database file is missing; return True when it was created.

    After the first start `menu.db` is the source of truth (it is edited through the API), so an existing database is
    never rebuilt from the SQL script.
    """
    if db_path.exists():
        return False
    if not sql_path.exists():
        raise FileNotFoundError(f"Knowledge base SQL not found: {sql_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    script = sql_path.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()
    logger.info("Menu database seeded at %s from %s", db_path, sql_path)
    return True
