# Tasks: Langfuse Tracing

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Definition of done for every task: `ruff check` passes, the related tests pass, and every function has a docstring.

- [x] **T601** Install the Langfuse agent skill (`.claude/skills/langfuse`, pinned in `.source-commit`) and read the current docs (Agno integration, SDK instrumentation, masking, best practices).
- [x] **T602** Add the dependencies, the `LANGFUSE_*` settings, `tracing.py` and `prompts_version`. **Done when** `test_tracing.py` passes (switch, masking, pseudonym, in-memory export through the real SDK).
- [x] **T603** Spans in `ConversationService` and lifespan wiring. **Done when** the tracing tests in `test_api.py` pass.
- [x] **T604** Run the app with tracing, fetch the traces, audit them against the best practices, fix the gaps and re-audit. Found: helper agents in invented sessions (fixed by forwarding `session_id`, with tests) and the missing `service.name`.
- [x] **T605** README, `.env.example`, constitution, tests default to tracing off.
