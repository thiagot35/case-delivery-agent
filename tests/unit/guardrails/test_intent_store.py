import json
from datetime import date
from pathlib import Path

from delivery_agent.guardrails.intent_store import IntentLogEntry, IntentStore

DAY = date(2026, 11, 8)


# CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person).
def entry(user_id: str = "52998224725", day: date = DAY, **overrides: object) -> IntentLogEntry:
    """A routed message of `user_id` on `day`."""
    values: dict[str, object] = {
        "local_date": day,
        "user_id": user_id,
        "session_id": "s-1",
        "new_session": True,
        "message": "meu cartão é [cartão removido]",
        "intent": "greeting",
        "method": "llm",
        "reason": "cumprimento",
        "route": "greeting",
        "first_message_of_day": True,
        "customer_type": "new",
        "pii_found": ("CREDIT_CARD",),
        "latency_ms": 12,
    }
    values.update(overrides)
    return IntentLogEntry(**values)  # type: ignore[arg-type]


def test_first_message_of_the_day(tmp_path: Path) -> None:
    """A customer has a message on a day only after one is recorded, per customer and per day."""
    store = IntentStore(tmp_path / "intent.db")
    assert store.has_message_on("52998224725", DAY) is False
    store.record(entry())
    assert store.has_message_on("52998224725", DAY) is True
    assert store.has_message_on("52998224725", date(2026, 11, 9)) is False
    assert store.has_message_on("11144477735", DAY) is False


def test_entry_is_stored(tmp_path: Path) -> None:
    """Every field is stored; the message is the redacted text."""
    store = IntentStore(tmp_path / "intent.db")
    store.record(entry())
    store.record(entry(intent=None, method="input_filter", route="blocked", first_message_of_day=False))
    rows = store.list_entries("52998224725")
    assert len(rows) == 2
    first = rows[0]
    assert first["message"] == "meu cartão é [cartão removido]"
    assert (first["intent"], first["route"], first["first_message_of_day"]) == ("greeting", "greeting", 1)
    assert first["local_date"] == "2026-11-08" and first["created_at"]
    assert json.loads(str(first["pii_found"])) == ["CREDIT_CARD"]
    assert rows[1]["intent"] is None and rows[1]["method"] == "input_filter"
    assert store.list_entries("11144477735") == []
