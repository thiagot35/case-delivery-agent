# Tasks: Special Dates in the First Agent Reply

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Definition of done for every task: `ruff check` passes, the related tests pass, and every function has a docstring.

- [x] **T501** `special_dates.json` + `special_dates.py` (schema, city resolution, window). **Done when** `test_special_dates.py` passes (window edges, year rollover, movable dates, cities, aliases, malformed file).
- [x] **T502** `MessageRequest.location`. **Done when** the location tests in `test_api.py` pass.
- [x] **T503** `ConversationService` first-agent-run logic, state defaults, the "DATAS ESPECIAIS" instruction, wiring, settings. **Done when** the 005 tests in `test_api.py` pass.
- [x] **T504** Eval S13 (the first agent reply mentions the upcoming date). **Done when** it passes live.
- [x] **T505** README, API contract, `.env.example`, constitution.
