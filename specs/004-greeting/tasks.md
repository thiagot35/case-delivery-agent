# Tasks: Greeting Intent, Intent Log and Stored Static Replies

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Definition of done for every task: `ruff check` passes, the related tests pass, and every function has a docstring.

- [x] **T401** Router: `greeting` intent, first-message flag in the prompt, the not-first-message guard, `RunStatus.error` fallback. **Done when** `test_router.py` passes.
- [x] **T402** Rewriter: `RunStatus.error` → `""`. **Done when** `test_handoff_and_rewriter.py` passes.
- [x] **T403** `greeting.py` + `messages.greeting_*`. **Done when** `test_greeting.py` passes (new, returning, order today, outages).
- [x] **T404** `intent_store.py`. **Done when** `test_intent_store.py` passes.
- [x] **T405** `AgnoSessionAccess.append_turn` + the persisted `replace_last_reply`. **Done when** `test_sessions.py` passes against a real `SqliteDb`.
- [x] **T406** `ConversationService` pipeline, `route=greeting`, the `greeted` state, the "SAUDAÇÃO JÁ ENVIADA" instruction, wiring, `INTENT_DB_PATH`. **Done when** the 004 tests in `test_api.py` pass.
- [x] **T407** Evals: S1/S2 greeting assertions, plus S10 (flavors, a second "Oi" → agent), S11 (bare "Sim") and S12 (item → agent). **Done when** they pass live.
- [x] **T408** README, `.env.example`, constitution, 003 amendment.
