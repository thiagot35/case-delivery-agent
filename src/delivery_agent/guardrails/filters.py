"""Deterministic filters: the input pre-filter (PII + blocklists) and the output term guardrail."""

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from delivery_agent.guardrails.protocols import Redactor
from delivery_agent.guardrails.terms import OUTPUT_CATEGORIES, Category, TermMatcher, Violation

BlockReason = Literal["prompt_injection", "input_hard_block"]


@dataclass(frozen=True)
class InputVerdict:
    """Result of the input pre-filter: the redacted text, the PII found and the block reason, if any."""

    text: str
    pii_found: frozenset[str] = field(default_factory=frozenset)
    blocked: BlockReason | None = None
    violations: tuple[Violation, ...] = ()


class InputFilter:
    """Masks CPF/cards, then checks the redacted text against the injection and hard-block lists."""

    def __init__(self, redactor: Redactor, matcher: TermMatcher):
        """Keep the PII redactor and the term matcher."""
        self._redactor = redactor
        self._matcher = matcher

    def check(self, message: str) -> InputVerdict:
        """Redact the message and report whether it must be blocked before reaching any model."""
        redaction = self._redactor.redact(message)
        for category in ("prompt_injection", "input_hard_block"):
            violations = self._matcher.find(redaction.text, (category,))
            if violations:
                return InputVerdict(redaction.text, redaction.found, category, tuple(violations))
        return InputVerdict(redaction.text, redaction.found)


class OutputGuardrail:
    """Checks an agent reply for profanity, competitors and undesired terms, one concurrent check per category."""

    def __init__(self, matcher: TermMatcher, categories: tuple[Category, ...] = OUTPUT_CATEGORIES):
        """Keep the matcher and the categories to check."""
        self._matcher = matcher
        self._categories = categories

    async def check(self, reply: str) -> list[Violation]:
        """Return every violation found in the reply (empty when it passes)."""
        results = await asyncio.gather(*(self._check_category(reply, category) for category in self._categories))
        return [violation for violations in results for violation in violations]

    async def _check_category(self, reply: str, category: Category) -> list[Violation]:
        """Run the check of one category."""
        return self._matcher.find(reply, (category,))
