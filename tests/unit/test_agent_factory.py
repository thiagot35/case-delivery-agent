from typing import Any

from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIResponses

from delivery_agent.agent.factory import build_agent, build_model, build_rewriter_agent, build_router_agent
from delivery_agent.config import Settings
from delivery_agent.guardrails.router import IntentClassification
from delivery_agent.tools.menu_tools import MenuTools
from delivery_agent.tools.order_tools import OrderTools


def test_order_agent_has_bounded_tool_loop(tmp_path: Any) -> None:
    """The order agent stops after AGENT_TOOL_CALL_LIMIT (10) tool calls per run."""
    settings = Settings(_env_file=None, openai_api_key="test")  # type: ignore[call-arg]  # the default, not .env
    db = SqliteDb(db_file=str(tmp_path / "agent.db"))
    agent = build_agent(settings, db, MenuTools(None), OrderTools(None, None))  # type: ignore[arg-type]
    assert agent.tool_call_limit == 10


def test_order_agent_trims_tool_calls_from_history(tmp_path: Any) -> None:
    """Only the last AGENT_MAX_TOOL_CALLS_FROM_HISTORY tool calls of the history reach the model (007)."""
    assert Settings(_env_file=None).agent_max_tool_calls_from_history == 3  # type: ignore[call-arg]
    settings = Settings(_env_file=None, openai_api_key="test", agent_max_tool_calls_from_history=2)  # type: ignore[call-arg]
    db = SqliteDb(db_file=str(tmp_path / "agent.db"))
    agent = build_agent(settings, db, MenuTools(None), OrderTools(None, None))  # type: ignore[arg-type]
    assert agent.max_tool_calls_from_history == 2


def test_order_agent_model_chains_responses() -> None:
    """The order agent's model chains OpenAI responses, which keeps the conversation in OpenAI's cache (007)."""
    model = build_model(Settings(_env_file=None, openai_api_key="test"))  # type: ignore[call-arg]
    assert isinstance(model, OpenAIResponses)
    assert model._chains_responses()


def test_guardrail_agents_have_no_tools() -> None:
    """The router (structured output) and the rewriter have no tools and no storage."""
    settings = Settings(openai_api_key="test", router_model_id="router-model")
    router = build_router_agent(settings)
    rewriter = build_rewriter_agent(settings)
    assert router.output_schema is IntentClassification
    assert router.model.id == "router-model"  # type: ignore[union-attr]
    for agent in (router, rewriter):
        assert not agent.tools and agent.db is None
