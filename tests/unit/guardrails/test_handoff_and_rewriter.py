from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agno.run.base import RunStatus

from delivery_agent.agent.runner import AgentRunner
from delivery_agent.guardrails.handoff_store import HandoffStore, HandoffTicket
from delivery_agent.guardrails.rewriter import ReplyRewriter, build_critique
from delivery_agent.guardrails.terms import Violation

TICKET = HandoffTicket(
    session_id="s-1",
    client_name="Maria Silva",
    # CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person).
    client_document="52998224725",
    order_id=7,
    request_count=3,
    last_message="quero falar com um atendente",
)


def test_handoff_one_ticket_per_session(tmp_path: Path) -> None:
    """The first ticket of a session is stored; a repeated request does not duplicate it."""
    store = HandoffStore(tmp_path / "human_attendant.db")
    assert store.record(TICKET) is True
    assert store.record(TICKET) is False
    tickets = store.list_tickets()
    assert len(tickets) == 1
    assert tickets[0]["session_id"] == "s-1" and tickets[0]["order_id"] == 7 and tickets[0]["request_count"] == 3


class EchoRunner(AgentRunner):
    """`AgentRunner` for tests: records the prompt and returns a fixed rewrite."""

    def __init__(self) -> None:
        """Start with no recorded prompts."""
        self.inputs: list[str] = []
        self.kwargs: list[dict[str, Any]] = []

    async def arun(self, input: str, **kwargs: Any) -> SimpleNamespace:
        """Record the prompt and return a clean reply."""
        self.inputs.append(input)
        self.kwargs.append(kwargs)
        return SimpleNamespace(content="  Resposta sem termos proibidos.  ")


async def test_rewriter_sends_critique() -> None:
    """The rewriter sends the draft with the violations and returns the trimmed rewrite."""
    runner = EchoRunner()
    violations = [Violation("competitors", "pizza hut")]
    result = await ReplyRewriter(runner).rewrite("Peça na Pizza Hut.", violations)
    assert result == "Resposta sem termos proibidos."
    assert runner.inputs == [build_critique("Peça na Pizza Hut.", violations)]
    assert "concorrente: 'pizza hut'" in runner.inputs[0]


async def test_rewriter_run_error_returns_empty() -> None:
    """A failed rewriter run never returns its error text as the reply."""

    class _FailedRunner(AgentRunner):
        async def arun(self, input: str, **kwargs: Any) -> SimpleNamespace:
            """Return a failed run whose content is the provider error."""
            return SimpleNamespace(content="Error code: 429 - rate limit", status=RunStatus.error)

    assert await ReplyRewriter(_FailedRunner()).rewrite("Peça na Pizza Hut.", [Violation("competitors", "x")]) == ""


async def test_rewriter_run_gets_the_conversation_session() -> None:
    """The rewrite run carries the conversation's session id, like the router's."""
    runner = EchoRunner()
    await ReplyRewriter(runner).rewrite("Peça na Pizza Hut.", [Violation("competitors", "x")], session_id="session-1")
    assert runner.kwargs == [{"session_id": "session-1"}]
