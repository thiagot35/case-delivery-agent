"""Typed errors raised by the Order API and Menu service clients."""

from typing import Any


class OrderApiError(Exception):
    """Base class for Order API failures."""


class OrderApiUnavailableError(OrderApiError):
    """Timeout, connection error or 5xx."""


class OrderApiValidationError(OrderApiError):
    """HTTP 400 with validation details."""

    def __init__(self, errors: Any):
        """Keep the validation errors returned by the Order API."""
        super().__init__(f"Order API rejected the request: {errors}")
        self.errors = errors


class OrderAlreadyExistsError(OrderApiValidationError):
    """The (client_name, client_document, delivery_date) unique constraint was violated."""


class OrderNotFoundError(OrderApiError):
    """HTTP 404 for an order."""


class ItemNotFoundError(OrderApiError):
    """HTTP 404 when deleting an item that is not in the order."""


class MenuServiceError(Exception):
    """The Menu service answered with an unexpected error."""


class MenuServiceUnavailableError(MenuServiceError):
    """Timeout, connection error or 5xx from the Menu service."""
