# Project Constitution — Beauty Pizza Delivery Agent

These principles are non-negotiable. Every spec, plan, task and pull request must comply with them.
When a principle conflicts with a feature request, the principle wins until this document is amended.

## I. Language

1. **Customer-facing language is Brazilian Portuguese (pt-BR).** Every message the agent sends to the customer, every
   tool message that may be surfaced to the customer and every menu rendering is in pt-BR.
2. **Everything else is English**: folder names, file names, identifiers, code comments, docstrings, logs, commit
   messages, specs and documentation.
3. Data that comes from the knowledge base (flavor, size and crust names such as `Calabresa`, `Média`,
   `Recheada com Catupiry`) is kept verbatim. It is data, not code.

## II. Deterministic Truth over Generated Text

1. **The LLM never decides prices, totals, item IDs or order IDs.** Prices come from the knowledge base (SQLite) and are
   resolved in code; totals come from the Order API (`total_price`); IDs come from API responses or session state.
2. **Flavor + size + crust combinations are validated in code** against the `precos` table before any write to the
   Order API. An invalid combination is never sent.
3. **Customer identity (`client_name`, `client_document`) comes from the inbound request**, is injected into tools via
   the run context, and is never accepted as an LLM-generated tool argument.
4. Tools return structured, factual results. The agent may phrase them, but must not contradict them.
5. **Guardrails are deterministic where it matters** (amendment, [003-guardrails](003-guardrails/spec.md)):
   - CPFs and card numbers are masked before any model call or log line. Traces get the same CPF masking at
     export, and a keyed pseudonym as user id ([006-langfuse-tracing](006-langfuse-tracing/spec.md)).
   - Injection and hard-blocklist inputs never reach a model.
   - Every reply is checked against the term lists before it is sent.
   - Static replies are built in code from tool data (Order API, Menu service), never by a model, and are
     stored in the agent's history like any other turn ([004-greeting](004-greeting/spec.md)).

## III. Source of Truth

| Concern                              | Source of truth                                   |
|--------------------------------------|---------------------------------------------------|
| Menu (flavors, ingredients, prices)  | `menu.db`, owned by the Menu service (seeded once from `knowledge_base.sql`) |
| Order items, address, total          | Order API                                         |
| Conversation history & session state | Agno `SqliteDb` (agent storage)                   |
| Order confirmation ("Pedido feito")  | Agent session state (the Order API has no status) |
| Intent log, first message of the day | `intent.db` ([004-greeting](004-greeting/spec.md)) |
| Holidays and commemorative dates     | `special_dates.json` ([005-special-dates](005-special-dates/spec.md)) |
| Human attendant tickets              | `human_attendant.db` ([003-guardrails](003-guardrails/spec.md)) |
| Semantic menu index                  | LanceDB inside the Menu service, derived from `menu.db` |

The vector index is a derived artifact and can always be rebuilt from `menu.db`. Since
[002-menu-service](002-menu-service/spec.md) the menu is changed only through the Menu service API, and the agent reads
it only through that API (amendment, 2026-11-08).

## IV. Simplicity First

1. One Agno `Agent` with well-designed tools. Multi-agent teams or workflows are introduced only when a spec proves a
   single agent is insufficient.
2. The business flow (from the flowchart) lives in the agent instructions and is enforced by tool invariants. It is
   not a hard-coded state machine, because customers jump between steps.
3. No speculative abstractions: no repository/service layers without a second implementation in sight.

## V. Quality Gates

1. Tools, clients and domain logic are typed (type hints + pydantic models) and covered by unit tests.
2. HTTP calls to the Order API are tested with mocked transports (`respx`). No unit test depends on the network or on
   the LLM.
3. Agent behavior is checked with conversation evals (golden pt-BR dialogues) that run against a live model and are
   kept separate from the unit suite.
4. `ruff` (lint + format) and `pytest` must pass before a task is marked done.

## VI. Operability

1. All configuration comes from environment variables (`pydantic-settings`, `.env` supported). No secrets in code.
2. The project starts with a **single command**: `uv run delivery-agent`.
3. Logs are structured. CPFs are masked in logs (`***.***.*89-01`).
4. External calls have timeouts and a bounded retry policy. Failures turn into friendly pt-BR messages, never stack traces.
5. Each customer message can be traced in Langfuse (one trace per message, grouped by session). Tracing is optional:
   `LANGFUSE_TRACING_ENABLED=false`, or no keys, turns it off, and a tracing failure never affects a reply.

## VII. Spec-Driven Workflow

1. Order of work: `spec.md` (what/why), then `plan.md` (how), then `tasks.md` (steps), then implementation.
2. Any change in behavior updates the spec first.
3. Each functional requirement is traceable to at least one task and one test.
