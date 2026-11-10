"""Read and write the order agent's stored sessions outside an agent run (guardrail and greeting routes).

Agno keeps runs in their own table: `asave_session` writes only the session row (state), so every change to a run
is also written with `db.upsert_run`.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Protocol

from agno.models.message import Message
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.session._utils import resolve_run_index
from agno.session.agent import AgentSession

from delivery_agent.agent.state import ensure_defaults

logger = logging.getLogger(__name__)


class SessionAccess(Protocol):
    """Session operations the conversation service needs outside an agent run."""

    async def state(self, session_id: str) -> dict[str, Any]:
        """Return the stored session state (empty for a session that doesn't exist yet)."""

    async def last_reply(self, session_id: str) -> str | None:
        """Return the last reply the agent sent in the session, if any."""

    async def update_state(self, session_id: str, updates: dict[str, Any]) -> None:
        """Merge `updates` into the stored session state (no-op when the session doesn't exist)."""

    async def replace_last_reply(self, session_id: str, reply: str) -> None:
        """Replace the last stored reply, so the agent's history matches what the customer received."""

    async def append_turn(
        self,
        session_id: str,
        user_id: str,
        message: str,
        reply: str,
        state_updates: dict[str, Any],
        route: str,
    ) -> None:
        """Store a reply sent without the order agent as a turn of its history (creating the session if needed)."""


class AgnoSessionAccess(SessionAccess):
    """`SessionAccess` over the order agent (`agno.agent.Agent`) and its database."""

    def __init__(self, order_agent: Any):
        """Keep the order agent whose sessions are read and written."""
        self._order_agent = order_agent

    async def state(self, session_id: str) -> dict[str, Any]:
        """Return the stored session state (empty for a session that doesn't exist yet)."""
        session = await self._order_agent.aget_session(session_id=session_id)
        if session is None or not session.session_data:
            return {}
        return dict(session.session_data.get("session_state") or {})

    async def last_reply(self, session_id: str) -> str | None:
        """Return the content of the last run of the session, if any."""
        run = await self._order_agent.aget_last_run_output(session_id=session_id)
        content = getattr(run, "content", None)
        return str(content) if content else None

    async def update_state(self, session_id: str, updates: dict[str, Any]) -> None:
        """Merge `updates` into the stored session state (no-op when the session doesn't exist)."""
        session = await self._order_agent.aget_session(session_id=session_id)
        if session is None:
            return
        session.session_data = session.session_data or {}
        session.session_data.setdefault("session_state", {}).update(updates)
        await self._order_agent.asave_session(session)

    async def replace_last_reply(self, session_id: str, reply: str) -> None:
        """Replace the last run's content and its final assistant message, and persist the run."""
        session = await self._order_agent.aget_session(session_id=session_id)
        if session is None or not session.runs:
            return
        run = session.runs[-1]
        run.content = reply
        for message in reversed(run.messages or []):
            if message.role == "assistant" and message.content:
                message.content = reply
                break
        await self._save_run(session, run)

    async def append_turn(
        self,
        session_id: str,
        user_id: str,
        message: str,
        reply: str,
        state_updates: dict[str, Any],
        route: str,
    ) -> None:
        """Store a reply sent without the order agent as a completed run, so the next agent turns see it."""
        now = int(time.time())
        session = await self._order_agent.aget_session(session_id=session_id)
        if session is None:
            session = AgentSession(
                session_id=session_id,
                agent_id=self._order_agent.id,
                user_id=user_id,
                session_data={"session_state": ensure_defaults({})},
                created_at=now,
            )
        session.session_data = session.session_data or {}
        session.session_data.setdefault("session_state", {}).update(state_updates)
        run = RunOutput(
            run_id=str(uuid.uuid4()),
            agent_id=self._order_agent.id,
            session_id=session_id,
            user_id=user_id,
            content=reply,
            status=RunStatus.completed,
            created_at=now,
            metadata={"static_route": route},
            messages=[
                Message(role="user", content=message, created_at=now),
                Message(role="assistant", content=reply, created_at=now),
            ],
        )
        session.upsert_run(run)
        await self._order_agent.asave_session(session)
        await self._save_run(session, run)
        logger.debug("static turn stored route=%s session=%s", route, session_id)

    async def _save_run(self, session: AgentSession, run: RunOutput) -> None:
        """Write one run to the runs table (the session row doesn't carry the runs)."""
        db = self._order_agent.db
        await asyncio.to_thread(
            db.upsert_run,
            run,
            session_id=session.session_id,
            user_id=session.user_id,
            run_index=resolve_run_index(session, run),
        )
