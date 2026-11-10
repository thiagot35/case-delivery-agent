import time
from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.session.agent import AgentSession

from delivery_agent.agent.factory import AGENT_ID
from delivery_agent.agent.session_resolver import SessionResolver

# CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person).
CPF = "52998224725"


def save_session(db: SqliteDb, session_id: str, updated_at: int, user_id: str = CPF) -> None:
    """Store a session for the customer with a chosen `updated_at`."""
    db.upsert_sessions(
        [
            AgentSession(
                session_id=session_id,
                agent_id=AGENT_ID,
                user_id=user_id,
                session_data={"session_state": {}},
                created_at=updated_at,
                updated_at=updated_at,
            )
        ],
        preserve_updated_at=True,
    )


def make_db(tmp_path: Path) -> SqliteDb:
    """Agno SQLite storage in a temporary file."""
    return SqliteDb(db_file=str(tmp_path / "agent.db"))


def test_new_session_when_none_exists(tmp_path: Path) -> None:
    """A customer without sessions gets a new session id."""
    session_id, is_new = SessionResolver(make_db(tmp_path), 5, AGENT_ID).resolve(CPF)
    assert is_new and session_id


def test_reuses_active_session(tmp_path: Path) -> None:
    """The latest session is reused while within the timeout."""
    db = make_db(tmp_path)
    now = int(time.time())
    save_session(db, "old", now - 3600)
    save_session(db, "recent", now - 60)
    resolver = SessionResolver(db, 5, AGENT_ID, clock=lambda: now)
    assert resolver.resolve(CPF) == ("recent", False)


def test_expired_session_starts_new(tmp_path: Path) -> None:
    """A session idle beyond the timeout is replaced by a new one."""
    db = make_db(tmp_path)
    now = int(time.time())
    save_session(db, "expired", now - 6 * 60)
    session_id, is_new = SessionResolver(db, 5, AGENT_ID, clock=lambda: now).resolve(CPF)
    assert is_new and session_id != "expired"


def test_sessions_are_per_customer(tmp_path: Path) -> None:
    """Another customer's active session is never reused."""
    db = make_db(tmp_path)
    now = int(time.time())
    save_session(db, "other-customer", now, user_id="11144477735")
    _, is_new = SessionResolver(db, 5, AGENT_ID, clock=lambda: now).resolve(CPF)
    assert is_new
