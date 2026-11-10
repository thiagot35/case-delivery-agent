"""Active-session resolution based on the agent's own storage (research.md R-02).

The Order API `updated_at` is not refreshed by add-items/update-address, so it cannot tell whether a conversation
is active. Agno updates the session row on every run, so the latest session's `updated_at` is used instead.
"""

import logging
import time
import uuid
from collections.abc import Callable

from agno.db.base import BaseDb, SessionType

logger = logging.getLogger(__name__)


class SessionResolver:
    def __init__(
        self,
        db: BaseDb,
        timeout_minutes: int,
        agent_id: str | None = None,
        clock: Callable[[], float] = time.time,
    ):
        """Keep the Agno storage, the inactivity timeout and the clock used to resolve sessions."""
        self._db = db
        self._timeout_seconds = timeout_minutes * 60
        self._agent_id = agent_id
        self._clock = clock

    def resolve(self, user_id: str) -> tuple[str, bool]:
        """Return `(session_id, is_new)`: the latest session if still active, otherwise a new session id."""
        sessions = self._db.get_sessions(
            session_type=SessionType.AGENT,
            user_id=user_id,
            component_id=self._agent_id,
            sort_by="updated_at",
            sort_order="desc",
            limit=1,
            deserialize=True,
            include_runs=False,
        )
        latest = sessions[0] if isinstance(sessions, list) and sessions else None
        if latest is not None:
            last_activity = latest.updated_at or latest.created_at or 0
            idle_seconds = self._clock() - last_activity
            if idle_seconds <= self._timeout_seconds:
                logger.debug("session resumed session=%s idle_s=%.0f", latest.session_id, idle_seconds)
                return latest.session_id, False
            logger.debug(
                "session expired session=%s idle_s=%.0f timeout_s=%d",
                latest.session_id,
                idle_seconds,
                self._timeout_seconds,
            )
        else:
            logger.debug("no previous session for this customer")
        return str(uuid.uuid4()), True
