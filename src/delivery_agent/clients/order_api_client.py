"""Async client for the Order API (`candidates-case-order-api`).

Contract: specs/001-pizza-ordering-agent/contracts/order-api-client.md
"""

import asyncio
import logging
import time
from datetime import date
from typing import Any

import httpx

from delivery_agent.clients.errors import (
    ItemNotFoundError,
    OrderAlreadyExistsError,
    OrderApiUnavailableError,
    OrderApiValidationError,
    OrderNotFoundError,
)
from delivery_agent.domain.cpf import mask_cpf
from delivery_agent.domain.models import CustomerContext, DeliveryAddress, Order, OrderItemIn

logger = logging.getLogger(__name__)

GET_RETRY_BACKOFF_SECONDS = 0.3


def _item_payload(item: OrderItemIn) -> dict[str, Any]:
    """Serialize an item for the Order API."""
    # Decimal sent as a string with 2 decimals: DRF DecimalField parses it without float rounding issues.
    return {"name": item.name, "quantity": item.quantity, "unit_price": f"{item.unit_price:.2f}"}


def _address_payload(address: DeliveryAddress) -> dict[str, Any]:
    """Serialize an address for the Order API, always with all four keys."""
    # All 4 keys are always sent: the API `setattr`s only the provided keys, so omitting optional ones would keep
    # stale values from a previous address.
    return {
        "street_name": address.street_name,
        "number": address.number,
        "complement": address.complement,
        "reference_point": address.reference_point,
    }


def _is_unique_violation(body: Any) -> bool:
    """Tell whether a 400 body is the one-order-per-customer-per-day constraint error."""
    if not isinstance(body, dict):
        return False
    messages = body.get("non_field_errors") or []
    return any("unique" in str(message).lower() for message in messages)


class OrderApiClient:
    def __init__(self, http: httpx.AsyncClient):
        """`http` must be configured with the Order API `base_url` and timeout."""
        self._http = http

    @classmethod
    def create_http_client(cls, base_url: str, timeout_seconds: float) -> httpx.AsyncClient:
        """Create the shared HTTP client for the Order API base URL and timeout."""
        return httpx.AsyncClient(base_url=base_url.rstrip("/") + "/", timeout=timeout_seconds)

    # --- Public API --------------------------------------------------------------------------------------

    async def create_order(
        self,
        customer: CustomerContext,
        delivery_date: date,
        items: list[OrderItemIn],
        address: DeliveryAddress | None = None,
    ) -> Order:
        """Create an order (`POST orders/`) and return it with item ids and total."""
        payload: dict[str, Any] = {
            "client_name": customer.client_name,
            "client_document": customer.client_document,
            "delivery_date": delivery_date.isoformat(),
            "items": [_item_payload(item) for item in items],
        }
        if address is not None:
            payload["delivery_address"] = _address_payload(address)
        response = await self._request("POST", "orders/", json=payload)
        if response.status_code == 400:
            body = _json(response)
            if _is_unique_violation(body):
                raise OrderAlreadyExistsError(body)
            raise OrderApiValidationError(body)
        self._raise_for_status(response)
        return Order.model_validate(response.json())

    async def get_order(self, order_id: int) -> Order:
        """Fetch one order with its items and total (`GET orders/{id}/`)."""
        response = await self._request("GET", f"orders/{order_id}/")
        if response.status_code == 404:
            raise OrderNotFoundError(f"Order {order_id} not found")
        self._raise_for_status(response)
        return Order.model_validate(response.json())

    async def find_orders(self, client_document: str, delivery_date: date | None = None) -> list[Order]:
        """Orders of a customer, most recent first (highest id)."""
        params = {"client_document": client_document}
        if delivery_date is not None:
            params["delivery_date"] = delivery_date.isoformat()
        response = await self._request("GET", "orders/filter/", params=params)
        self._raise_for_status(response)
        orders = [Order.model_validate(item) for item in response.json()]
        return sorted(orders, key=lambda order: order.id, reverse=True)

    async def add_items(self, order_id: int, items: list[OrderItemIn]) -> None:
        """Append items to an order (`PATCH orders/{id}/add-items/`)."""
        payload = {"items": [_item_payload(item) for item in items]}
        response = await self._request("PATCH", f"orders/{order_id}/add-items/", json=payload)
        if response.status_code == 404:
            raise OrderNotFoundError(f"Order {order_id} not found")
        if response.status_code == 400:
            # Caveat: the API persists the valid items before reporting errors. Callers must re-read the order.
            raise OrderApiValidationError(_json(response))
        self._raise_for_status(response)

    async def remove_item(self, order_id: int, item_id: int) -> None:
        """Delete one item from an order (`DELETE orders/{id}/items/{item_id}/`)."""
        # The API answers 204 *with* a JSON body. httpx reads 204 as bodyless, so the leftover bytes would corrupt
        # the next response on a reused keep-alive connection ("illegal status line"). Closing it avoids that.
        response = await self._request("DELETE", f"orders/{order_id}/items/{item_id}/", headers={"Connection": "close"})
        if response.status_code == 404:
            raise ItemNotFoundError(f"Item {item_id} not found in order {order_id}")
        self._raise_for_status(response)

    async def update_address(self, order_id: int, address: DeliveryAddress) -> None:
        """Replace the order's delivery address (`PATCH orders/{id}/update-address/`)."""
        payload = {"delivery_address": _address_payload(address)}
        response = await self._request("PATCH", f"orders/{order_id}/update-address/", json=payload)
        if response.status_code == 404:
            raise OrderNotFoundError(f"Order {order_id} not found")
        if response.status_code == 400:
            raise OrderApiValidationError(_json(response))
        self._raise_for_status(response)

    async def ping(self) -> bool:
        """Return True when the Order API answers a lightweight filter query."""
        try:
            await self.find_orders("00000000000")
            return True
        except Exception:
            return False

    # --- Transport ---------------------------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request with logging; GET is retried once on timeout, connection error or 5xx."""
        attempts = 2 if method == "GET" else 1  # only idempotent requests are retried
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._http.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning("order_api %s %s failed (attempt %d): %s", method, path, attempt, exc)
                if attempt < attempts:
                    await asyncio.sleep(GET_RETRY_BACKOFF_SECONDS)
                    continue
                raise OrderApiUnavailableError(str(exc)) from exc

            self._log(method, path, kwargs.get("params"), response.status_code, started)
            if response.status_code >= 500 and attempt < attempts:
                await asyncio.sleep(GET_RETRY_BACKOFF_SECONDS)
                continue
            return response
        raise OrderApiUnavailableError("unreachable")  # pragma: no cover

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map unexpected 4xx/5xx responses to the client's typed errors."""
        if response.status_code >= 500:
            raise OrderApiUnavailableError(f"Order API returned {response.status_code}")
        if response.status_code >= 400:
            raise OrderApiValidationError(_json(response))

    @staticmethod
    def _log(method: str, path: str, params: dict | None, status: int, started: float) -> None:
        """Log method, path, CPF-masked params, status and latency of a call."""
        safe_params = dict(params or {})
        if "client_document" in safe_params:
            safe_params["client_document"] = mask_cpf(safe_params["client_document"])
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("order_api %s %s params=%s status=%d %.0fms", method, path, safe_params, status, elapsed_ms)


def _json(response: httpx.Response) -> Any:
    """Return the JSON body, or the raw text when the body is not JSON."""
    try:
        return response.json()
    except ValueError:
        return response.text
