"""Agent session state: defaults, typed accessors and status transitions (data-model.md §3).

Only tools mutate the session state. Values must stay JSON-serializable (Agno persists them in SQLite).
"""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from delivery_agent.domain.models import Order, OrderSnapshot, OrderStatus

DEFAULT_SESSION_STATE: dict[str, Any] = {
    "order_id": None,
    "order_status": "none",
    "flow_step": "start",
    "address_set": False,
    "address_candidate": None,
    "last_order_id": None,
    "previous_flavors": [],
    "repeat_offered": False,
    "confirmed_at": None,
    "order_snapshot": None,
    "human_requests": 0,
    "greeted": False,
    # specs/005-special-dates: dates of the next days, set only for the first agent reply of the session.
    "upcoming_dates": [],
    "special_dates_sent": False,
}


def ensure_defaults(state: dict[str, Any]) -> dict[str, Any]:
    """Fill missing keys in place (new sessions start with only the run parameters)."""
    for key, value in DEFAULT_SESSION_STATE.items():
        state.setdefault(key, deepcopy(value))
    return state


def adopt_order(state: dict[str, Any], order: Order) -> None:
    """Make `order` the session's current order."""
    state["order_id"] = order.id
    if state.get("order_status") in (None, "none"):
        state["order_status"] = "open"
    state["address_set"] = order.delivery_address is not None
    store_snapshot(state, order)


def reset_order(state: dict[str, Any]) -> None:
    """Forget the current order (e.g. it no longer exists in the Order API)."""
    state.update(
        order_id=None,
        order_status="none",
        address_set=False,
        address_candidate=None,
        order_snapshot=None,
        confirmed_at=None,
    )


def mark_open(state: dict[str, Any]) -> None:
    """Any write reopens the order (a confirmed or cancelled order must be re-confirmed)."""
    state["order_status"] = "open"
    state["confirmed_at"] = None


def mark_confirmed(state: dict[str, Any]) -> None:
    """Mark the order as placed and record when it was confirmed."""
    state["order_status"] = "confirmed"
    state["flow_step"] = "confirmed"
    state["confirmed_at"] = datetime.now(UTC).isoformat()


def mark_cancelled(state: dict[str, Any]) -> None:
    """Mark the order as cancelled and send the flow back to the start."""
    state["order_status"] = "cancelled"
    state["flow_step"] = "start"
    state["confirmed_at"] = None
    state["address_candidate"] = None


def status_of(state: dict[str, Any]) -> OrderStatus:
    """Return the session's order status, defaulting to `none` for missing or unknown values."""
    status = state.get("order_status") or "none"
    return status if status in ("none", "open", "confirmed", "cancelled") else "none"


def store_snapshot(state: dict[str, Any], order: Order) -> None:
    """Save a JSON snapshot of `order` in the state (returned by the HTTP API)."""
    status = status_of(state)
    snapshot = OrderSnapshot.from_order(order, "open" if status == "none" else status)
    state["order_snapshot"] = snapshot.model_dump(mode="json")
    state["address_set"] = order.delivery_address is not None
