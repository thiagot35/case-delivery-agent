"""Order tools (🟨 nodes calling the Order API).

Customer identity and today's date come from `run_context.dependencies` (injected per request, never from the model).
Prices come from the Menu service (never from the model). Totals come from the Order API.
Contract: specs/001-pizza-ordering-agent/contracts/agent-tools.md
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from agno.run import RunContext
from agno.tools import Toolkit
from pydantic import ValidationError

from delivery_agent.agent.state import (
    adopt_order,
    ensure_defaults,
    mark_cancelled,
    mark_confirmed,
    mark_open,
    reset_order,
    status_of,
    store_snapshot,
)
from delivery_agent.clients.errors import (
    ItemNotFoundError,
    MenuServiceError,
    OrderAlreadyExistsError,
    OrderApiUnavailableError,
    OrderApiValidationError,
    OrderNotFoundError,
)
from delivery_agent.clients.menu_api_client import MenuApiClient
from delivery_agent.clients.order_api_client import OrderApiClient
from delivery_agent.domain.formatting import build_item_name, format_address, format_brl, order_view, parse_item_name
from delivery_agent.domain.models import CustomerContext, DeliveryAddress, Order, OrderItemIn, to_money
from delivery_agent.tools.menu_tools import menu_unavailable, quote_error
from delivery_agent.tools.results import fail, ok

logger = logging.getLogger(__name__)

MAX_QUANTITY = 20


class _ToolError(Exception):
    """Internal: aborts a tool with a structured error result."""

    def __init__(self, result: str):
        """Keep the ready-to-return tool result."""
        super().__init__(result)
        self.result = result


class OrderTools(Toolkit):
    def __init__(self, client: OrderApiClient, menu: MenuApiClient):
        """Keep the Order API and Menu service clients and register the order tools with Agno."""
        self._client = client
        self._menu = menu
        # The model may call several order tools in parallel within one turn (e.g. two add_pizza calls). They share
        # the session state, so calls of the same customer are serialized: otherwise both see "no order yet" and both
        # try to create one (the second violates the one-order-per-day constraint).
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        super().__init__(
            name="order_tools",
            tools=[
                self.get_customer_history,
                self.get_current_order,
                self.add_pizza,
                self.remove_item,
                self.repeat_last_order,
                self.get_delivery_address,
                self.confirm_delivery_address,
                self.set_delivery_address,
                self.confirm_order,
                self.cancel_order,
            ],
        )

    # =====================================================================================================
    # Public tools (docstrings are read by the model)
    # =====================================================================================================

    async def get_customer_history(self, run_context: RunContext) -> str:
        """Look up the customer's orders in the Order API. Call it at the start of every new conversation.
        Returns whether the customer already has an order for today and a summary of their last previous order
        (to offer repeating it). Does not return the address (use get_delivery_address at the address step)."""
        return await self._run(run_context, self._get_customer_history)

    async def get_current_order(self, run_context: RunContext) -> str:
        """Get the current order from the Order API: items (with item_id), quantities, unit prices, subtotals,
        delivery address and total price. Use it for the order summary before confirmation."""
        return await self._run(run_context, self._get_current_order)

    async def add_pizza(
        self, run_context: RunContext, flavor: str, size: str, crust: str = "Tradicional", quantity: int = 1
    ) -> str:
        """Add a pizza to the customer's order. Creates the order on the first item. The price is taken from the
        menu automatically. Call it once per distinct pizza. Returns the added item and the updated order total.

        Args:
            flavor: Flavor, e.g. "Calabresa" (typos tolerated).
            size: "Pequena", "Média" or "Grande".
            crust: "Tradicional", "Recheada com Cheddar" or "Recheada com Catupiry". Default "Tradicional".
            quantity: Number of identical pizzas, 1 to 20. Default 1.
        """
        return await self._run(run_context, self._add_pizza, flavor, size, crust, quantity)

    async def remove_item(self, run_context: RunContext, item_id: int) -> str:
        """Remove one item from the current order. Get the item_id from the current order items. To change an
        item (size, crust or quantity), remove it and add the new version with add_pizza.

        Args:
            item_id: The item_id of the item to remove.
        """
        return await self._run(run_context, self._remove_item, item_id)

    async def repeat_last_order(self, run_context: RunContext) -> str:
        """Repeat the customer's last previous order: its items are re-priced with today's menu and added to
        today's order, and its delivery address is copied. Only call it after the customer accepts the offer."""
        return await self._run(run_context, self._repeat_last_order)

    async def get_delivery_address(self, run_context: RunContext) -> str:
        """Address step, step 1. Call it when the customer has finished choosing items, BEFORE asking anything
        about the address. Returns the address of the current order, or else the address of the customer's last
        order (source=current_order|last_order), or source=none when there is no known address."""
        return await self._run(run_context, self._get_delivery_address)

    async def confirm_delivery_address(self, run_context: RunContext) -> str:
        """Address step, when the customer CONFIRMS the address returned by get_delivery_address. Applies it to
        the current order. Takes no arguments: the address comes from the previous get_delivery_address call."""
        return await self._run(run_context, self._confirm_delivery_address)

    async def set_delivery_address(
        self,
        run_context: RunContext,
        street_name: str,
        number: str,
        complement: str | None = None,
        reference_point: str | None = None,
    ) -> str:
        """Save a NEW delivery address given by the customer (no known address, or the customer said it changed).

        Args:
            street_name: Street name, e.g. "Rua das Flores".
            number: Building number, e.g. "100".
            complement: Optional complement, e.g. "Apto 12".
            reference_point: Optional reference point, e.g. "Perto da praça".
        """
        return await self._run(
            run_context, self._set_delivery_address, street_name, number, complement, reference_point
        )

    async def confirm_order(self, run_context: RunContext) -> str:
        """Place the order ("Pedido feito"). Only call it after showing the summary with the total and the
        customer explicitly confirming. Fails if the order has no items or no delivery address."""
        return await self._run(run_context, self._confirm_order)

    async def cancel_order(self, run_context: RunContext) -> str:
        """Cancel the current order (removes all its items). Only call it when the customer asks to cancel."""
        return await self._run(run_context, self._cancel_order)

    # =====================================================================================================
    # Implementations
    # =====================================================================================================

    async def _get_customer_history(self, ctx: "_Ctx") -> str:
        """Load the customer's orders, adopt today's order and record previous flavors."""
        orders = await self._client.find_orders(ctx.customer.client_document)
        today_order = next((o for o in orders if o.delivery_date == ctx.today), None)
        previous = [o for o in orders if o.delivery_date != ctx.today]
        last_order = previous[0] if previous else None

        flavors: list[str] = []
        for order in orders:
            for item in order.items:
                parsed = parse_item_name(item.name)
                if parsed and parsed.flavor not in flavors:
                    flavors.append(parsed.flavor)
        ctx.state["previous_flavors"] = flavors[:5]
        ctx.state["last_order_id"] = last_order.id if last_order else None

        if today_order is not None and ctx.state.get("order_id") is None:
            adopt_order(ctx.state, today_order)

        return ok(
            has_history=bool(orders),
            today_order=order_view(today_order, status_of(ctx.state)) if today_order and today_order.items else None,
            last_order=_history_view(last_order) if last_order else None,
        )

    async def _get_current_order(self, ctx: "_Ctx") -> str:
        """Return the current order as stored in the Order API."""
        order = await self._current_order(ctx)
        return ok(order=order_view(order, status_of(ctx.state)))

    async def _add_pizza(self, ctx: "_Ctx", flavor: str, size: str, crust: str, quantity: int) -> str:
        """Validate and price the pizza, then append it to the order (creating it if needed)."""
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 0
        if not 1 <= quantity <= MAX_QUANTITY:
            return fail("invalid_quantity", f"A quantidade deve ser entre 1 e {MAX_QUANTITY}.")

        item = await self._build_item(flavor, size, crust, quantity)
        order = await self._append_items(ctx, [item])
        mark_open(ctx.state)
        ctx.state["flow_step"] = "building"
        store_snapshot(ctx.state, order)
        return ok(
            added={"name": item.name, "quantity": item.quantity, "unit_price_display": format_brl(item.unit_price)},
            order=order_view(order, status_of(ctx.state)),
        )

    async def _remove_item(self, ctx: "_Ctx", item_id: int) -> str:
        """Delete one item and return the refreshed order."""
        order_id = self._require_order_id(ctx)
        try:
            await self._client.remove_item(order_id, int(item_id))
        except ItemNotFoundError:
            order = await self._refresh(ctx, order_id)
            return fail(
                "item_not_found",
                "Esse item não está no pedido atual.",
                order=order_view(order, status_of(ctx.state)),
            )
        mark_open(ctx.state)
        ctx.state["flow_step"] = "building"
        order = await self._refresh(ctx, order_id)
        return ok(removed_item_id=int(item_id), order=order_view(order, status_of(ctx.state)))

    async def _repeat_last_order(self, ctx: "_Ctx") -> str:
        """Re-price the last order's items, add them to today's order and copy its address."""
        ctx.state["repeat_offered"] = True
        last_order_id = ctx.state.get("last_order_id")
        if last_order_id is None:
            return fail("no_history", "O cliente não tem pedido anterior para repetir.")
        last_order = await self._client.get_order(int(last_order_id))

        items: list[OrderItemIn] = []
        skipped: list[str] = []
        price_changes: list[dict[str, str]] = []
        for old in last_order.items:
            parsed = parse_item_name(old.name)
            quote = await self._menu.quote(parsed.flavor, parsed.size, parsed.crust) if parsed else None
            # Only the very same flavor counts: a fuzzy match to another flavor is not a repeat.
            same_flavor = bool(quote and parsed and quote.pizza and quote.pizza.flavor.lower() == parsed.flavor.lower())
            if quote is None or quote.status != "ok" or quote.price is None or not same_flavor or old.quantity < 1:
                skipped.append(old.name)
                continue
            new_price = to_money(quote.price)
            if to_money(old.unit_price) != new_price:
                price_changes.append(
                    {"name": old.name, "old": format_brl(old.unit_price), "new": format_brl(new_price)}
                )
            items.append(OrderItemIn(name=old.name, quantity=min(old.quantity, MAX_QUANTITY), unit_price=new_price))

        if not items:
            return fail(
                "no_history", "Nenhum item do último pedido está disponível no cardápio atual.", skipped=skipped
            )

        order = await self._append_items(ctx, items)
        if order.delivery_address is None and last_order.delivery_address is not None:
            await self._client.update_address(order.id, last_order.delivery_address)
            order = await self._client.get_order(order.id)
        mark_open(ctx.state)
        ctx.state["flow_step"] = "building"
        store_snapshot(ctx.state, order)
        return ok(
            added_items=[i.name for i in items],
            skipped_items=skipped,
            price_changes=price_changes,
            order=order_view(order, status_of(ctx.state)),
        )

    async def _get_delivery_address(self, ctx: "_Ctx") -> str:
        """Find the address candidate (current order, then last order) and keep it in the state."""
        order = await self._current_order(ctx)
        candidate: dict[str, Any] | None = None
        if order.delivery_address is not None:
            candidate = {"source": "current_order", "from_order_id": order.id, "address": order.delivery_address}
        else:
            orders = await self._client.find_orders(ctx.customer.client_document)
            previous = next((o for o in orders if o.id != order.id and o.delivery_address is not None), None)
            if previous is not None:
                candidate = {"source": "last_order", "from_order_id": previous.id, "address": previous.delivery_address}

        ctx.state["flow_step"] = "address"
        if candidate is None:
            ctx.state["address_candidate"] = None
            return ok(source="none", address=None)

        address: DeliveryAddress = candidate["address"]
        ctx.state["address_candidate"] = {**candidate, "address": address.model_dump(mode="json")}
        return ok(
            source=candidate["source"],
            from_order_id=candidate["from_order_id"],
            address=address.model_dump(mode="json"),
            address_display=format_address(address),
        )

    async def _confirm_delivery_address(self, ctx: "_Ctx") -> str:
        """Apply the address candidate to the current order."""
        order_id = self._require_order_id(ctx)
        candidate = ctx.state.get("address_candidate")
        if not candidate or not candidate.get("address"):
            return fail("no_address_candidate", "Chame get_delivery_address antes de confirmar o endereço.")
        address = DeliveryAddress.model_validate(candidate["address"])
        if candidate.get("source") != "current_order":
            await self._client.update_address(order_id, address)
            mark_open(ctx.state)
        ctx.state["address_candidate"] = None
        ctx.state["flow_step"] = "review"
        order = await self._refresh(ctx, order_id)
        return ok(address_display=format_address(address), order=order_view(order, status_of(ctx.state)))

    async def _set_delivery_address(
        self, ctx: "_Ctx", street_name: str, number: str, complement: str | None, reference_point: str | None
    ) -> str:
        """Validate and save a new address on the current order."""
        order_id = self._require_order_id(ctx)
        try:
            address = DeliveryAddress(
                street_name=street_name or "",
                number=str(number or ""),
                complement=complement,
                reference_point=reference_point,
            )
        except ValidationError:
            return fail("invalid_address", "Endereço incompleto: rua e número são obrigatórios.")
        await self._client.update_address(order_id, address)
        mark_open(ctx.state)
        ctx.state["address_candidate"] = None
        ctx.state["flow_step"] = "review"
        order = await self._refresh(ctx, order_id)
        return ok(address_display=format_address(address), order=order_view(order, status_of(ctx.state)))

    async def _confirm_order(self, ctx: "_Ctx") -> str:
        """Mark the order as placed when it has items and an address."""
        order = await self._current_order(ctx)
        if not order.items:
            return fail("missing_items", "O pedido não tem itens. Pergunte o que o cliente deseja.")
        if order.delivery_address is None:
            return fail("missing_address", "O pedido não tem endereço de entrega. Siga a etapa de endereço.")
        mark_confirmed(ctx.state)
        store_snapshot(ctx.state, order)
        return ok(
            order_id=order.id,
            total_price_display=format_brl(order.total_price),
            delivery_address=format_address(order.delivery_address),
            order=order_view(order, status_of(ctx.state)),
        )

    async def _cancel_order(self, ctx: "_Ctx") -> str:
        """Remove every item and mark the order as cancelled."""
        order = await self._current_order(ctx)
        for item in order.items:
            try:
                await self._client.remove_item(order.id, item.id)
            except ItemNotFoundError:
                continue
        mark_cancelled(ctx.state)
        order = await self._refresh(ctx, order.id)
        return ok(order_id=order.id, cancelled=True)

    # =====================================================================================================
    # Helpers
    # =====================================================================================================

    async def _run(self, run_context: RunContext, func: Callable[..., Awaitable[str]], *args: Any) -> str:
        """Build the context and translate infrastructure errors into structured tool results."""
        try:
            ctx = _Ctx.from_run_context(run_context)
        except (KeyError, ValueError, ValidationError):
            logger.exception("Tool called without customer context")
            return fail("missing_customer_context", "Contexto do cliente indisponível.")
        async with self._locks[ctx.customer.client_document]:
            return await self._execute(ctx, func, *args)

    async def _execute(self, ctx: "_Ctx", func: Callable[..., Awaitable[str]], *args: Any) -> str:
        """Run a tool implementation, mapping Order API errors to structured results."""
        try:
            return await func(ctx, *args)
        except _ToolError as exc:
            return exc.result
        except OrderNotFoundError:
            reset_order(ctx.state)
            return fail("order_not_found", "O pedido atual não foi encontrado. Um novo pedido será criado.")
        except OrderApiValidationError as exc:
            logger.warning("Order API rejected request: %s", exc.errors)
            return fail("order_api_rejected", "O sistema de pedidos recusou a operação.", details=exc.errors)
        except OrderApiUnavailableError:
            return fail(
                "order_api_unavailable",
                "O sistema de pedidos está indisponível no momento. Peça desculpas e sugira tentar em instantes.",
            )
        except MenuServiceError:
            return menu_unavailable()

    async def _build_item(self, flavor: str, size: str, crust: str, quantity: int) -> OrderItemIn:
        """Resolve flavor, size and crust and price the item through the Menu service."""
        quote = await self._menu.quote(flavor, size, crust)
        if error := quote_error(quote):
            raise _ToolError(error)
        assert quote.pizza is not None and quote.size and quote.crust and quote.price is not None
        return OrderItemIn(
            name=build_item_name(quote.pizza.flavor, quote.size, quote.crust),
            quantity=quantity,
            unit_price=to_money(quote.price),
        )

    async def _append_items(self, ctx: "_Ctx", items: list[OrderItemIn]) -> Order:
        """Add items to the session order, adopting today's order or creating one when needed."""
        order_id = ctx.state.get("order_id")
        if order_id is not None:
            try:
                return await self._add_and_refresh(ctx, int(order_id), items)
            except OrderNotFoundError:
                reset_order(ctx.state)

        existing = await self._client.find_orders(ctx.customer.client_document, ctx.today)
        if existing:
            adopt_order(ctx.state, existing[0])
            return await self._add_and_refresh(ctx, existing[0].id, items)

        try:
            order = await self._client.create_order(ctx.customer, ctx.today, items)
        except OrderAlreadyExistsError:
            # Race with another request: adopt the order that now exists.
            existing = await self._client.find_orders(ctx.customer.client_document, ctx.today)
            if not existing:
                raise
            adopt_order(ctx.state, existing[0])
            return await self._add_and_refresh(ctx, existing[0].id, items)
        adopt_order(ctx.state, order)
        return order

    async def _add_and_refresh(self, ctx: "_Ctx", order_id: int, items: list[OrderItemIn]) -> Order:
        """Add items to an existing order and return it re-read from the API."""
        try:
            await self._client.add_items(order_id, items)
        except OrderApiValidationError:
            await self._refresh(ctx, order_id)  # the API may have saved part of the items
            raise
        return await self._client.get_order(order_id)

    def _require_order_id(self, ctx: "_Ctx") -> int:
        """Return the session's order id, or abort the tool when there is no order yet."""
        order_id = ctx.state.get("order_id")
        if order_id is None:
            raise _ToolError(fail("no_open_order", "Ainda não há pedido. Adicione uma pizza primeiro."))
        return int(order_id)

    async def _current_order(self, ctx: "_Ctx") -> Order:
        """Fetch the session's current order."""
        return await self._refresh(ctx, self._require_order_id(ctx))

    async def _refresh(self, ctx: "_Ctx", order_id: int) -> Order:
        """Re-read an order and store its snapshot in the session state."""
        order = await self._client.get_order(order_id)
        store_snapshot(ctx.state, order)
        return order


class _Ctx:
    """Per-call view of the run context: customer, today's date and the mutable session state."""

    def __init__(self, customer: CustomerContext, today: date, state: dict[str, Any]):
        """Hold the customer, today's date and the mutable session state."""
        self.customer = customer
        self.today = today
        self.state = state

    @classmethod
    def from_run_context(cls, run_context: RunContext) -> "_Ctx":
        """Build the context from the identity injected by the service (never from the model)."""
        deps = run_context.dependencies or {}
        customer = CustomerContext(client_name=deps["client_name"], client_document=deps["client_document"])
        today = date.fromisoformat(str(deps["today"]))
        if run_context.session_state is None:
            run_context.session_state = {}
        return cls(customer, today, ensure_defaults(run_context.session_state))


def _history_view(order: Order) -> dict[str, Any]:
    """Summarize a previous order for the model (items and total, no address)."""
    return {
        "order_id": order.id,
        "delivery_date": order.delivery_date.isoformat(),
        "items": [
            {"name": item.name, "quantity": item.quantity, "unit_price_display": format_brl(item.unit_price)}
            for item in order.items
        ],
        "total_price_display": format_brl(order.total_price),
    }
