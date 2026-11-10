"""Async read-only client for the Menu service. The agent has no way to change the menu.

Contract: specs/002-menu-service/contracts/menu-service-api.md
"""

import logging
import time
from typing import Any

import httpx

from delivery_agent.clients.errors import MenuServiceError, MenuServiceUnavailableError
from delivery_agent.domain.models import FlavorMatch, Menu, MenuQuote, MenuSearchHit

logger = logging.getLogger(__name__)


class MenuApiClient:
    """Reads the menu, resolves flavors, prices combinations and runs semantic searches through the Menu service."""

    def __init__(self, http: httpx.AsyncClient):
        """`http` must be configured with the Menu service `base_url` and timeout."""
        self._http = http

    @classmethod
    def create_http_client(cls, base_url: str, timeout_seconds: float) -> httpx.AsyncClient:
        """Create the shared HTTP client for the Menu service base URL and timeout."""
        return httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", timeout=timeout_seconds)

    async def get_menu(self) -> Menu:
        """Return the orderable menu (`GET menu`)."""
        return Menu.model_validate(await self._request("GET", "menu"))

    async def resolve_flavor(self, text: str) -> FlavorMatch:
        """Resolve the customer's wording to a flavor (`GET menu/flavors/resolve`)."""
        return FlavorMatch.model_validate(await self._request("GET", "menu/flavors/resolve", params={"text": text}))

    async def quote(self, flavor: str, size: str, crust: str | None = None) -> MenuQuote:
        """Resolve and price a combination (`GET menu/quote`)."""
        params = {"flavor": flavor, "size": size, **({"crust": crust} if crust else {})}
        return MenuQuote.model_validate(await self._request("GET", "menu/quote", params=params))

    async def search(self, query: str, limit: int = 3) -> list[MenuSearchHit]:
        """Semantic search; the Menu service embeds the query and searches its vector index (`POST menu/search`)."""
        body = await self._request("POST", "menu/search", json={"query": query, "limit": limit})
        return [MenuSearchHit.model_validate(hit) for hit in body["results"]]

    async def health(self) -> dict[str, Any] | None:
        """Return the Menu service health body, or None when it is unreachable."""
        try:
            return await self._request("GET", "health")
        except MenuServiceError:
            return None

    async def ping(self) -> bool:
        """Tell whether the Menu service is reachable."""
        return await self.health() is not None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send a request and return the JSON body, mapping failures to typed errors."""
        started = time.perf_counter()
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("menu service %s %s failed: %s", method, path, exc.__class__.__name__)
            raise MenuServiceUnavailableError(str(exc)) from exc
        logger.debug(
            "menu service %s %s -> %d (%.0f ms)",
            method,
            path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        if response.status_code >= 500:
            raise MenuServiceUnavailableError(f"{method} {path} -> {response.status_code}")
        if response.status_code >= 400:
            raise MenuServiceError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
        return response.json()
