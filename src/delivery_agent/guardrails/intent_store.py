"""Intent log: one row per customer message, in its own SQLite database (`data/intent.db`, specs/004-greeting).

It also answers "is this the customer's first message of the day?" for the `greeting` intent.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from delivery_agent.guardrails.protocols import IntentRecorder

SCHEMA = """
CREATE TABLE IF NOT EXISTS intent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    new_session INTEGER NOT NULL,
    message TEXT NOT NULL,
    intent TEXT,
    method TEXT NOT NULL,
    reason TEXT,
    route TEXT NOT NULL,
    first_message_of_day INTEGER NOT NULL,
    flow_step TEXT,
    customer_type TEXT,
    pii_found TEXT,
    human_requests INTEGER,
    latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS ix_intent_log_user_day ON intent_log (user_id, local_date);
"""


@dataclass(frozen=True)
class IntentLogEntry:
    """One routed customer message. `message` is the PII-redacted text; `intent` is None for blocked messages."""

    local_date: date
    user_id: str
    session_id: str
    new_session: bool
    message: str
    intent: str | None
    method: str
    reason: str
    route: str
    first_message_of_day: bool
    flow_step: str | None = None
    customer_type: str | None = None
    pii_found: tuple[str, ...] = ()
    human_requests: int = 0
    latency_ms: int | None = None


class IntentStore(IntentRecorder):
    """Writes and queries the intent log."""

    def __init__(self, db_path: Path):
        """Create the database file, table and index if needed."""
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def has_message_on(self, user_id: str, day: date) -> bool:
        """Tell whether the customer already sent a message on `day` (local date)."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM intent_log WHERE user_id = ? AND local_date = ? LIMIT 1", (user_id, day.isoformat())
            ).fetchone()
        return row is not None

    def record(self, entry: IntentLogEntry) -> None:
        """Insert the entry."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO intent_log (created_at, local_date, user_id, session_id, new_session, message, intent, "
                "method, reason, route, first_message_of_day, flow_step, customer_type, pii_found, human_requests, "
                "latency_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(),
                    entry.local_date.isoformat(),
                    entry.user_id,
                    entry.session_id,
                    int(entry.new_session),
                    entry.message,
                    entry.intent,
                    entry.method,
                    entry.reason,
                    entry.route,
                    int(entry.first_message_of_day),
                    entry.flow_step,
                    entry.customer_type,
                    json.dumps(sorted(entry.pii_found)),
                    entry.human_requests,
                    entry.latency_ms,
                ),
            )

    def list_entries(self, user_id: str | None = None) -> list[dict[str, object]]:
        """Return the log, oldest first, optionally for one customer (used by tests and support tooling)."""
        query, params = "SELECT * FROM intent_log", ()
        if user_id is not None:
            query, params = query + " WHERE user_id = ?", (user_id,)
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query + " ORDER BY id", params)]

    def _connect(self) -> sqlite3.Connection:
        """Open a connection to the database."""
        return sqlite3.connect(self._db_path)
