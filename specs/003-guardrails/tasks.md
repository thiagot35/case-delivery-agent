# Tasks: Conversation Guardrails

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Definition of done for every task: `ruff check` passes, the related tests pass, and every function has a docstring.

- [x] **T301** Dependencies: Presidio + `pt_core_news_sm`. **Done when** `PiiRedactor()` loads on Python 3.13.
- [x] **T302** `terms.json` + `terms.py`. **Done when** `test_terms.py` passes (no matches inside other words).
- [x] **T303** `pii.py` (CPF check digits, card Luhn). **Done when** `test_pii.py` passes, including the non-PII numbers.
- [x] **T304** `filters.py`: `InputFilter`, `OutputGuardrail`. **Done when** `test_filters.py` passes.
- [x] **T305** `router.py` + `factory.build_router_agent`. **Done when** `test_router.py` passes (state check, fail-open).
- [x] **T306** `rewriter.py` + `handoff_store.py`. **Done when** `test_handoff_and_rewriter.py` passes.
- [x] **T307** `agent/sessions.py` + `agent/runner.py`; `tool_call_limit`. **Done when** `test_agent_factory.py` passes.
- [x] **T308** `ConversationService` pipeline, `route` in the response, app wiring, settings, the `human_requests`
  instruction. **Done when** the pipeline tests in `test_api.py` pass.
- [x] **T309** Evals S7 (off-topic), S8 (handoff ticket), S9 (card never echoed). **Done when** they pass live.
- [x] **T310** README, `.env.example`, constitution principle.
