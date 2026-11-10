"""FastAPI application: wiring (lifespan) and routes."""

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from agno.db.sqlite import SqliteDb
from fastapi import FastAPI, HTTPException, Request

from delivery_agent.agent.factory import (
    AGENT_ID,
    build_agent,
    build_rewriter_agent,
    build_router_agent,
    prompts_version,
)
from delivery_agent.agent.session_resolver import SessionResolver
from delivery_agent.api.schemas import HealthResponse, MessageRequest, MessageResponse
from delivery_agent.api.service import AgentUnavailableError, ConversationService, Guardrails
from delivery_agent.clients.menu_api_client import MenuApiClient
from delivery_agent.clients.order_api_client import OrderApiClient
from delivery_agent.config import Settings, get_settings
from delivery_agent.domain.cpf import mask_cpf
from delivery_agent.domain.special_dates import SpecialDateCalendar
from delivery_agent.guardrails.filters import InputFilter, OutputGuardrail
from delivery_agent.guardrails.greeting import GreetingResponder
from delivery_agent.guardrails.handoff_store import HandoffStore
from delivery_agent.guardrails.intent_store import IntentStore
from delivery_agent.guardrails.rewriter import ReplyRewriter
from delivery_agent.guardrails.router import IntentRouter
from delivery_agent.guardrails.terms import TermList, TermMatcher
from delivery_agent.logging_config import REQUEST_HIGHLIGHT
from delivery_agent.tools.menu_tools import MenuTools
from delivery_agent.tools.order_tools import OrderTools
from delivery_agent.tracing import setup_tracing

logger = logging.getLogger(__name__)


@dataclass
class AppComponents:
    service: ConversationService
    order_client: OrderApiClient | None = None
    menu_client: MenuApiClient | None = None


def build_guardrails(settings: Settings, order_client: OrderApiClient, menu_client: MenuApiClient) -> Guardrails:
    """Build the pipeline: Presidio redactor, term lists, router and rewriter agents, greeter and the stores."""
    from delivery_agent.guardrails.pii import PiiRedactor  # loads Presidio + spaCy: only when the app starts

    matcher = TermMatcher(TermList.load(settings.guardrail_terms_path))
    return Guardrails(
        input_filter=InputFilter(PiiRedactor(), matcher),
        router=IntentRouter(build_router_agent(settings)),
        output=OutputGuardrail(matcher),
        rewriter=ReplyRewriter(build_rewriter_agent(settings)),
        handoff=HandoffStore(settings.human_attendant_db_path),
        greeter=GreetingResponder(order_client, menu_client, random.Random()),
        intents=IntentStore(settings.intent_db_path),
        handoff_threshold=settings.human_handoff_threshold,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build every component once at startup (clients, agent) and close them on shutdown."""
    if getattr(app.state, "components", None) is not None:  # injected (tests)
        yield
        return

    settings: Settings = app.state.settings
    # Before any agent runs: the Agno instrumentation must be in place for the first message.
    tracer = setup_tracing(settings, version=prompts_version())
    if tracer.enabled and not await asyncio.to_thread(tracer.verify):
        logger.warning(
            "Langfuse rejected the credentials or is unreachable at %s: traces will be dropped",
            settings.langfuse_base_url,
        )
    menu_http = MenuApiClient.create_http_client(settings.menu_service_base_url, settings.menu_service_timeout_seconds)
    menu_client = MenuApiClient(menu_http)
    if not await menu_client.ping():
        logger.warning("Menu service not reachable at %s", settings.menu_service_base_url)

    order_http = OrderApiClient.create_http_client(settings.order_api_base_url, settings.order_api_timeout_seconds)
    order_client = OrderApiClient(order_http)
    if not await order_client.ping():
        logger.warning("Order API not reachable at %s", settings.order_api_base_url)

    settings.agent_db_path.parent.mkdir(parents=True, exist_ok=True)
    db = SqliteDb(db_file=str(settings.agent_db_path))
    agent = build_agent(settings, db, MenuTools(menu_client), OrderTools(order_client, menu_client))
    resolver = SessionResolver(db, settings.session_timeout_minutes, agent_id=AGENT_ID)
    app.state.components = AppComponents(
        service=ConversationService(
            agent,
            resolver,
            settings.today,
            build_guardrails(settings, order_client, menu_client),
            special_dates=SpecialDateCalendar.load(settings.special_dates_path, settings.special_dates_window_days),
            tracer=tracer,
        ),
        order_client=order_client,
        menu_client=menu_client,
    )
    logger.info("Delivery agent ready (model=%s)", settings.model_id)
    try:
        yield
    finally:
        await order_http.aclose()
        await menu_http.aclose()
        await asyncio.to_thread(tracer.shutdown)  # flushes the spans still buffered


def create_app(settings: Settings | None = None, components: AppComponents | None = None) -> FastAPI:
    """Create the FastAPI app; `components` can be injected to skip the lifespan wiring (tests)."""
    app = FastAPI(title="Beauty Pizza Delivery Agent", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings or get_settings()
    app.state.components = components

    @app.post("/messages", response_model=MessageResponse)
    async def post_message(payload: MessageRequest, request: Request) -> MessageResponse:
        """Handle one customer message and return the agent's reply with the order snapshot."""
        logger.info(
            "request POST /messages customer=%s chars=%d location=%s",
            mask_cpf(payload.client_document),
            len(payload.message),
            "set" if payload.location else "-",
            extra=REQUEST_HIGHLIGHT,
        )
        components: AppComponents = request.app.state.components
        try:
            return await components.service.handle(payload)
        except AgentUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Agent temporarily unavailable") from exc
        except Exception as exc:
            logger.exception("Unexpected error handling message")
            raise HTTPException(status_code=500, detail="Internal error") from exc

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        """Report whether the Order API and the Menu service are reachable and the menu vector index is loaded."""
        components: AppComponents = request.app.state.components
        reachable = components.order_client is not None and await components.order_client.ping()
        menu = await components.menu_client.health() if components.menu_client is not None else None
        return HealthResponse(
            order_api="ok" if reachable else "unreachable",
            menu_service="ok" if menu is not None else "unreachable",
            menu_index="ready" if menu is not None and menu.get("index") == "ready" else "missing",
        )

    return app
