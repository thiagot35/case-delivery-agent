"""Langfuse tracing (specs/006-langfuse-tracing): on/off switch, CPF masking and pseudonymous user ids."""

import uuid
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from delivery_agent.agent.factory import prompts_version
from delivery_agent.config import Settings
from delivery_agent.tracing import (
    LangfuseTracer,
    NoopTracer,
    build_langfuse_client,
    mask_span_attributes,
    pseudonymous_user_id,
    setup_tracing,
)

# CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person).
CPF = "52998224725"
KEY = b"secret-key"


def pseudonymize(document: str) -> str:
    """Pseudonymizer of the tests."""
    return pseudonymous_user_id(document, KEY)


def settings(**overrides: Any) -> Settings:
    """Settings that ignore `.env` and the environment's Langfuse keys."""
    values: dict[str, Any] = {
        "langfuse_tracing_enabled": True,
        "langfuse_public_key": None,
        "langfuse_secret_key": None,
    }
    return Settings(_env_file=None, **{**values, **overrides})  # type: ignore[call-arg]


def test_pseudonym_is_stable_keyed_and_hides_the_cpf() -> None:
    """Same CPF (any punctuation) → same pseudonym; another key → another one; the digits never show."""
    pseudonym = pseudonymous_user_id(CPF, KEY)
    assert pseudonym == pseudonymous_user_id("529.982.247-25", KEY)
    assert pseudonym != pseudonymous_user_id(CPF, b"other-key")
    assert pseudonym.startswith("customer-") and len(pseudonym) == len("customer-") + 16
    assert "2247" not in pseudonym and "4725" not in pseudonym


def test_mask_changes_only_what_carries_a_cpf() -> None:
    """CPFs are masked in strings and sequences; a CPF `user.id` becomes the pseudonym; the rest is untouched."""
    attributes = {
        "input.value": "meu cpf é 529.982.247-25",
        "llm.input_messages.0.message.content": '<session_state>{"current_user_id": "52998224725"}</session_state>',
        "user.id": CPF,
        "tags": ("agent", CPF),
        "llm.model_name": "gpt-6-luna",
        "llm.token_count.total": 120,
        "session.id": "6f1d2c3e-1111-2222-3333-444455556666",
    }
    assert mask_span_attributes(attributes, pseudonymize) == {
        "input.value": "meu cpf é ***.***.*47-25",
        "llm.input_messages.0.message.content": '<session_state>{"current_user_id": "***.***.*47-25"}</session_state>',
        "user.id": pseudonymize(CPF),
        "tags": ("agent", "***.***.*47-25"),
    }


def test_mask_keeps_an_already_pseudonymous_user_id() -> None:
    """The user id set by the pipeline is not re-hashed."""
    assert mask_span_attributes({"user.id": pseudonymize(CPF)}, pseudonymize) == {}


def test_tracing_off_when_disabled() -> None:
    """`LANGFUSE_TRACING_ENABLED=false` wins over configured keys."""
    tracer = setup_tracing(settings(langfuse_tracing_enabled=False, langfuse_public_key="pk", langfuse_secret_key="sk"))
    assert isinstance(tracer, NoopTracer) and tracer.enabled is False


@pytest.mark.parametrize(("public_key", "secret_key"), [(None, None), ("pk-lf-1", None), ("", ""), ("pk-lf-1", "")])
def test_tracing_off_without_both_keys(public_key: str | None, secret_key: str | None) -> None:
    """Missing or blank keys keep tracing off (no client, no network)."""
    configured = settings(langfuse_public_key=public_key, langfuse_secret_key=secret_key)
    assert configured.langfuse_configured() is False
    assert isinstance(setup_tracing(configured), NoopTracer)


def test_tracing_switch_reads_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The switch and the keys come from the same variables as the Langfuse SDK's."""
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-1")
    loaded = Settings(_env_file=None)  # type: ignore[call-arg]
    assert loaded.langfuse_tracing_enabled is False and loaded.langfuse_configured() is False
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    assert Settings(_env_file=None).langfuse_configured() is True  # type: ignore[call-arg]


def test_noop_tracer_accepts_every_call() -> None:
    """With tracing off the pipeline's calls are harmless."""
    tracer = NoopTracer()
    with tracer.message(session_id="s", user_id=CPF, new_session=True) as root:
        root.update(input="oi", output="olá")
        with tracer.step("classify-intent", as_type="chain", input={"message": "oi"}) as step:
            step.update(output={"intent": "delivery"})
        tracer.annotate(tags=["agent"], metadata={"route": "agent"})
    assert tracer.verify() is True
    tracer.shutdown()


def test_prompts_version_is_a_short_stable_hash() -> None:
    """The version stamped on traces changes only with the prompts."""
    assert prompts_version() == prompts_version()
    assert prompts_version().startswith("prompts-") and len(prompts_version()) == len("prompts-") + 10


def _export(tracer: LangfuseTracer, provider: TracerProvider, exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    """Run a message with a step and an Agno-like span, flush, and return the exported spans."""
    with tracer.message(session_id="session-1", user_id=CPF, new_session=True) as root:
        root.update(input="meu cpf é 529.982.247-25")
        with tracer.step("classify-intent", as_type="chain", input={"message": "oi"}) as step:
            step.update(output={"intent": "delivery"})
        agno = provider.get_tracer("openinference.instrumentation.agno")
        attributes = {"openinference.span.kind": "AGENT", "user.id": CPF, "input.value": f"cliente {CPF}"}
        with agno.start_as_current_span("Beauty Pizza Attendant.arun", attributes=attributes):
            pass
        root.update(output="Olá!")
        tracer.annotate(tags=["agent"], metadata={"route": "agent"})
    tracer._client.flush()
    return list(exporter.get_finished_spans())


def test_exported_spans_are_masked_and_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real SDK: names, nesting, trace attributes, and no CPF in any exported attribute."""
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")  # the SDK also reads it; conftest turns it off
    exporter, provider = InMemorySpanExporter(), TracerProvider()
    configured = settings(langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}", langfuse_secret_key="sk-lf-test")
    client = build_langfuse_client(configured, pseudonymize, span_exporter=exporter, tracer_provider=provider)
    tracer = LangfuseTracer(client, pseudonymize, version="prompts-test")
    try:
        spans = {span.name: span for span in _export(tracer, provider, exporter)}
    finally:
        tracer.shutdown()

    assert set(spans) == {"handle-message", "classify-intent", "Beauty Pizza Attendant.arun"}
    root = spans["handle-message"]
    for child in ("classify-intent", "Beauty Pizza Attendant.arun"):
        assert spans[child].parent is not None and spans[child].parent.span_id == root.context.span_id
    for span in spans.values():
        exported = dict(span.attributes or {})
        assert CPF not in str(exported) and "529.982.247-25" not in str(exported)
        assert exported["user.id"] == pseudonymize(CPF)
        assert exported["session.id"] == "session-1"
        assert exported["langfuse.version"] == "prompts-test"
    assert "***.***.*47-25" in str(root.attributes)
    assert "***.***.*47-25" in str(spans["Beauty Pizza Attendant.arun"].attributes)
    assert tuple(root.attributes["langfuse.trace.tags"]) == ("agent",)  # type: ignore[index]
    assert root.attributes["langfuse.trace.metadata.route"] == '"agent"'  # type: ignore[index]  # JSON-encoded
