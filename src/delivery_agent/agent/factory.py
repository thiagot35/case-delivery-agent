"""Build the Agno agents: the order agent (plan.md §3.2) and the guardrail helpers (specs/003-guardrails)."""

import hashlib

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.base import Model
from agno.models.openai import OpenAIResponses

from delivery_agent.agent.instructions import DESCRIPTION, INSTRUCTIONS
from delivery_agent.config import Settings
from delivery_agent.guardrails.rewriter import REWRITER_INSTRUCTIONS
from delivery_agent.guardrails.router import ROUTER_INSTRUCTIONS, IntentClassification
from delivery_agent.logging_config import is_debug
from delivery_agent.tools.menu_tools import MenuTools
from delivery_agent.tools.order_tools import OrderTools

AGENT_ID = "beauty-pizza-attendant"
ROUTER_ID = "intent-router"
REWRITER_ID = "reply-rewriter"


def prompts_version() -> str:
    """Short hash of every agent prompt (order agent, router, rewriter): tells traces apart across prompt changes."""
    text = "\n".join([DESCRIPTION, *INSTRUCTIONS, *ROUTER_INSTRUCTIONS, *REWRITER_INSTRUCTIONS])
    return f"prompts-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:10]}"


def build_model(settings: Settings) -> Model:
    """Create the order agent's OpenAI Responses model; requests are chained to the previous response."""
    # For reasoning models (gpt-5*, gpt-6*) Agno chains each request to the previous OpenAI response
    # (previous_response_id) and sends only the new messages. Kept on purpose for cost (specs/007-short-replies):
    # OpenAI serves the conversation from its cache, while replaying the context changes the system prompt (time,
    # session state) on every run and loses the cache. Trade-off: the history limits (num_history_runs,
    # max_tool_calls_from_history) and the refreshed session state don't reach the model across runs.
    return OpenAIResponses(id=settings.model_id, api_key=settings.openai_api_key)


def build_router_model(settings: Settings) -> Model:
    """Model of the intent router and the rewriter (`ROUTER_MODEL_ID`, defaulting to `MODEL_ID`)."""
    return OpenAIResponses(id=settings.router_model_id or settings.model_id, api_key=settings.openai_api_key)


def build_agent(
    settings: Settings,
    db: BaseDb,
    menu_tools: MenuTools,
    order_tools: OrderTools,
    model: Model | None = None,
) -> Agent:
    """Build the Beauty Pizza order agent with its storage, instructions and toolkits."""
    return Agent(
        id=AGENT_ID,
        name="Beauty Pizza Attendant",
        model=model or build_model(settings),
        db=db,
        description=DESCRIPTION,
        instructions=INSTRUCTIONS,
        tools=[menu_tools, order_tools],
        # Bounded tool loop: a run stops calling tools after this many calls and answers with what it has.
        tool_call_limit=settings.agent_tool_call_limit,
        # Session state is mutated only by tools; the model sees it to know the current step.
        add_session_state_to_context=True,
        add_history_to_context=True,
        num_history_runs=settings.num_history_runs,
        # Older tool results are dropped from the history (fewer tokens); the current run's tool loop keeps them all.
        max_tool_calls_from_history=settings.agent_max_tool_calls_from_history,
        add_datetime_to_context=True,
        timezone_identifier=settings.timezone,
        markdown=False,
        telemetry=False,
        # Agno's native trace: prompts sent to the model, tool calls (name, arguments, result, duration),
        # run start/end and token metrics. Routed to the app's log handler (CPF-masked) by logging_config.
        debug_mode=is_debug(settings.log_level),
    )


def build_router_agent(settings: Settings, model: Model | None = None) -> Agent:
    """Build the intent classifier: no tools, no storage, structured output."""
    return Agent(
        id=ROUTER_ID,
        name="Intent Router",
        model=model or build_router_model(settings),
        instructions=ROUTER_INSTRUCTIONS,
        output_schema=IntentClassification,
        markdown=False,
        telemetry=False,
        debug_mode=is_debug(settings.log_level),
    )


def build_rewriter_agent(settings: Settings, model: Model | None = None) -> Agent:
    """Build the reply rewriter used for the output guardrail retry: no tools, no storage."""
    return Agent(
        id=REWRITER_ID,
        name="Reply Rewriter",
        model=model or build_router_model(settings),
        instructions=REWRITER_INSTRUCTIONS,
        markdown=False,
        telemetry=False,
        debug_mode=is_debug(settings.log_level),
    )
