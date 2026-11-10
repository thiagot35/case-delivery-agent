# Feature Spec: Langfuse Tracing

| Field     | Value                                                    |
|-----------|----------------------------------------------------------|
| Feature   | 006-langfuse-tracing                                     |
| Status    | Implemented                                              |
| Depends   | [003-guardrails](../003-guardrails/spec.md), [004-greeting](../004-greeting/spec.md) |
| Plan      | [plan.md](plan.md) · [tasks.md](tasks.md)                |

## Context

The logs show what happened inside one process, but not what each conversation cost, which step was slow, or what the
model saw when it chose a tool. Langfuse adds this per message: a trace with the router, agent, model, tool and
guardrail steps, grouped by conversation. It was set up with the Langfuse agent skill
(`.claude/skills/langfuse`, from github.com/langfuse/skills) and its instrumentation guide.

## Requirements (from the request)

| # | Requirement |
|---|---|
| R1 | Add Langfuse tracing to the application, following Langfuse best practices. |
| R2 | Tracing can be turned off through `.env`. |

## Decisions

| Topic | Decision |
|---|---|
| Switch | `LANGFUSE_TRACING_ENABLED` (default `true`, the Langfuse SDK's own variable). Tracing runs only when it is true **and** `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set. Otherwise the no-op tracer is used: no client, no instrumentation, no network. The values are passed to the client explicitly, because `.env` is read by pydantic-settings and is not exported to `os.environ`. |
| Agno integration | `openinference-instrumentation-agno`: agent runs, model calls (generations with model, tokens, cost) and tool calls become spans automatically. OpenLIT (the Langfuse docs' Agno example) pins `openai<3` and conflicts with this project's `openai>=3.46`. OpenInference spans pass Langfuse's default export filter. |
| Trace scope | One trace per customer message (`handle-message`). Langfuse Sessions group the conversation by the Agno `session_id`. |
| Steps | `filter-input` (guardrail), `classify-intent` (chain, wraps the router agent), `greet-customer`, `register-handoff`, the order agent (instrumented), `check-output` (guardrail, WARNING when it finds a violation) and `rewrite-reply` (chain, wraps the rewriter agent). |
| Trace input and output | The input is the **redacted** customer message. The raw text never reaches a span. The output is the reply sent. |
| Trace attributes | `session_id`, `user_id` (pseudonym), `version` = a hash of the three agent prompts (`prompts-<sha256[:10]>`), `release` = the package version, `environment` = `LANGFUSE_TRACING_ENVIRONMENT`. The tag is the route. The metadata is `route`, `intent`, `method`, `first_message_of_day`, `flow_step`, `customer_type` and `new_session`. |
| CPF | Masked at export by the SDK's `mask_otel_spans` hook, with the same regex as the logs. This covers every string attribute of every span, Agno's included. The CPF is Agno's `user_id` and appears in the `<session_state>` of the system prompt. If the hook raises, the SDK drops the batch, so it fails closed. |
| User id | `customer-<HMAC-SHA256(secret key, CPF digits)[:16]>`. It is stable per customer and can't be reversed without the project's secret key. A plain hash of 11 digits could be brute-forced. The Agno instrumentor writes the raw CPF to `user.id`, and the hook replaces it with the same pseudonym. |
| Helper agents and sessions | The router and rewriter runs get the conversation's `session_id`. Without it, Agno makes one up and keeps it on the shared agent, and the instrumentor writes it to `session.id`. That would put every customer's router calls in one fake Langfuse session. This was found in the trace audit. |
| HTTP spans | FastAPI emits its own OpenTelemetry spans. The default export filter drops them, and Langfuse marks `handle-message` as the app root. |
| Tests | `tests/conftest.py` defaults `LANGFUSE_TRACING_ENABLED=false`, so unit tests and evals never send traces. Export `LANGFUSE_TRACING_ENABLED=true` to trace an eval run. |

## Edge cases

| Case | Behavior |
|---|---|
| Wrong keys or region | At startup `auth_check` fails, a warning is logged, and the app keeps working. The spans are dropped by the server. |
| Langfuse unreachable | The export runs on a background thread, so replies are never delayed or broken. |
| Agent error (503) | The root span ends with level ERROR and the exception. |
| Exception events | `mask_otel_spans` can patch attributes only, not span events. The app's exception messages don't carry CPFs. Tool errors are returned to the model as results, which are attributes and get masked. |
| Names and addresses | Not masked: the same policy as the logs. Tool outputs (Order API) carry the customer's first name and delivery address. |

## Functional requirements

| ID | Requirement |
|---|---|
| FR-601 | `setup_tracing` returns the Langfuse tracer only when `LANGFUSE_TRACING_ENABLED` is true and both keys are set. Otherwise it returns the no-op tracer. |
| FR-602 | Each message is one trace, `handle-message`, with the steps above. Its input is the redacted text and its output is the reply. Its tag is the route. |
| FR-603 | No exported span attribute contains a CPF. `user.id` is the keyed pseudonym on every span. |
| FR-604 | All the spans of a message share the conversation's `session.id`, the helper agents' included. |
| FR-605 | The client is flushed and shut down, and the Agno instrumentation removed, when the app stops. |

## Traceability

| Req | Implementation | Tests |
|---|---|---|
| R2, FR-601 | `tracing.setup_tracing`, `config.py` | `test_tracing.py::test_tracing_off_when_disabled`, `test_tracing.py::test_tracing_off_without_both_keys`, `test_tracing.py::test_tracing_switch_reads_the_env` |
| R1, FR-602 | `ConversationService` (spans), `LangfuseTracer` | `test_api.py::test_agent_turn_is_one_trace_with_named_steps`, `test_api.py::test_trace_gets_only_the_redacted_message`, `test_api.py::test_blocked_message_trace_has_a_warning_guardrail`, `test_api.py::test_greeting_trace_has_the_greeter_step`, `test_api.py::test_rewrite_is_traced_with_both_output_checks` |
| FR-603 | `tracing.mask_span_attributes`, `pseudonymous_user_id` | `test_tracing.py::test_mask_changes_only_what_carries_a_cpf`, `test_tracing.py::test_exported_spans_are_masked_and_nested` |
| FR-604 | `IntentRouter.classify`, `ReplyRewriter.rewrite` (`session_id`) | `test_router.py::test_router_run_gets_the_conversation_session`, `test_handoff_and_rewriter.py::test_rewriter_run_gets_the_conversation_session`, `test_api.py::test_helper_agents_get_the_conversation_session` |
| FR-605 | `api/app.py` lifespan | Live trace audit (see [plan.md](plan.md)) |
