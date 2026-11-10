"""Static greeting for the first message of the day (specs/004-greeting). No model is called.

A returning customer gets the last order summary and the offer to repeat it; a new customer gets a few random flavors
from the menu. Prices and items come from the Order API and the menu from the Menu service (constitution II, III).
"""

import logging
import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from delivery_agent.clients.errors import MenuServiceError, OrderApiError
from delivery_agent.clients.menu_api_client import MenuApiClient
from delivery_agent.clients.order_api_client import OrderApiClient
from delivery_agent.domain.formatting import format_brl, parse_item_name
from delivery_agent.domain.models import CustomerContext, Order
from delivery_agent.guardrails import messages
from delivery_agent.guardrails.protocols import Greeter

logger = logging.getLogger(__name__)

CustomerType = Literal["new", "returning"]
FLAVOR_COUNT = 3


@dataclass(frozen=True)
class Greeting:
    """The greeting reply and the session state it sets for the order agent's next turns."""

    reply: str
    customer_type: CustomerType
    state_updates: dict[str, Any] = field(default_factory=dict)


class GreetingResponder(Greeter):
    """Builds the greeting from the customer's order history and the menu."""

    def __init__(self, orders: OrderApiClient, menu: MenuApiClient, rng: random.Random | None = None):
        """Keep the Order API and Menu service clients and the random generator used to pick flavors."""
        self._orders = orders
        self._menu = menu
        self._rng = rng or random.Random()

    async def greet(self, customer: CustomerContext, today: date) -> Greeting | None:
        """Return the greeting, or None when the customer already has an order today (the order agent answers)."""
        try:
            orders = await self._orders.find_orders(customer.client_document)
        except OrderApiError:
            logger.warning("greeting: Order API unavailable, greeting as a new customer")
            orders = []
        if any(order.delivery_date == today and order.items for order in orders):
            return None
        previous = [order for order in orders if order.delivery_date != today and order.items]
        if previous:
            return self._returning(customer.first_name, previous[0], orders)
        return Greeting(messages.greeting_new(customer.first_name, await self._flavors()), "new", {"greeted": True})

    def _returning(self, first_name: str, last_order: Order, orders: list[Order]) -> Greeting:
        """Greeting with the last order summary; the state lets the order agent repeat it on a "sim"."""
        items = [f"{item.quantity}x {item.name}" for item in last_order.items]
        flavors: list[str] = []
        for order in orders:
            for item in order.items:
                parsed = parse_item_name(item.name)
                if parsed and parsed.flavor not in flavors:
                    flavors.append(parsed.flavor)
        state = {
            "greeted": True,
            "repeat_offered": True,
            "last_order_id": last_order.id,
            "previous_flavors": flavors[:5],
        }
        reply = messages.greeting_returning(first_name, items, format_brl(last_order.total_price))
        return Greeting(reply, "returning", state)

    async def _flavors(self) -> list[str]:
        """Pick random flavors from the menu, or none when the Menu service is unavailable."""
        try:
            menu = await self._menu.get_menu()
        except MenuServiceError:
            logger.warning("greeting: Menu service unavailable, greeting without flavors")
            return []
        flavors = sorted({pizza.flavor for pizza in menu.pizzas})
        if len(flavors) < FLAVOR_COUNT:
            return []
        return self._rng.sample(flavors, FLAVOR_COUNT)
