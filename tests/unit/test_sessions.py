"""`AgnoSessionAccess` against a real Agno agent and SQLite storage (no model call)."""

import time
import uuid
from pathlib import Path

import pytest
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.message import Message
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.session._utils import resolve_run_index
from agno.session.agent import AgentSession

from delivery_agent.agent.factory import AGENT_ID, build_agent
from delivery_agent.agent.session_resolver import SessionResolver
from delivery_agent.agent.sessions import AgnoSessionAccess
from delivery_agent.config import Settings
from delivery_agent.tools.menu_tools import MenuTools
from delivery_agent.tools.order_tools import OrderTools

# CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person).
CPF = "52998224725"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path of a fresh agent database."""
    return tmp_path / "agent.db"


def make_agent(db_path: Path) -> Agent:
    """Build the real order agent over `db_path` (a new instance reads only what was persisted)."""
    settings = Settings(openai_api_key="test")
    return build_agent(settings, SqliteDb(db_file=str(db_path)), MenuTools(None), OrderTools(None, None))  # type: ignore[arg-type]


def seed_agent_run(agent: Agent, session_id: str, reply: str) -> None:
    """Store a session with one agent run, the way Agno does after `arun`."""
    now = int(time.time())
    session = AgentSession(
        session_id=session_id,
        agent_id=AGENT_ID,
        user_id=CPF,
        session_data={"session_state": {"flow_step": "building"}},
        created_at=now,
    )
    run = RunOutput(
        run_id=str(uuid.uuid4()),
        agent_id=AGENT_ID,
        session_id=session_id,
        user_id=CPF,
        content=reply,
        status=RunStatus.completed,
        created_at=now,
        messages=[Message(role="user", content="oi"), Message(role="assistant", content=reply)],
    )
    session.upsert_run(run)
    agent.db.upsert_session(session)  # type: ignore[union-attr]
    agent.db.upsert_run(run, session_id=session_id, user_id=CPF, run_index=resolve_run_index(session, run))  # type: ignore[union-attr]


async def test_append_turn_creates_the_session_and_its_history(db_path: Path) -> None:
    """A static turn on a new session is persisted as a run the order agent and the resolver see."""
    await AgnoSessionAccess(make_agent(db_path)).append_turn(
        "s-1", CPF, "oi", "Olá, Maria!", {"greeted": True}, "greeting"
    )

    reader = make_agent(db_path)
    access = AgnoSessionAccess(reader)
    assert await access.last_reply("s-1") == "Olá, Maria!"
    state = await access.state("s-1")
    assert state["greeted"] is True and state["flow_step"] == "start"  # defaults filled for the new session
    session = await reader.aget_session(session_id="s-1")
    assert session is not None and session.runs is not None
    assert session.runs[-1].metadata == {"static_route": "greeting"}
    history = [(m.role, m.content) for m in session.get_messages(agent_id=AGENT_ID, skip_roles=["system"])]
    assert history == [("user", "oi"), ("assistant", "Olá, Maria!")]
    assert SessionResolver(reader.db, 5, agent_id=AGENT_ID).resolve(CPF) == ("s-1", False)  # type: ignore[arg-type]


async def test_append_turn_extends_an_existing_session(db_path: Path) -> None:
    """A static turn is appended after the agent's runs and merges its state updates."""
    agent = make_agent(db_path)
    seed_agent_run(agent, "s-1", "Deseja mais alguma coisa?")
    await AgnoSessionAccess(agent).append_turn(
        "s-1", CPF, "[mensagem bloqueada]", "Não posso ajudar com isso.", {}, "blocked"
    )

    session = await make_agent(db_path).aget_session(session_id="s-1")
    assert session is not None and session.runs is not None
    assert [run.content for run in session.runs] == ["Deseja mais alguma coisa?", "Não posso ajudar com isso."]
    assert session.session_data["session_state"]["flow_step"] == "building"  # type: ignore[index]


async def test_replace_last_reply_is_persisted(db_path: Path) -> None:
    """The rewritten reply is saved to the runs table, not only patched in memory (regression)."""
    agent = make_agent(db_path)
    seed_agent_run(agent, "s-1", "Peça na Pizza Hut.")
    await AgnoSessionAccess(agent).replace_last_reply("s-1", "Resposta revisada.")

    session = await make_agent(db_path).aget_session(session_id="s-1")
    assert session is not None and session.runs is not None
    run = session.runs[-1]
    assert run.content == "Resposta revisada."
    assert [(m.role, m.content) for m in run.messages or []] == [("user", "oi"), ("assistant", "Resposta revisada.")]


async def test_update_state_merges(db_path: Path) -> None:
    """State updates are merged into the stored session state."""
    agent = make_agent(db_path)
    seed_agent_run(agent, "s-1", "Oi")
    await AgnoSessionAccess(agent).update_state("s-1", {"human_requests": 3})
    state = await AgnoSessionAccess(make_agent(db_path)).state("s-1")
    assert state == {"flow_step": "building", "human_requests": 3}


async def test_missing_session(db_path: Path) -> None:
    """A session that doesn't exist yet reads as empty and updates are no-ops."""
    access = AgnoSessionAccess(make_agent(db_path))
    assert await access.state("s") == {}
    assert await access.last_reply("s") is None
    await access.update_state("s", {"human_requests": 1})
    await access.replace_last_reply("s", "x")
    assert await access.state("s") == {}
