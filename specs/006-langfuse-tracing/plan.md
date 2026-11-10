# Implementation Plan: Langfuse Tracing

**Spec**: [spec.md](spec.md)

## Modules

```
src/delivery_agent/tracing.py      Tracer protocol, NoopTracer, LangfuseTracer, setup_tracing,
                                   mask_span_attributes, pseudonymous_user_id
src/delivery_agent/config.py       LANGFUSE_* settings, Settings.langfuse_configured()
src/delivery_agent/agent/factory.py prompts_version()
src/delivery_agent/api/service.py  root span per message + step spans, trace tags/metadata
src/delivery_agent/api/app.py      setup_tracing + auth check at startup, shutdown (flush) on exit
src/delivery_agent/guardrails/router.py, rewriter.py   session_id forwarded to the helper agent runs
```

Dependencies: `langfuse>=5.2.0` and `openinference-instrumentation-agno>=1.1.2`, the latest releases at
implementation time.

## Flow

1. **Lifespan start.** `setup_tracing(settings, version=prompts_version())`:
   - If tracing is off, it returns `NoopTracer`.
   - If it's on, it sets `OTEL_SERVICE_NAME`, then creates `Langfuse(...)`. The client registers the global tracer provider and installs the `mask_otel_spans` hook.
   - Then `AgnoInstrumentor().instrument()` runs. The order matters: the instrumentor uses the global provider.
   - `auth_check` runs in a thread. A failure only logs a warning.
2. **Each message.** Under the customer lock:
   - `tracer.message(...)` opens `handle-message`. `propagate_attributes` sets the session, the pseudonymous user and the version on every span created inside it, the Agno spans included.
   - After the input filter, the root input is set to the redacted text.
   - After the reply, the root output is set, and `annotate` adds the route tag and the metadata to the root span.
3. **Lifespan end.** `tracer.shutdown()` runs in a thread. It uninstruments Agno, then flushes and shuts down the client.

## Trace tree (agent route)

```
handle-message (span)                      in: redacted message   out: reply   tags: [route]
├─ filter-input (guardrail)                out: blocked, pii_found, violations
├─ classify-intent (chain)                 in: message, flow_step, first_message_of_day   out: intent, method, reason
│  └─ Intent_Router.arun (agent) └─ OpenAIResponses.ainvoke (generation)
├─ Beauty_Pizza_Attendant.arun (agent)
│  ├─ OpenAIResponses.ainvoke (generation)  model, usage, cost
│  ├─ add_pizza (tool)                      arguments, result
│  └─ OpenAIResponses.ainvoke (generation)
├─ check-output (guardrail)                in: reply   out: violations   level WARNING on violation
└─ rewrite-reply (chain)                   only after a violation
   ├─ Reply_Rewriter.arun (agent) └─ generation
   └─ check-output (guardrail)
```

## Live trace audit

This is the skill's required step. It ran against Langfuse Cloud (US) with real conversations, and each trace was fetched back with `langfuse-cli api observations list`.

- **Bug found: helper agents in the wrong session.** The router agent spans carried an invented `session.id`, because the router agent was called without one. Fixed by forwarding the conversation's `session_id`. Re-audited: one session per conversation.
- **Gap found: no service name.** `service.name` was `unknown_service:python.exe`. Fixed by setting `OTEL_SERVICE_NAME`.
- **Confirmed: no raw CPF.** No exported observation contained the CPF. The CPF in the system prompt shows as `***.***.*NN-NN`.
- **Confirmed: generations are complete.** Every generation has the model, token usage (including cached and cache-creation tokens) and a computed cost.
- **Confirmed: tools nest correctly.** Tools appear as `TOOL` observations, siblings of the generation that requested them.
