"""Langfuse tracing (specs/006-langfuse-tracing): one trace per customer message.

`setup_tracing` returns the no-op tracer unless `LANGFUSE_TRACING_ENABLED` is true (the default) and both Langfuse keys
are set, so `LANGFUSE_TRACING_ENABLED=false` in `.env` turns tracing off. When on, it creates the Langfuse client (an
OpenTelemetry exporter) and instruments Agno with OpenInference: every agent run, model call (a generation with model,
tokens and cost) and tool call becomes a span. `ConversationService` opens the root span of each message
(`handle-message`) and names the pipeline steps (`filter-input`, `classify-intent`, `greet-customer`,
`register-handoff`, `check-output`, `rewrite-reply`); the Agno spans nest under them.

Privacy: every exported span goes through `mask_span_attributes` before it leaves the process. CPF-shaped numbers are
masked as in the logs (the CPF is Agno's `user_id` and appears in the `<session_state>` of the system prompt), and a
CPF in `user.id` becomes a keyed pseudonym (`customer-<hmac>`), so Langfuse groups a customer's traces without ever
storing the CPF. If the masking hook raises, the SDK drops the batch instead of exporting it unmasked.
"""

import hashlib
import hmac
import logging
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Literal, Protocol

from delivery_agent import __version__
from delivery_agent.config import Settings
from delivery_agent.logging_config import mask_cpfs_in_text

if TYPE_CHECKING:
    from langfuse import Langfuse
    from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult

logger = logging.getLogger(__name__)

ObservationType = Literal["span", "agent", "tool", "chain", "retriever", "evaluator", "guardrail"]

# Root observation of every customer message: also the trace name. Treat names as an API (filters, evaluators).
MESSAGE_SPAN = "handle-message"
# OpenTelemetry `service.name` of the exported spans (unless OTEL_SERVICE_NAME is already set).
SERVICE_NAME = "beauty-pizza-delivery-agent"
# Trace user id attribute, shared by Langfuse and OpenInference (the Agno instrumentor writes the raw `user_id` there).
USER_ID_ATTRIBUTE = "user.id"


class Observation(Protocol):
    """An open span; `update` sets its input, output, metadata, level or status message."""

    def update(self, **fields: Any) -> Any:
        """Set fields of the span."""
        ...


class Tracer(Protocol):
    """What the conversation pipeline needs from tracing (Langfuse, or the no-op tracer)."""

    enabled: bool

    def message(self, *, session_id: str, user_id: str, new_session: bool) -> AbstractContextManager[Observation]:
        """Open the root span of one customer message; trace attributes propagate to every span inside it."""
        ...

    def step(
        self, name: str, *, as_type: ObservationType = "span", input: Any = None
    ) -> AbstractContextManager[Observation]:
        """Open a child span for one pipeline step."""
        ...

    def annotate(self, *, tags: Sequence[str], metadata: Mapping[str, str]) -> None:
        """Add tags and metadata to the trace of the current message (call it inside `message`)."""
        ...

    def verify(self) -> bool:
        """Check the credentials and the base URL against the server."""
        ...

    def shutdown(self) -> None:
        """Flush the buffered spans and stop the exporter."""
        ...


class _NoopObservation:
    """Span stand-in when tracing is off."""

    def update(self, **fields: Any) -> None:
        """Ignore the fields."""


_NOOP_OBSERVATION = _NoopObservation()


class NoopTracer:
    """Tracing off: every call does nothing (the default, and in tests)."""

    enabled = False

    @contextmanager
    def message(self, *, session_id: str, user_id: str, new_session: bool) -> Iterator[Observation]:
        """Yield a span stand-in."""
        yield _NOOP_OBSERVATION

    @contextmanager
    def step(self, name: str, *, as_type: ObservationType = "span", input: Any = None) -> Iterator[Observation]:
        """Yield a span stand-in."""
        yield _NOOP_OBSERVATION

    def annotate(self, *, tags: Sequence[str], metadata: Mapping[str, str]) -> None:
        """Do nothing."""

    def verify(self) -> bool:
        """Nothing to verify."""
        return True

    def shutdown(self) -> None:
        """Nothing to flush."""


class LangfuseTracer:
    """Tracer backed by the Langfuse SDK (OpenTelemetry); the Agno instrumentor, if any, is removed on shutdown."""

    enabled = True

    def __init__(
        self,
        client: "Langfuse",
        pseudonymize: Callable[[str], str],
        version: str | None = None,
        instrumentor: Any = None,
    ):
        """Keep the client, the user-id pseudonymizer, the version stamped on every span and the instrumentor."""
        self._client = client
        self._pseudonymize = pseudonymize
        self._version = version
        self._instrumentor = instrumentor

    @contextmanager
    def message(self, *, session_id: str, user_id: str, new_session: bool) -> Iterator[Observation]:
        """Root span `handle-message` with the session, the pseudonymous user id and the prompts version."""
        from langfuse import propagate_attributes

        with (
            self._client.start_as_current_observation(name=MESSAGE_SPAN, as_type="span") as root,
            propagate_attributes(
                user_id=self._pseudonymize(user_id),
                session_id=session_id,
                version=self._version,
                metadata={"new_session": str(new_session).lower()},
            ),
        ):
            yield root

    @contextmanager
    def step(self, name: str, *, as_type: ObservationType = "span", input: Any = None) -> Iterator[Observation]:
        """Child span of the active observation; an exception inside marks it as an error."""
        with self._client.start_as_current_observation(name=name, as_type=as_type, input=input) as observation:
            yield observation

    def annotate(self, *, tags: Sequence[str], metadata: Mapping[str, str]) -> None:
        """Set tags and metadata on the active (root) span, which makes them trace attributes."""
        from langfuse import propagate_attributes

        with propagate_attributes(tags=list(tags), metadata=dict(metadata)):
            pass

    def verify(self) -> bool:
        """`auth_check` against the server; a network error counts as a failure."""
        try:
            return bool(self._client.auth_check())
        except Exception:
            logger.warning("tracing auth check failed", exc_info=True)
            return False

    def shutdown(self) -> None:
        """Remove the Agno instrumentation, then flush and stop the Langfuse exporter."""
        if self._instrumentor is not None:
            self._instrumentor.uninstrument()
        self._client.shutdown()


def pseudonymous_user_id(document: str, key: bytes) -> str:
    """Stable, keyed pseudonym of a CPF (HMAC-SHA256 of its digits): a plain hash of 11 digits could be reversed."""
    digits = re.sub(r"\D", "", document)
    return f"customer-{hmac.new(key, digits.encode('ascii'), hashlib.sha256).hexdigest()[:16]}"


def _mask_value(value: Any) -> Any:
    """Mask CPFs in a string or in the strings of a sequence attribute; other values are returned as they are."""
    if isinstance(value, str):
        return mask_cpfs_in_text(value)
    if isinstance(value, list | tuple):
        return tuple(mask_cpfs_in_text(item) if isinstance(item, str) else item for item in value)
    return value


def mask_span_attributes(attributes: Mapping[str, Any], pseudonymize: Callable[[str], str]) -> dict[str, Any]:
    """Return only the attributes to change: CPFs masked everywhere, a CPF in `user.id` replaced by its pseudonym."""
    changes: dict[str, Any] = {}
    for key, value in attributes.items():
        masked = _mask_value(value)
        if masked == (tuple(value) if isinstance(value, list | tuple) else value):
            continue
        changes[key] = pseudonymize(value) if key == USER_ID_ATTRIBUTE and isinstance(value, str) else masked
    return changes


def build_span_masker(
    pseudonymize: Callable[[str], str],
) -> Callable[..., "MaskOtelSpansResult"]:
    """Build the SDK's `mask_otel_spans` hook: runs on every export batch, Langfuse and Agno spans alike."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    def mask_otel_spans(*, params: "MaskOtelSpansParams") -> MaskOtelSpansResult:
        """Patch the spans of one export batch that carry a CPF."""
        patches = {}
        for identifier, span in params.spans.items():
            changes = mask_span_attributes(span.attributes or {}, pseudonymize)
            if changes:
                patches[identifier] = OtelSpanPatch(set_attributes=changes)
        return MaskOtelSpansResult(span_patches=patches)

    return mask_otel_spans


def build_langfuse_client(settings: Settings, pseudonymize: Callable[[str], str], **overrides: Any) -> "Langfuse":
    """Create the Langfuse client from the settings (`overrides`: e.g. an in-memory exporter in tests)."""
    from langfuse import Langfuse

    secret_key = settings.langfuse_secret_key.get_secret_value() if settings.langfuse_secret_key else None
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=secret_key,
        base_url=settings.langfuse_base_url,
        tracing_enabled=True,
        environment=settings.langfuse_tracing_environment,
        release=__version__,
        sample_rate=settings.langfuse_sample_rate,
        mask_otel_spans=build_span_masker(pseudonymize),
        **overrides,
    )


def setup_tracing(settings: Settings, version: str | None = None) -> Tracer:
    """Return the Langfuse tracer (client + Agno instrumentation) when tracing is configured, else the no-op tracer."""
    if not settings.langfuse_tracing_enabled:
        logger.info("tracing off (LANGFUSE_TRACING_ENABLED=false)")
        return NoopTracer()
    if not settings.langfuse_configured():
        logger.info("tracing off (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set)")
        return NoopTracer()

    from openinference.instrumentation.agno import AgnoInstrumentor

    secret = settings.langfuse_secret_key.get_secret_value().encode("utf-8")  # type: ignore[union-attr]

    def pseudonymize(document: str) -> str:
        """Pseudonym keyed by the project's secret key."""
        return pseudonymous_user_id(document, secret)

    # The OpenTelemetry resource reads it when the client creates the tracer provider (else "unknown_service").
    os.environ.setdefault("OTEL_SERVICE_NAME", SERVICE_NAME)
    # The client registers the global tracer provider: instrument Agno after it so the Agno spans reach Langfuse.
    client = build_langfuse_client(settings, pseudonymize)
    instrumentor = AgnoInstrumentor()
    instrumentor.instrument()
    logger.info(
        "tracing on: Langfuse base_url=%s environment=%s sample_rate=%s version=%s",
        settings.langfuse_base_url,
        settings.langfuse_tracing_environment,
        settings.langfuse_sample_rate,
        version,
    )
    return LangfuseTracer(client, pseudonymize, version=version, instrumentor=instrumentor)
