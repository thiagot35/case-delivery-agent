"""FastAPI application of the Menu service: wiring (lifespan), admin write endpoints and read endpoints."""

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from menu_service.bootstrap import ensure_menu_db
from menu_service.config import MenuSettings, get_settings
from menu_service.schemas import (
    DeleteRequest,
    DeleteResponse,
    HealthResponse,
    MenuResponse,
    QuoteResponse,
    ResolveResponse,
    SearchRequest,
    SearchResponse,
    UpsertRequest,
    UpsertResponse,
)
from menu_service.service import IndexUnavailableError, MenuService
from menu_service.store import DuplicateValueError, ItemNotFoundError, ReferenceNotFoundError
from menu_service.vector_index import MenuVectorIndex

logger = logging.getLogger(__name__)


def build_index(settings: MenuSettings) -> MenuVectorIndex | None:
    """Open the vector index with the OpenAI embedder; without an API key semantic search is disabled."""
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set: semantic menu search disabled (keyword fallback)")
        return None
    from agno.knowledge.embedder.openai import OpenAIEmbedder

    embedder = OpenAIEmbedder(id=settings.embedding_model_id, api_key=settings.openai_api_key)
    return MenuVectorIndex(settings.vector_db_path, embedder)


def build_service(settings: MenuSettings) -> MenuService:
    """Seed the database if needed, open the index and sync it with the menu."""
    ensure_menu_db(settings.kb_sql_path, settings.menu_db_path)
    index = build_index(settings)
    service = MenuService(settings.menu_db_path, index, settings.semantic_max_distance)
    try:
        service.sync_index()
    except Exception:
        logger.exception("Could not sync the menu vector index: semantic search disabled")
        service = MenuService(settings.menu_db_path, None, settings.semantic_max_distance)
    return service


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the service once at startup unless one was injected (tests)."""
    if getattr(app.state, "service", None) is None:
        app.state.service = build_service(app.state.settings)
        logger.info("Menu service ready (index=%s)", "ready" if app.state.service.index_ready else "missing")
    yield


def _service(request: Request) -> MenuService:
    """FastAPI dependency: the service built by the lifespan."""
    return request.app.state.service


def _require_admin(request: Request, x_admin_token: Annotated[str | None, Header()] = None) -> None:
    """FastAPI dependency: when MENU_ADMIN_TOKEN is set, writes need a matching `X-Admin-Token` header."""
    expected = request.app.state.settings.menu_admin_token
    if expected and not hmac.compare_digest(x_admin_token or "", expected):
        raise HTTPException(status_code=401, detail={"error_code": "unauthorized", "message": "Invalid admin token"})


Service = Annotated[MenuService, Depends(_service)]


def create_app(settings: MenuSettings | None = None, service: MenuService | None = None) -> FastAPI:
    """Create the Menu service app; `service` can be injected to skip the lifespan wiring (tests)."""
    app = FastAPI(title="Beauty Pizza Menu Service", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings or get_settings()
    app.state.service = service

    @app.exception_handler(ItemNotFoundError)
    async def _not_found(request: Request, exc: ItemNotFoundError) -> JSONResponse:
        """404 for a missing row."""
        return JSONResponse(
            status_code=404,
            content={"error_code": "not_found", "message": str(exc), "table": exc.table, "id": exc.key},
        )

    @app.exception_handler(DuplicateValueError)
    async def _duplicate(request: Request, exc: DuplicateValueError) -> JSONResponse:
        """409 for a UNIQUE violation."""
        return JSONResponse(
            status_code=409, content={"error_code": "duplicate", "message": exc.detail, "table": exc.table}
        )

    @app.exception_handler(ReferenceNotFoundError)
    async def _reference(request: Request, exc: ReferenceNotFoundError) -> JSONResponse:
        """422 when a price references a pizza, size or crust that does not exist."""
        return JSONResponse(
            status_code=422,
            content={"error_code": "reference_not_found", "message": str(exc), "missing": exc.missing},
        )

    @app.exception_handler(IndexUnavailableError)
    async def _index_unavailable(request: Request, exc: IndexUnavailableError) -> JSONResponse:
        """503 when the embedding failed (the menu change was rolled back)."""
        return JSONResponse(
            status_code=503,
            content={
                "error_code": "index_unavailable",
                "message": "The vector index could not be updated; the menu change was rolled back.",
            },
        )

    # --- Admin: menu changes ------------------------------------------------------------------------------

    @app.put("/menu/items", response_model=UpsertResponse, dependencies=[Depends(_require_admin)])
    def upsert_item(payload: Annotated[UpsertRequest, Body()], service: Service) -> UpsertResponse:
        """Add (no `id`) or edit (with `id`; `precos` by its composite key) a menu row, then re-embed."""
        return service.upsert(payload.table, payload.fields.model_dump(exclude_none=True))

    @app.delete("/menu/items", response_model=DeleteResponse, dependencies=[Depends(_require_admin)])
    def delete_item(payload: Annotated[DeleteRequest, Body()], service: Service) -> DeleteResponse:
        """Delete a menu row; pizzas, sizes and crusts also lose the prices that reference them."""
        key = payload.id if isinstance(payload.id, int) else payload.id.model_dump()
        return service.delete(payload.table, key)

    # --- Reads (used by the agent) -----------------------------------------------------------------------

    @app.get("/menu", response_model=MenuResponse)
    def get_menu(service: Service) -> MenuResponse:
        """Return the orderable menu: pizzas with prices and price range, sizes and crusts."""
        return service.menu()

    @app.get("/menu/flavors/resolve", response_model=ResolveResponse)
    def resolve_flavor(service: Service, text: Annotated[str, Query(max_length=200)]) -> ResolveResponse:
        """Resolve the customer's wording to a flavor."""
        return service.resolve_flavor(text)

    @app.get("/menu/quote", response_model=QuoteResponse)
    def quote(
        service: Service,
        flavor: Annotated[str, Query(max_length=200)],
        size: Annotated[str, Query(max_length=100)],
        crust: Annotated[str | None, Query(max_length=100)] = None,
    ) -> QuoteResponse:
        """Resolve and price a flavor/size/crust combination."""
        return service.quote(flavor, size, crust)

    @app.post("/menu/search", response_model=SearchResponse)
    def search(payload: SearchRequest, service: Service) -> SearchResponse:
        """Semantic search over the menu (the query is embedded here)."""
        return service.search(payload.query, payload.limit)

    @app.get("/health", response_model=HealthResponse)
    def health(service: Service) -> HealthResponse:
        """Report liveness and whether the vector index is loaded."""
        return HealthResponse(index="ready" if service.index_ready else "missing")

    return app
