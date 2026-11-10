"""Conversation orchestration: per-customer locking, session resolution, guardrails and agent execution.

Pipeline (specs/003-guardrails, specs/004-greeting): input pre-filter (PII + blocklists) → intent router (with the
first-message-of-the-day flag) → static reply (greeting, off-topic, handoff, blocked) or the order agent (bounded
tool loop) → output guardrails with one rewrite. Static replies are stored in the agent's history, and every message
is logged in the intent log. Each message is one trace (specs/006-langfuse-tracing): the root span `handle-message`
with a span per step; only the redacted text is traced. Each step also logs an orange `step=<name>` line.
"""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from agno.run.base import RunStatus

from delivery_agent.agent.runner import AgentRunner
from delivery_agent.agent.session_resolver import SessionResolver
from delivery_agent.agent.sessions import AgnoSessionAccess, SessionAccess
from delivery_agent.api.schemas import MessageRequest, MessageResponse, Route
from delivery_agent.domain.cpf import mask_cpf
from delivery_agent.domain.models import CustomerContext, OrderSnapshot
from delivery_agent.domain.special_dates import SpecialDateCalendar, resolve_city
from delivery_agent.guardrails import messages
from delivery_agent.guardrails.filters import InputFilter, InputVerdict, OutputGuardrail
from delivery_agent.guardrails.handoff_store import HandoffTicket
from delivery_agent.guardrails.intent_store import IntentLogEntry
from delivery_agent.guardrails.protocols import Greeter, HandoffRecorder, IntentClassifier, IntentRecorder, Rewriter
from delivery_agent.guardrails.terms import Violation
from delivery_agent.logging_config import STEP_HIGHLIGHT, log_context
from delivery_agent.tracing import NoopTracer, Observation, ObservationType, Tracer

__all__ = ["AgentRunner", "AgentUnavailableError", "ConversationService", "Guardrails"]

logger = logging.getLogger(__name__)


class AgentUnavailableError(Exception):
    """The agent run failed (model provider error)."""


@dataclass
class Guardrails:
    """The guardrail components of the pipeline."""

    input_filter: InputFilter
    router: IntentClassifier
    output: OutputGuardrail
    rewriter: Rewriter
    handoff: HandoffRecorder
    greeter: Greeter
    intents: IntentRecorder
    handoff_threshold: int = 3


@dataclass(frozen=True)
class _Turn:
    """How a message was answered, before the PII notice is appended.

    `stored_message` is the customer's side of a static turn, stored in the agent's history with the reply; it is
    None when the order agent answered (Agno stores its own runs).
    """

    reply: str
    route: Route
    state: dict[str, Any]
    intent: str | None
    method: str
    reason: str = ""
    stored_message: str | None = None
    state_updates: dict[str, Any] = field(default_factory=dict)
    customer_type: str | None = None


class ConversationService:
    def __init__(
        self,
        order_agent: AgentRunner,
        resolver: SessionResolver,
        today: Callable[[], date],
        guardrails: Guardrails,
        sessions: SessionAccess | None = None,
        special_dates: SpecialDateCalendar | None = None,
        tracer: Tracer | None = None,
    ):
        """Keep the order agent, the session resolver, the date provider, the guardrails, the special-dates
        calendar (None: no dates), the tracer (None: tracing off) and the per-customer locks."""
        self._order_agent = order_agent
        self._resolver = resolver
        self._today = today
        self._guardrails = guardrails
        self._sessions = sessions or AgnoSessionAccess(order_agent)
        self._special_dates = special_dates
        self._tracer: Tracer = tracer or NoopTracer()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def handle(self, request: MessageRequest) -> MessageResponse:
        """Process one message: lock the customer, resolve the session, run the pipeline, build the response."""
        started = time.perf_counter()
        customer = CustomerContext(client_name=request.client_name, client_document=request.client_document)
        # Serialize messages of the same customer: avoids two concurrent order creations (NFR-006).
        async with self._locks[customer.client_document]:
            session_id, is_new = await asyncio.to_thread(self._resolver.resolve, customer.client_document)
            logger.info(
                "message customer=%s session=%s new_session=%s",
                mask_cpf(customer.client_document),
                session_id,
                is_new,
            )
            with (
                log_context(session_id),  # tags every line of this run, including Agno's and the tools'
                self._tracer.message(
                    session_id=session_id, user_id=customer.client_document, new_session=is_new
                ) as trace,
            ):
                today = self._today()
                verdict = self._filter_input(request.message)
                trace.update(input=verdict.text)  # the redacted text: the raw message may carry PII
                first_of_day = not await self._seen_today(customer.client_document, today)
                state = await self._state(session_id, is_new)
                turn = await self._answer(customer, session_id, is_new, verdict, state, first_of_day, request.location)

                reply = turn.reply
                if verdict.pii_found:
                    reply = f"{reply}\n\n{messages.pii_notice(verdict.pii_found)}"
                if turn.stored_message is not None:
                    await self._sessions.append_turn(
                        session_id, customer.client_document, turn.stored_message, reply, turn.state_updates, turn.route
                    )
                logger.info("route=%s first_message_of_day=%s", turn.route, first_of_day)
                trace.update(output=reply)
                self._tracer.annotate(tags=[turn.route], metadata=_trace_metadata(turn, state, first_of_day))
                await self._log_intent(
                    IntentLogEntry(
                        local_date=today,
                        user_id=customer.client_document,
                        session_id=session_id,
                        new_session=is_new,
                        message=verdict.text,
                        intent=turn.intent,
                        method=turn.method,
                        reason=turn.reason,
                        route=turn.route,
                        first_message_of_day=first_of_day,
                        flow_step=state.get("flow_step"),
                        customer_type=turn.customer_type,
                        pii_found=tuple(sorted(verdict.pii_found)),
                        human_requests=int(turn.state.get("human_requests") or 0),
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                )

        snapshot = turn.state.get("order_snapshot")
        return MessageResponse(
            reply=reply,
            session_id=session_id,
            new_session=is_new,
            route=turn.route,
            order=OrderSnapshot.model_validate(snapshot) if snapshot else None,
        )

    async def _answer(
        self,
        customer: CustomerContext,
        session_id: str,
        is_new: bool,
        verdict: InputVerdict,
        state: dict[str, Any],
        first_of_day: bool,
        location: str | None = None,
    ) -> _Turn:
        """Route the (redacted) message to a static reply, a human handoff or the order agent."""
        name = customer.first_name
        if verdict.blocked:
            logger.warning("guardrail input blocked=%s terms=%s", verdict.blocked, [v.term for v in verdict.violations])
            # The blocked text never enters the agent's history: a placeholder keeps the turn's context.
            return _Turn(
                messages.blocked(name),
                "blocked",
                state,
                intent=None,
                method="input_filter",
                reason=verdict.blocked,
                stored_message=messages.BLOCKED_PLACEHOLDER,
            )

        last_reply = None if is_new else await self._sessions.last_reply(session_id)
        router = self._guardrails.router
        with self._step(
            "classify-intent",
            as_type="chain",
            input={"message": verdict.text, "flow_step": state.get("flow_step"), "first_message_of_day": first_of_day},
        ) as step:
            decision = await router.classify(
                verdict.text, state.get("flow_step"), last_reply, first_of_day, session_id=session_id
            )
            step.update(output={"intent": decision.intent, "method": decision.method, "reason": decision.reason})
        human_requests = int(state.get("human_requests") or 0)
        if decision.intent == "call_human":
            human_requests += 1
        logger.info("intent=%s method=%s human_requests=%d", decision.intent, decision.method, human_requests)
        routed = {"intent": decision.intent, "method": decision.method, "reason": decision.reason}

        if decision.intent == "greeting":
            with self._step("greet-customer") as step:
                greeting = await self._guardrails.greeter.greet(customer, self._today())
                if greeting is None:
                    step.update(output={"skipped": "the customer has an order today"})
                else:
                    step.update(output={"customer_type": greeting.customer_type, "reply": greeting.reply})
            if greeting is not None:
                reply = greeting.reply
                if await self._check_output(reply):  # flavor names come from data: check them too
                    logger.warning("guardrail output_violation in the greeting, sending it without flavors")
                    reply = messages.greeting_new(name, [])
                logger.info("greeting customer_type=%s", greeting.customer_type)
                return _Turn(
                    reply,
                    "greeting",
                    {**state, **greeting.state_updates},
                    **routed,
                    stored_message=verdict.text,
                    state_updates=greeting.state_updates,
                    customer_type=greeting.customer_type,
                )
            logger.info("greeting skipped: the customer has an order today")

        if decision.intent == "not_delivery":
            return _Turn(messages.not_delivery(name), "not_delivery", state, **routed, stored_message=verdict.text)
        if decision.intent == "call_human" and human_requests >= self._guardrails.handoff_threshold:
            ticket = HandoffTicket(
                session_id=session_id,
                client_name=customer.client_name,
                client_document=customer.client_document,
                order_id=state.get("order_id"),
                request_count=human_requests,
                last_message=verdict.text,
            )
            with self._step("register-handoff", input={"request_count": human_requests}) as step:
                created = await asyncio.to_thread(self._guardrails.handoff.record, ticket)
                step.update(output={"created": created})
            logger.info("human_handoff created=%s count=%d", created, human_requests)
            updates = {"human_requests": human_requests}
            return _Turn(
                messages.human_handoff(name),
                "human_handoff",
                {**state, **updates},
                **routed,
                stored_message=verdict.text,
                state_updates=updates,
            )

        # The agent run is traced by the Agno instrumentation (no span here), but it is a step in the logs too.
        logger.info("step=order-agent", extra=STEP_HIGHLIGHT)
        run = await self._order_agent.arun(
            verdict.text,
            user_id=customer.client_document,
            session_id=session_id,
            session_state={
                "customer_first_name": name,
                "human_requests": human_requests,
                **self._special_dates_state(state, location),
            },
            dependencies={
                "client_name": customer.client_name,
                "client_document": customer.client_document,
                "today": self._today().isoformat(),
            },
        )
        if getattr(run, "status", None) == RunStatus.error or not getattr(run, "content", None):
            logger.error("Agent run failed: status=%s", getattr(run, "status", None))
            raise AgentUnavailableError("Agent run failed")

        reply = await self._guard_reply(str(run.content), session_id, name)
        return _Turn(reply, "agent", getattr(run, "session_state", None) or {}, **routed)

    async def _guard_reply(self, draft: str, session_id: str, first_name: str) -> str:
        """Check the reply; on failure rewrite it once with a critique, else ask the customer to rephrase."""
        violations = await self._check_output(draft)
        if not violations:
            return draft
        logger.warning("guardrail output_violation %s", [(v.category, v.term) for v in violations])
        with self._step(
            "rewrite-reply", as_type="chain", input={"draft": draft, "violations": _violations(violations)}
        ) as step:
            try:
                rewritten = await self._guardrails.rewriter.rewrite(draft, violations, session_id=session_id)
            except Exception:
                logger.warning("guardrail rewrite failed", exc_info=True)
                rewritten = ""
            accepted = bool(rewritten) and not await self._check_output(rewritten)
            step.update(output=rewritten or None, metadata={"accepted": accepted})
        if accepted:
            logger.info("guardrail rewrite=ok")
            final = rewritten
        else:
            logger.warning("guardrail rewrite=failed, asking the customer to rephrase")
            final = messages.rephrase(first_name)
        # Keep the agent's history in step with what the customer actually received.
        await self._sessions.replace_last_reply(session_id, final)
        return final

    def _step(
        self, name: str, *, as_type: ObservationType = "span", input: Any = None
    ) -> AbstractContextManager[Observation]:
        """Open a pipeline step: one orange `step=<name>` log line, then its tracing span."""
        logger.info("step=%s", name, extra=STEP_HIGHLIGHT)
        return self._tracer.step(name, as_type=as_type, input=input)

    def _filter_input(self, message: str) -> InputVerdict:
        """Run the input pre-filter in a guardrail span; the span gets the verdict, never the raw message."""
        with self._step("filter-input", as_type="guardrail") as step:
            verdict = self._guardrails.input_filter.check(message)
            step.update(
                output={
                    "blocked": verdict.blocked,
                    "pii_found": sorted(verdict.pii_found),
                    "violations": _violations(verdict.violations),
                },
                level="WARNING" if verdict.blocked else "DEFAULT",
            )
        if verdict.pii_found:
            logger.info("guardrail input pii=%s", sorted(verdict.pii_found))
        return verdict

    async def _check_output(self, reply: str) -> list[Violation]:
        """Run the output guardrail in a guardrail span (level WARNING when it finds violations)."""
        with self._step("check-output", as_type="guardrail", input=reply) as step:
            violations = await self._guardrails.output.check(reply)
            step.update(output={"violations": _violations(violations)}, level="WARNING" if violations else "DEFAULT")
        return violations

    def _special_dates_state(self, state: dict[str, Any], location: str | None) -> dict[str, Any]:
        """Upcoming dates for the first agent reply of the session; later replies get none (run state wins)."""
        if state.get("special_dates_sent"):
            return {"upcoming_dates": []}
        upcoming = self._special_dates.upcoming(self._today(), location) if self._special_dates else []
        logger.info("special_dates city=%s upcoming=%s", resolve_city(location), [d.name for d in upcoming])
        return {"upcoming_dates": [d.as_state() for d in upcoming], "special_dates_sent": True}

    async def _state(self, session_id: str, is_new: bool) -> dict[str, Any]:
        """Stored session state, or empty for a new session."""
        return {} if is_new else await self._sessions.state(session_id)

    async def _seen_today(self, user_id: str, today: date) -> bool:
        """Tell whether the customer already wrote today; a failing intent log counts as "already wrote"."""
        try:
            return await asyncio.to_thread(self._guardrails.intents.has_message_on, user_id, today)
        except Exception:
            logger.warning("intent log unavailable, treating the message as not the first of the day", exc_info=True)
            return True

    async def _log_intent(self, entry: IntentLogEntry) -> None:
        """Record the message in the intent log; a failure never breaks the reply."""
        try:
            await asyncio.to_thread(self._guardrails.intents.record, entry)
        except Exception:
            logger.warning("intent log write failed", exc_info=True)


def _violations(violations: Iterable[Violation]) -> list[dict[str, str]]:
    """Guardrail violations as trace data: category and matched term."""
    return [{"category": v.category, "term": v.term} for v in violations]


def _trace_metadata(turn: _Turn, state: dict[str, Any], first_of_day: bool) -> dict[str, str]:
    """Trace-level metadata of a message: short string values, filterable in Langfuse."""
    metadata = {
        "route": turn.route,
        "intent": turn.intent or "none",
        "method": turn.method,
        "first_message_of_day": str(first_of_day).lower(),
        "flow_step": str(state.get("flow_step") or "none"),
    }
    if turn.customer_type:
        metadata["customer_type"] = turn.customer_type
    return metadata
