"""Human attendant requests, stored in their own SQLite database (`data/human_attendant.db`)."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from delivery_agent.guardrails.protocols import HandoffRecorder

SCHEMA = """
CREATE TABLE IF NOT EXISTS handoff_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    session_id TEXT NOT NULL UNIQUE,
    client_name TEXT NOT NULL,
    client_document TEXT NOT NULL,
    order_id INTEGER,
    request_count INTEGER NOT NULL,
    last_message TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class HandoffTicket:
    """A customer's request for a human attendant. `last_message` is the PII-redacted text."""

    session_id: str
    client_name: str
    client_document: str
    order_id: int | None
    request_count: int
    last_message: str


class HandoffStore(HandoffRecorder):
    """Writes one ticket per session to the human attendant database."""

    def __init__(self, db_path: Path):
        """Create the database file and table if needed."""
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(SCHEMA)

    def record(self, ticket: HandoffTicket) -> bool:
        """Insert the ticket; return False when the session already has one."""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO handoff_requests (created_at, session_id, client_name, client_document, "
                "order_id, request_count, last_message) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    ticket.session_id,
                    ticket.client_name,
                    ticket.client_document,
                    ticket.order_id,
                    ticket.request_count,
                    ticket.last_message,
                ),
            )
            return cursor.rowcount == 1

    def list_tickets(self) -> list[dict[str, object]]:
        """Return every ticket, oldest first (used by tests and support tooling)."""
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute("SELECT * FROM handoff_requests ORDER BY id")]

    def _connect(self) -> sqlite3.Connection:
        """Open a connection to the database."""
        return sqlite3.connect(self._db_path)
