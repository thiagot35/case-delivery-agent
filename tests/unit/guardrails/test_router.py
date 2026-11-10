from types import SimpleNamespace
from typing import Any

import pytest
from agno.run.base import RunStatus

from delivery_agent.agent.runner import AgentRunner
from delivery_agent.guardrails.router import IntentClassification, IntentRouter, build_router_input, state_check


class ScriptedRunner(AgentRunner):
    """`AgentRunner` for tests: returns a fixed content or raises."""

    def __init__(self, content: Any = None, error: Exception | None = None, status: RunStatus = RunStatus.completed):
        """Return `content` (with `status`) from every run, or raise `error`."""
        self.content = content
        self.error = error
        self.status = status
        self.inputs: list[str] = []
        self.kwargs: list[dict[str, Any]] = []

    async def arun(self, input: str, **kwargs: Any) -> SimpleNamespace:
        """Record the input and return the scripted output."""
        self.inputs.append(input)
        self.kwargs.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content, status=self.status)


@pytest.mark.parametrize("message", ["Sim", "não", "ok!", "2", "Rua das Flores, 100", "só isso"])
async def test_state_check_skips_the_model(message: str) -> None:
    """Short replies in an active flow are routed to delivery without calling the model."""
    runner = ScriptedRunner(IntentClassification(intent="not_delivery"))
    decision = await IntentRouter(runner).classify(message, "building", "Deseja mais alguma coisa?")
    assert (decision.intent, decision.method) == ("delivery", "state")
    assert runner.inputs == []


def test_state_check_inactive_at_start() -> None:
    """At the start of a conversation even short replies go to the model."""
    assert state_check("sim", "start") is None
    assert state_check("sim", None) is None
    assert state_check("qual a capital da França?", "building") is None


@pytest.mark.parametrize("intent", ["delivery", "not_delivery", "call_human", "greeting"])
async def test_model_intents(intent: str) -> None:
    """The structured output (model instance or dict) is returned as the decision."""
    for content in (IntentClassification(intent=intent), {"intent": intent, "reason": "x"}):  # type: ignore[arg-type]
        decision = await IntentRouter(ScriptedRunner(content)).classify("mensagem", "start", None, True)
        assert (decision.intent, decision.method) == (intent, "llm")


@pytest.mark.parametrize(
    "runner",
    [
        ScriptedRunner(error=RuntimeError("boom")),
        ScriptedRunner("texto livre"),
        ScriptedRunner("Error code: 500 - provider down", status=RunStatus.error),
    ],
)
async def test_failures_route_to_delivery(runner: ScriptedRunner) -> None:
    """A router error or an invalid output fails open to delivery."""
    decision = await IntentRouter(runner).classify("mensagem", "start", None)
    assert (decision.intent, decision.method) == ("delivery", "fallback")


def test_router_input_has_context() -> None:
    """The prompt carries the step, the last reply and the message."""
    prompt = build_router_input("quero falar com alguém", "review", "Está tudo certo?")
    assert "review" in prompt and "Está tudo certo?" in prompt and "quero falar com alguém" in prompt


def test_router_input_has_first_message_flag() -> None:
    """The prompt tells the model whether the message is the customer's first of the day."""
    assert "Primeira mensagem do cliente hoje: sim" in build_router_input("Oi", None, None, True)
    assert "Primeira mensagem do cliente hoje: não" in build_router_input("Oi", None, None, False)


async def test_greeting_outside_first_message_becomes_delivery() -> None:
    """A greeting that is not the first message of the day is routed to delivery."""
    runner = ScriptedRunner(IntentClassification(intent="greeting"))
    decision = await IntentRouter(runner).classify("Bom dia", "start", "Olá!", False)
    assert (decision.intent, decision.method) == ("delivery", "llm")
    assert "Primeira mensagem do cliente hoje: não" in runner.inputs[0]


async def test_run_error_is_reported_as_router_error() -> None:
    """A run with status=error falls back with the router-error reason, not as invalid output."""
    runner = ScriptedRunner(IntentClassification(intent="not_delivery"), status=RunStatus.error)
    decision = await IntentRouter(runner).classify("mensagem", "start", None, True)
    assert (decision.intent, decision.method, decision.reason) == ("delivery", "fallback", "router error")


async def test_router_run_gets_the_conversation_session() -> None:
    """The classification run carries the conversation's session id (else Agno invents one, splitting the traces)."""
    runner = ScriptedRunner(IntentClassification(intent="delivery"))
    await IntentRouter(runner).classify("quero pizza", "start", None, True, session_id="session-1")
    assert runner.kwargs == [{"session_id": "session-1"}]
