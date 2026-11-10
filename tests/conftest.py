import asyncio
import os
import shutil
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from agno.run import RunContext

from delivery_agent.agent.sessions import SessionAccess
from delivery_agent.clients.errors import (
    ItemNotFoundError,
    MenuServiceUnavailableError,
    OrderAlreadyExistsError,
    OrderApiUnavailableError,
    OrderNotFoundError,
)
from delivery_agent.domain.models import (
    CustomerContext,
    DeliveryAddress,
    FlavorMatch,
    Menu,
    MenuQuote,
    MenuSearchHit,
    Order,
    OrderItem,
    OrderItemIn,
)
from delivery_agent.guardrails.greeting import Greeting
from delivery_agent.guardrails.intent_store import IntentLogEntry
from delivery_agent.guardrails.pii import PiiRedactor
from delivery_agent.guardrails.protocols import Greeter, IntentClassifier, IntentRecorder, Rewriter
from delivery_agent.guardrails.router import RouteDecision
from delivery_agent.guardrails.terms import TermList, TermMatcher, Violation
from menu_service.bootstrap import ensure_menu_db
from menu_service.protocols import MenuIndex
from menu_service.repository import MenuRepository
from menu_service.resolver import MenuResolver
from menu_service.service import MenuService

# Tests never send traces, whatever `.env` says. Export LANGFUSE_TRACING_ENABLED=true to trace an eval run.
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[1]
KB_SQL = ROOT / "knowledge_base" / "knowledge_base.sql"

TODAY = date(2026, 11, 6)
# CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person).
CPF = "52998224725"
NAME = "Maria Silva"


@pytest.fixture(scope="session")
def menu_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Materialize the knowledge base into a temporary SQLite file once per test session."""
    path = tmp_path_factory.mktemp("kb") / "menu.db"
    ensure_menu_db(KB_SQL, path)
    return path


@pytest.fixture
def writable_menu_db(menu_db: Path, tmp_path: Path) -> Path:
    """A private copy of the seeded menu database, for tests that change the menu."""
    path = tmp_path / "menu.db"
    shutil.copy(menu_db, path)
    return path


@pytest.fixture(scope="session")
def repository(menu_db: Path) -> MenuRepository:
    """Menu snapshot over the temporary menu database."""
    return MenuRepository.from_path(menu_db)


class FakeIndex(MenuIndex):
    """Deterministic `MenuIndex` implementation for tests: maps keywords to pizza ids and records writes."""

    def __init__(self, mapping: dict[str, list[int]] | None = None, distance: float = 0.3):
        """Map query keywords to ranked pizza ids, all at a fixed distance."""
        self.mapping = mapping or {
            "sem carne": [1, 3, 6],
            "doce": [6],
            "queijo": [3, 1],
            "cremos": [5],
            "parecidas": [4, 2, 5],
            "classicas": [1, 2, 3],
        }
        self.distance = distance
        self.queries: list[str] = []
        self.refreshed: list[set[int]] = []
        self.synced = 0
        self.fail_refresh = False

    @property
    def ready(self) -> bool:
        """The fake index is always ready."""
        return True

    def sync(self, repository: MenuRepository) -> None:
        """Count full syncs."""
        self.synced += 1

    def refresh(self, repository: MenuRepository, pizza_ids: set[int]) -> None:
        """Record the refreshed pizzas, or fail like an embedding outage when `fail_refresh` is set."""
        if self.fail_refresh:
            raise RuntimeError("embedding provider down")
        self.refreshed.append(set(pizza_ids))

    def search(self, query: str, limit: int = 3) -> list[tuple[int, float]]:
        """Return the ids mapped to the first keyword found in the query."""
        self.queries.append(query)
        normalized = query.lower().replace("á", "a")
        for keyword, ids in self.mapping.items():
            if keyword in normalized:
                return [(pizza_id, self.distance + i * 0.05) for i, pizza_id in enumerate(ids)][:limit]
        return []


@pytest.fixture
def fake_index() -> FakeIndex:
    """Fresh fake semantic index."""
    return FakeIndex()


@pytest.fixture
def resolver(repository: MenuRepository, fake_index: FakeIndex) -> MenuResolver:
    """Menu resolver using the fake semantic index."""
    return MenuResolver(repository, fake_index, semantic_max_distance=0.75)


@pytest.fixture
def menu_service(menu_db: Path, fake_index: FakeIndex) -> MenuService:
    """Menu service over the shared (read-only) menu database and the fake index."""
    return MenuService(menu_db, fake_index, semantic_max_distance=0.75)


class FakeMenuApi:
    """In-process stand-in for `MenuApiClient`: calls a real `MenuService` and round-trips the JSON contract."""

    def __init__(self, service: MenuService):
        """Wrap the service; `unavailable` simulates a Menu service outage."""
        self.service = service
        self.unavailable = False
        self.calls: list[str] = []

    def _check(self, name: str) -> None:
        """Record the call and fail when the service is marked unavailable."""
        self.calls.append(name)
        if self.unavailable:
            raise MenuServiceUnavailableError("menu service down")

    async def get_menu(self) -> Menu:
        """Same as `GET /menu`."""
        self._check("get_menu")
        return Menu.model_validate(self.service.menu().model_dump(mode="json"))

    async def resolve_flavor(self, text: str) -> FlavorMatch:
        """Same as `GET /menu/flavors/resolve`."""
        self._check("resolve_flavor")
        return FlavorMatch.model_validate(self.service.resolve_flavor(text).model_dump(mode="json"))

    async def quote(self, flavor: str, size: str, crust: str | None = None) -> MenuQuote:
        """Same as `GET /menu/quote`."""
        self._check("quote")
        return MenuQuote.model_validate(self.service.quote(flavor, size, crust).model_dump(mode="json"))

    async def search(self, query: str, limit: int = 3) -> list[MenuSearchHit]:
        """Same as `POST /menu/search`."""
        self._check("search")
        body = self.service.search(query, limit).model_dump(mode="json")
        return [MenuSearchHit.model_validate(hit) for hit in body["results"]]


@pytest.fixture
def menu_api(menu_service: MenuService) -> FakeMenuApi:
    """Fake Menu service client backed by the real service logic."""
    return FakeMenuApi(menu_service)


class FakeOrderApi:
    """In-memory Order API mirroring the real behavior (unique constraint, totals, address setattr)."""

    def __init__(self) -> None:
        """Start with no orders and an empty call log."""
        self.orders: dict[int, Order] = {}
        self._next_order = 1
        self._next_item = 1
        self.calls: list[tuple[str, Any]] = []
        self.unavailable = False

    def seed(
        self,
        delivery_date: date,
        items: list[tuple[str, int, str]],
        address: DeliveryAddress | None = None,
        name: str = NAME,
        document: str = CPF,
    ) -> Order:
        """Insert an order directly, bypassing the unique-constraint check."""
        order = Order(
            id=self._next_order,
            client_name=name,
            client_document=document,
            delivery_date=delivery_date,
            delivery_address=address,
        )
        self._next_order += 1
        self.orders[order.id] = order
        self._append(order, [OrderItemIn(name=n, quantity=q, unit_price=Decimal(p)) for n, q, p in items])
        return order

    async def _check(self) -> None:
        """Yield like a real HTTP call and fail when the fake API is marked unavailable."""
        await asyncio.sleep(0)  # yield like a real HTTP call, so concurrent tool calls interleave
        if self.unavailable:
            raise OrderApiUnavailableError("down")

    def _append(self, order: Order, items: list[OrderItemIn]) -> None:
        """Append items with new ids and recompute the total."""
        for item in items:
            order.items.append(
                OrderItem(id=self._next_item, name=item.name, quantity=item.quantity, unit_price=item.unit_price)
            )
            self._next_item += 1
        order.total_price = sum((i.unit_price * i.quantity for i in order.items), Decimal("0"))

    async def create_order(
        self,
        customer: CustomerContext,
        delivery_date: date,
        items: list[OrderItemIn],
        address: DeliveryAddress | None = None,
    ) -> Order:
        """Create an order, enforcing the one-order-per-customer-per-day constraint."""
        await self._check()
        self.calls.append(("create_order", delivery_date))
        for order in self.orders.values():
            if (order.client_name, order.client_document, order.delivery_date) == (
                customer.client_name,
                customer.client_document,
                delivery_date,
            ):
                raise OrderAlreadyExistsError({"non_field_errors": ["must make a unique set."]})
        order = self.seed(delivery_date, [], address, customer.client_name, customer.client_document)
        self._append(order, items)
        return order.model_copy(deep=True)

    async def get_order(self, order_id: int) -> Order:
        """Return a copy of an order or raise OrderNotFoundError."""
        await self._check()
        self.calls.append(("get_order", order_id))
        if order_id not in self.orders:
            raise OrderNotFoundError(str(order_id))
        return self.orders[order_id].model_copy(deep=True)

    async def find_orders(self, client_document: str, delivery_date: date | None = None) -> list[Order]:
        """Return the customer's orders, most recent first."""
        await self._check()
        self.calls.append(("find_orders", delivery_date))
        found = [
            o.model_copy(deep=True)
            for o in self.orders.values()
            if o.client_document == client_document and (delivery_date is None or o.delivery_date == delivery_date)
        ]
        return sorted(found, key=lambda o: o.id, reverse=True)

    async def add_items(self, order_id: int, items: list[OrderItemIn]) -> None:
        """Append items to an existing order."""
        await self._check()
        self.calls.append(("add_items", order_id))
        if order_id not in self.orders:
            raise OrderNotFoundError(str(order_id))
        self._append(self.orders[order_id], items)

    async def remove_item(self, order_id: int, item_id: int) -> None:
        """Delete an item or raise ItemNotFoundError."""
        await self._check()
        self.calls.append(("remove_item", item_id))
        order = self.orders.get(order_id)
        if order is None or not any(i.id == item_id for i in order.items):
            raise ItemNotFoundError(str(item_id))
        order.items = [i for i in order.items if i.id != item_id]
        order.total_price = sum((i.unit_price * i.quantity for i in order.items), Decimal("0"))

    async def update_address(self, order_id: int, address: DeliveryAddress) -> None:
        """Replace the order's address."""
        await self._check()
        self.calls.append(("update_address", order_id))
        if order_id not in self.orders:
            raise OrderNotFoundError(str(order_id))
        self.orders[order_id].delivery_address = address.model_copy()

    def call_names(self) -> list[str]:
        """Names of the calls made so far, in order."""
        return [name for name, _ in self.calls]


@pytest.fixture
def order_api() -> FakeOrderApi:
    """Fresh in-memory Order API."""
    return FakeOrderApi()


def make_run_context(state: dict[str, Any] | None = None, today: date = TODAY, name: str = NAME) -> RunContext:
    """Build a RunContext with the customer identity a real request would inject."""
    return RunContext(
        run_id="run-1",
        session_id="session-1",
        user_id=CPF,
        dependencies={"client_name": name, "client_document": CPF, "today": today.isoformat()},
        session_state=state if state is not None else {},
    )


# --- Guardrails --------------------------------------------------------------------------------------------


class FakeRouter(IntentClassifier):
    """`IntentClassifier` for tests: returns scripted intents and records the calls."""

    def __init__(self, intents: list[str] | None = None):
        """Return the scripted intents in order, then `delivery`."""
        self.intents = list(intents or [])
        self.calls: list[tuple[str, str | None, str | None]] = []
        self.first_of_day: list[bool] = []
        self.session_ids: list[str | None] = []

    async def classify(
        self,
        message: str,
        flow_step: str | None,
        last_reply: str | None,
        first_message_of_day: bool = False,
        session_id: str | None = None,
    ) -> RouteDecision:
        """Record the call and return the next scripted intent."""
        self.calls.append((message, flow_step, last_reply))
        self.session_ids.append(session_id)
        self.first_of_day.append(first_message_of_day)
        intent = self.intents.pop(0) if self.intents else "delivery"
        return RouteDecision(intent, "llm")  # type: ignore[arg-type]


class FakeRewriter(Rewriter):
    """`Rewriter` for tests: returns a fixed rewrite and records the calls."""

    def __init__(self, result: str = "Resposta revisada."):
        """Return `result` for every rewrite."""
        self.result = result
        self.calls: list[tuple[str, list[Violation]]] = []
        self.session_ids: list[str | None] = []

    async def rewrite(self, reply: str, violations: list[Violation], session_id: str | None = None) -> str:
        """Record the call and return the fixed rewrite."""
        self.calls.append((reply, violations))
        self.session_ids.append(session_id)
        return self.result


class FakeSessions(SessionAccess):
    """In-memory `SessionAccess` for tests."""

    def __init__(self, state: dict[str, Any] | None = None, last_reply: str | None = None):
        """Start with the given stored state and last reply."""
        self.stored_state = dict(state or {})
        self.stored_reply = last_reply
        self.replaced: list[str] = []
        self.turns: list[dict[str, Any]] = []

    async def state(self, session_id: str) -> dict[str, Any]:
        """Return a copy of the stored state."""
        return dict(self.stored_state)

    async def last_reply(self, session_id: str) -> str | None:
        """Return the stored last reply."""
        return self.stored_reply

    async def update_state(self, session_id: str, updates: dict[str, Any]) -> None:
        """Merge the updates into the stored state."""
        self.stored_state.update(updates)

    async def replace_last_reply(self, session_id: str, reply: str) -> None:
        """Record the replacement."""
        self.stored_reply = reply
        self.replaced.append(reply)

    async def append_turn(
        self,
        session_id: str,
        user_id: str,
        message: str,
        reply: str,
        state_updates: dict[str, Any],
        route: str,
    ) -> None:
        """Record the static turn and apply its state updates."""
        self.turns.append({"message": message, "reply": reply, "state_updates": state_updates, "route": route})
        self.stored_state.update(state_updates)
        self.stored_reply = reply


class FakeGreeter(Greeter):
    """`Greeter` for tests: returns a fixed greeting (or None) and records the calls."""

    def __init__(self, greeting: Greeting | None = None):
        """Return `greeting` for every call (None: the customer has an order today)."""
        self.greeting = greeting
        self.calls: list[tuple[CustomerContext, date]] = []

    async def greet(self, customer: CustomerContext, today: date) -> Greeting | None:
        """Record the call and return the fixed greeting."""
        self.calls.append((customer, today))
        return self.greeting


class FakeIntents(IntentRecorder):
    """In-memory `IntentRecorder` for tests; `fail` makes every call raise."""

    def __init__(self, fail: bool = False):
        """Start with an empty log."""
        self.entries: list[IntentLogEntry] = []
        self.fail = fail

    def has_message_on(self, user_id: str, day: date) -> bool:
        """Tell whether an entry exists for the customer on `day`."""
        if self.fail:
            raise RuntimeError("intent db down")
        return any(e.user_id == user_id and e.local_date == day for e in self.entries)

    def record(self, entry: IntentLogEntry) -> None:
        """Append the entry."""
        if self.fail:
            raise RuntimeError("intent db down")
        self.entries.append(entry)


class RecordedSpan:
    """A span opened through `RecordingTracer`: its name, type, input and every field set by `update`."""

    def __init__(self, name: str, as_type: str, input: Any = None, **attributes: Any):
        """Keep what the span was opened with."""
        self.name = name
        self.as_type = as_type
        self.input = input
        self.attributes = attributes
        self.fields: dict[str, Any] = {}

    def update(self, **fields: Any) -> None:
        """Merge the fields (an `input` update replaces the opening input)."""
        if "input" in fields:
            self.input = fields.pop("input")
        self.fields.update(fields)


class RecordingTracer:
    """`Tracer` for tests: records the spans opened, in order, and the trace annotations."""

    enabled = True

    def __init__(self) -> None:
        """Start with no spans."""
        self.spans: list[RecordedSpan] = []
        self.annotations: list[dict[str, Any]] = []

    @contextmanager
    def message(self, *, session_id: str, user_id: str, new_session: bool) -> Iterator[RecordedSpan]:
        """Record the root span."""
        yield self._open("handle-message", "span", session_id=session_id, user_id=user_id, new_session=new_session)

    @contextmanager
    def step(self, name: str, *, as_type: str = "span", input: Any = None) -> Iterator[RecordedSpan]:
        """Record a step span."""
        yield self._open(name, as_type, input)

    def annotate(self, *, tags: Sequence[str], metadata: Mapping[str, str]) -> None:
        """Record the trace tags and metadata."""
        self.annotations.append({"tags": list(tags), "metadata": dict(metadata)})

    def verify(self) -> bool:
        """Always valid."""
        return True

    def shutdown(self) -> None:
        """Nothing to flush."""

    def names(self) -> list[str]:
        """Names of the spans, in the order they were opened."""
        return [span.name for span in self.spans]

    def span(self, name: str) -> RecordedSpan:
        """The first span with this name."""
        return next(span for span in self.spans if span.name == name)

    def _open(self, name: str, as_type: str, input: Any = None, **attributes: Any) -> RecordedSpan:
        """Create and keep a span."""
        span = RecordedSpan(name, as_type, input, **attributes)
        self.spans.append(span)
        return span


@pytest.fixture(scope="session")
def pii_redactor() -> PiiRedactor:
    """The real Presidio redactor, loaded once per test session."""
    return PiiRedactor()


@pytest.fixture(scope="session")
def term_matcher() -> TermMatcher:
    """Matcher over the packaged terms.json."""
    return TermMatcher(TermList.load())
