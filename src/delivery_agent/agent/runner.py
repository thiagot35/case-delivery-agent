"""The minimal agent interface used by the conversation service, the intent router and the reply rewriter."""

from typing import Any, Protocol


class AgentRunner(Protocol):
    """What the callers need from an agent.

    Production passes `agno.agent.Agent`, which matches it structurally (a third-party class can't inherit it);
    test doubles inherit it explicitly.
    """

    async def arun(self, input: str, **kwargs: Any) -> Any:
        """Run the agent for one message (matches `agno.agent.Agent.arun`)."""
