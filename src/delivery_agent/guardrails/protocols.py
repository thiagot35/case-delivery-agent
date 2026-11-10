"""Protocols of the guardrail pipeline. Production classes and test doubles inherit them explicitly."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import date

    from delivery_agent.domain.models import CustomerContext
    from delivery_agent.guardrails.greeting import Greeting
    from delivery_agent.guardrails.handoff_store import HandoffTicket
    from delivery_agent.guardrails.intent_store import IntentLogEntry
    from delivery_agent.guardrails.redaction import Redaction
    from delivery_agent.guardrails.router import RouteDecision
    from delivery_agent.guardrails.terms import Violation


class Redactor(Protocol):
    """Masks sensitive data (CPF, credit cards) in a customer message."""

    def redact(self, text: str) -> "Redaction":
        """Return the redacted text and the PII types found."""


class IntentClassifier(Protocol):
    """Classifies a customer message as delivery, not_delivery, call_human or greeting."""

    async def classify(
        self,
        message: str,
        flow_step: str | None,
        last_reply: str | None,
        first_message_of_day: bool = False,
        session_id: str | None = None,
    ) -> "RouteDecision":
        """Return the intent of `message`, given the step, the last reply and whether it opens the day.

        `session_id` (the conversation's) ties the classification run to the conversation in traces.
        """


class Rewriter(Protocol):
    """Rewrites an agent reply that failed the output guardrails."""

    async def rewrite(self, reply: str, violations: list["Violation"], session_id: str | None = None) -> str:
        """Return the reply reworded without the violating terms (`session_id`: the conversation's)."""


class HandoffRecorder(Protocol):
    """Stores requests for a human attendant."""

    def record(self, ticket: "HandoffTicket") -> bool:
        """Save the ticket; return False if the session already has one."""


class Greeter(Protocol):
    """Builds the static greeting for the first message of the day."""

    async def greet(self, customer: "CustomerContext", today: "date") -> "Greeting | None":
        """Return the greeting, or None when the order agent must answer (the customer has an order today)."""


class IntentRecorder(Protocol):
    """Stores one log entry per customer message (`data/intent.db`)."""

    def has_message_on(self, user_id: str, day: "date") -> bool:
        """Tell whether the customer already sent a message on `day`."""

    def record(self, entry: "IntentLogEntry") -> None:
        """Save the entry."""
