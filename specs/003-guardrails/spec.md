# Feature Spec: Conversation Guardrails

| Field     | Value                                                    |
|-----------|----------------------------------------------------------|
| Feature   | 003-guardrails                                           |
| Status    | Implemented                                              |
| Depends   | [001-pizza-ordering-agent](../001-pizza-ordering-agent/spec.md), [002-menu-service](../002-menu-service/spec.md) |
| Plan      | [plan.md](plan.md) · [tasks.md](tasks.md)                |

## Context

Until now every customer message went straight to the order agent, and every reply went straight back to the
customer. Nothing removed sensitive data, kept off-topic chats or "I want a human" requests away from the agent,
limited the tool loop, or checked the reply's wording. This feature wraps the agent in a guardrail pipeline:

```
[Customer message]
   │
   ▼
[Deterministic pre-filter] ── mask CPF / cards (Presidio) + injection / hard blocklist ──► blocked: static reply
   │
   ▼
[Router: state check + intent agent]
   ├─► not_delivery ─────────────────────────► static reply ("Só atendo pedidos…")
   ├─► call_human (count >= 3) ──────────────► ticket in human_attendant DB + static reply
   └─► delivery, or call_human (count < 3)
          │
          ▼
[Order agent: bounded tool loop, max 10 tool calls]
          │
          ▼
[Output guardrails, in parallel: profanity, competitors, undesired terms]
   ├─► pass ──► reply to the customer
   └─► fail ──► 1 rewrite with critique ──► still failing ──► ask the customer to rephrase
```

## Requirements (from the business request)

| # | Requirement |
|---|---|
| R1 | Check each customer message deterministically, with a PII library, for sensitive data: **CPF and credit card only**. Mask it. |
| R2 | Check the input against a hard blocklist and prompt-injection patterns. |
| R3 | An agent classifies the customer's intent as `delivery`, `not_delivery` or `call_human`. |
| R4 | `not_delivery` gets a static reply. |
| R5 | `call_human` with a count >= 3 is logged to a new human attendant DB. With a count < 3 the message goes to the order agent. |
| R6 | The order agent's tool loop is bounded: at most 10 tool calls per message. |
| R7 | Deterministic output guardrails check profanity and competitors (plus other undesired terms), from a JSON list. |
| R8 | A failing reply gets 1 retry with a critique. If it fails again, the customer is asked to rephrase. |

## Decisions

| Topic | Decision |
|---|---|
| PII library | Microsoft Presidio. The analyzer has only two recognizers: a custom `BR_CPF` pattern validated by the CPF check digits, and Presidio's Luhn-validated `CREDIT_CARD`. spaCy `pt_core_news_sm` is only the tokenizer. |
| Curse words in the input | Not blocked: a frustrated customer still reaches the agent. Profanity is enforced on the **output**. The input blocks only injection patterns and the hard blocklist (threats, slurs). |
| Retry | A separate tool-less **rewriter** agent rewords the draft with the critique. Rerunning the order agent would re-execute its tools (for example, add a pizza twice). |
| Handoff | One ticket per session. After the handoff, delivery messages still reach the order agent. |
| Router failure | Fail open to `delivery`. The order agent still refuses off-topic subjects (instruction rule 5). |
| Router context | Before calling the model, a deterministic **state check** routes short answers ("sim", "2", "Rua …") in an active flow to `delivery`. The model sees the flow step and the last agent reply. |

## Functional requirements

| ID | Requirement |
|---|---|
| FR-301 | The raw message is never sent to a model and never logged. Only the redacted text is (CPF → `[CPF removido]`, card → `[cartão removido]`). |
| FR-302 | When PII was removed, the reply ends with a pt-BR notice that the data was removed and isn't needed. |
| FR-303 | A message matching `prompt_injection` or `input_hard_block` gets a static reply (`route=blocked`), and no model is called. |
| FR-304 | `not_delivery` → static reply (`route=not_delivery`). The order agent is not called. |
| FR-305 | Each `call_human` increments `session_state.human_requests`. Below `HUMAN_HANDOFF_THRESHOLD` (3) the agent answers, offering to help and explaining that asking again calls a human. At the threshold a ticket is stored and the reply is static (`route=human_handoff`). |
| FR-306 | A ticket stores: session, name, CPF (for the attendant), order id, request count and the redacted last message. |
| FR-307 | The order agent runs with `tool_call_limit=AGENT_TOOL_CALL_LIMIT` (10). |
| FR-308 | Every agent reply is checked for the `profanity`, `competitors` and `undesired_terms` lists. Matching is whole-word and ignores case, accents and punctuation. |
| FR-309 | A failing reply is rewritten once. A rewrite that passes is sent. Otherwise the static "pode reformular?" reply is sent. Either way, the stored agent history is updated to match what the customer received. |
| FR-310 | `MessageResponse.route` reports the branch: `agent`, `not_delivery`, `human_handoff` or `blocked`. |
| FR-311 | The term lists live in `src/delivery_agent/guardrails/terms.json` (or `GUARDRAIL_TERMS_PATH`) and are validated at startup. |

## Edge cases

- **Bare 11-digit numbers** are masked only if they pass the CPF check digits. Order numbers, prices, phone numbers and addresses are left alone.
- ~~**Static replies** (blocked, not_delivery, handoff) are not stored in `agent.db`.~~ Superseded by
  [004-greeting](../004-greeting/spec.md): every static reply is stored as a turn of the agent's history (a blocked
  message as `[mensagem bloqueada]`), so the agent has the full context, and the session stays active.
- **Fix (004):** `replace_last_reply` now writes the patched run with `db.upsert_run`. Agno keeps runs in their own
  table and `asave_session` writes only the session row, so the rewrite used to stay in memory only.
- **Router error or invalid output** → `delivery` (logged as `method=fallback`).
- **A rewrite that still contains a term**, or an empty rewrite → the "pode reformular?" reply.
- **The customer's own CPF in the message** is masked too. The system already knows it from the channel (`client_document`).

## Traceability

| Req | Implementation | Tests |
|---|---|---|
| R1, FR-301/302 | `guardrails/pii.py`, `filters.InputFilter`, `ConversationService.handle` | `test_pii.py`, `test_filters.py`, `test_api.py::test_pii_is_redacted_before_the_agent`, eval S9 |
| R2, FR-303 | `terms.json` (`prompt_injection`, `input_hard_block`), `filters.InputFilter` | `test_filters.py`, `test_api.py::test_injection_is_blocked_without_any_model_call`, eval S6 |
| R3 | `guardrails/router.py`, `factory.build_router_agent` | `test_router.py`, `test_agent_factory.py` |
| R4, FR-304 | `ConversationService._answer`, `messages.not_delivery` | `test_api.py::test_not_delivery_gets_static_reply`, eval S7 |
| R5, FR-305/306 | `guardrails/handoff_store.py`, `ConversationService._answer` | `test_handoff_and_rewriter.py`, `test_api.py::test_call_human_*`, eval S8 |
| R6, FR-307 | `factory.build_agent(tool_call_limit=…)` | `test_agent_factory.py::test_order_agent_has_bounded_tool_loop` |
| R7, FR-308/311 | `terms.json`, `guardrails/terms.py`, `filters.OutputGuardrail` | `test_terms.py`, `test_filters.py` |
| R8, FR-309 | `guardrails/rewriter.py`, `ConversationService._guard_reply`, `agent/sessions.py` | `test_api.py::test_output_violation_*`, `test_agent_factory.py::test_agno_session_access` |
