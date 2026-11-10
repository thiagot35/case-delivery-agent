# Tasks: Beauty Pizza Ordering Agent

**Input**: [spec.md](spec.md), [plan.md](plan.md), [data-model.md](data-model.md), [contracts/](contracts/)

Legend: `[P]` = can run in parallel with the other `[P]` tasks of the same phase (different files, no dependency).
`[USx]` = user story served. Each task lists its target file(s) and a **Done when** check.
Definition of done for every task: `ruff check` passes, the related tests pass, and the spec is updated if behavior changed.

---

## Phase 0 — Setup

- [x] **T001** Initialize the uv project with the `src/` layout: `pyproject.toml` (name `case-delivery-agent`, Python
  3.13, `[project.scripts] delivery-agent = "delivery_agent.main:run"`), `src/delivery_agent/__init__.py`.
  **Done when** `uv run python -c "import delivery_agent"` works.
- [x] **T002** Add dependencies: `uv add agno fastapi "uvicorn[standard]" openai lancedb rapidfuzz httpx pydantic-settings python-dotenv`
  and `uv add --dev pytest pytest-asyncio pytest-cov respx ruff`. **Done when** `uv.lock` is committed and `uv sync` is clean.
- [x] **T003** [P] Configure `ruff` and `pytest` (markers `integration`, `evals` excluded by default) in `pyproject.toml`.
- [x] **T004** [P] Add `.gitignore` (`.venv/`, `data/`, `.env`, `__pycache__/`) and `.env.example` (quickstart.md).
- [x] **T005** [P] `config.py`: `Settings` (pydantic-settings) with every variable from plan.md §5, plus a
  `get_settings()` cached accessor. **Done when** a unit test covers the defaults and env overrides.
- [x] **T006** [P] `logging.py`: structured logging setup + `mask_cpf()` filter.

## Phase 1 — Foundations (domain, knowledge base, Order API client)

- [x] **T010** [P] [US2] `menu/bootstrap.py::ensure_menu_db()`: execute `knowledge_base.sql` into `MENU_DB_PATH`
  when missing, idempotently. **Done when** the test builds a temp DB with 6 pizzas, 3 sizes, 3 crusts and 38 prices.
- [x] **T011** [US2] `menu/repository.py::MenuRepository`: `list_pizzas()`, `get_pizza(id)`, `list_sizes()`,
  `list_crusts()`, `price_matrix(pizza_id)`, `get_price(pizza_id, size, crust) -> PriceQuote`. **Done when** tests
  prove Pequena is Tradicional only, Doce de Leite is Tradicional only, and the prices match the SQL.
- [x] **T012** [P] `domain/cpf.py` (normalize, validate check digits, reject repeated digits, mask) and
  `domain/formatting.py` (`format_brl`, `build_item_name`, `parse_item_name`, `render_menu`, `render_order_summary`
  in pt-BR). **Done when** unit tests cover valid/invalid CPFs, the `R$ 1.234,50` format and the name round-trip.
- [x] **T013** [P] `domain/models.py`: all models from data-model.md §2 and §6.
- [x] **T014** [US3] `clients/errors.py` + `clients/order_api_client.py::OrderApiClient` with all methods from
  contracts/order-api-client.md, the timeout and the GET-only retry. Address updates always send all 4 keys.
- [x] **T015** [US3] `tests/unit/test_order_api_client.py` with `respx`: payload shapes, `find_orders` sorting,
  unique-constraint → `OrderAlreadyExistsError`, 404 mapping, no retry on POST/PATCH/DELETE.
  **Done when** the client coverage is ≥ 90%.

## Phase 2 — Tools

- [x] **T020** [US2] `menu/resolver.py`: flavor/size/crust resolution (normalize → exact → rapidfuzz → semantic via an
  injectable index protocol). Tests: "calabreza", "4 queijos", "frango catupiri", "media", "borda de cheddar", "sem borda".
- [x] **T021** [US1][US2] `tools/menu_tools.py::MenuTools`: `list_menu`, `get_pizza_details`, `get_price`,
  `search_menu`, `suggest_pizzas` per contracts/agent-tools.md. Tests use a fake vector index.
- [x] **T022** [US3] `agent/state.py`: `DEFAULT_SESSION_STATE`, typed get/set helpers, transition helpers
  (`mark_open`, `mark_confirmed`, `mark_cancelled`) following the data-model.md §3 state machine.
- [x] **T023** [US3] `tools/order_tools.py::OrderTools`: `get_customer_history`, `get_current_order`, `add_pizza`,
  `remove_item` (identity from `run_context.dependencies`, prices from `MenuRepository`).
- [x] **T024** [US3] Tests for T023: first add creates the order, second add appends, adopt today's order, unique-race
  recovery, stale `order_id` 404 recovery, partial add-items failure re-reads the order, invalid quantity/combination
  never calls the API, snapshot refreshed after each write.
- [x] **T025** [US4][US5] `OrderTools`: `get_delivery_address`, `confirm_delivery_address`, `set_delivery_address`,
  `confirm_order`, `cancel_order` + tests:
  - `get_delivery_address` returns `current_order` → `last_order` → `none` in that priority and skips previous orders
    without an address;
  - `confirm_delivery_address` makes no API write for `current_order`, PATCHes for `last_order`, and fails with
    `no_address_candidate` when no address was fetched first;
  - `confirm_order` is blocked without items or an address;
  - a write after confirm reopens the order;
  - `cancel_order` removes all items.

## Phase 3 — Agent

- [x] **T030** [US1] `agent/factory.py::build_agent(settings, menu_tools, order_tools)` with the configuration from
  plan.md §3.2 (`OpenAIResponses(id=settings.model_id)`, `SqliteDb`, history, session state).
- [x] **T031** [US1–US5] `agent/instructions.py`: pt-BR instructions (persona, hard rules, flow guidance from
  flowchart-review.md §5, global behaviors, formatting). Reviewed against every row of the flowchart node-mapping table.
- [x] **T032** [US1] Greeting behavior: new session → `get_customer_history` first. Returning customer → repeat offer.
  New customer → `list_menu` + `suggest_pizzas`. Verified by eval S1/S2.
- [x] **T033** [US2] Guardrails in the instructions: scope limits, no prices without tools, prompt-injection refusal.
  Verified by eval S6.
- [x] **T034** [US7] `agent/session_resolver.py::SessionResolver.resolve(document) -> (session_id, is_new)` using
  `SqliteDb.get_sessions(..., sort_by="updated_at", sort_order="desc", limit=1)` and `SESSION_TIMEOUT_MINUTES`.
  Tests with a temp DB and a frozen clock.

## Phase 4 — HTTP API & entrypoint

- [x] **T040** [P] `api/schemas.py`: `MessageRequest` (CPF validator), `MessageResponse`, `OrderSnapshot`.
- [x] **T041** `api/service.py::ConversationService.handle(request)`: per-document lock, session resolution,
  `agent.arun(...)` with `dependencies={"customer", "today"}`, snapshot extraction, error mapping (503/500).
- [x] **T042** `api/app.py` (FastAPI app factory with a lifespan that runs bootstrap and creates the shared
  `httpx.AsyncClient`, repository, index, tools and agent; routes `POST /messages`, `GET /health`) and
  `main.py::run()` (uvicorn).
- [x] **T043** [P] `scripts/chat_cli.py`: interactive loop with `--name`, `--cpf`, `--url`. Prints the reply and a
  one-line order summary.
- [x] **T044** `tests/unit/test_api.py`: 422 on invalid CPF, response shape, same-CPF requests serialized (fake agent).

## Phase 5 — Embeddings (differentiator)

- [x] **T050** [US8] `menu/vector_index.py::MenuVectorIndex`: LanceDB table `menu`, `OpenAIEmbedder`, `build(pizzas,
  prices)`, `search(query, limit) -> list[(pizza_id, score)]`, hash-based rebuild check.
- [x] **T051** [US8] Wire the index into `bootstrap` (build when missing/stale) and into `MenuTools.search_menu`.
- [x] **T052** [US8] Wire the semantic fallback into `menu/resolver.py` with a confidence threshold → `flavor_ambiguous`.
- [x] **T053** [US8] `suggest_pizzas` personalization (history-seeded query, exclude previous flavors) + tests with a
  fake index. Integration test (`-m evals`): "sem carne" returns Margherita/Quatro Queijos and not Pepperoni/Calabresa.

## Phase 6 — Session continuity & reorder

- [x] **T060** [US7] Confirmed-order handling in the instructions + state: an active session with
  `order_status=confirmed` → ask "alterar ou novo". "Novo" on the same day → explain and reopen today's order.
- [x] **T061** [US6] `OrderTools.repeat_last_order` (parse, re-price, skip invalid, report price changes, copy address)
  + tests.
- [x] **T062** [US7] Expired session with today's order → greeting mentions it and offers to modify it. Verified by eval S5.

## Phase 7 — Quality, docs, delivery

- [x] **T070** Smoke test of the single command: fresh clone → `uv sync` → `uv run delivery-agent` → `GET /health` ok.
- [x] **T071** `README.md` (English): overview, architecture diagram, setup, single-command run, CLI usage, tests,
  design decisions (link to `specs/`), flowchart review summary, known Order API limitations.
- [x] **T072** `tests/evals/`: scripted dialogues S1–S6 from quickstart.md. Assert on tool calls, final Order API state
  (items, total, address), session state, pt-BR replies and presence of the total. Marked `evals`.
- [x] **T073** [P] GitHub Actions workflow: `uv sync`, `ruff check`, `ruff format --check`, `pytest` (unit only).
- [ ] **T074** Presentation material (20 min): problem, flowchart review, architecture, demo (S1, S2, S3), embeddings,
  testing strategy, next steps.

---

## Dependencies

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → (Phase 5 ∥ Phase 6) → Phase 7
T010 → T011 → T020 → T021
T013 → T014 → T015
T014 + T011 + T022 → T023 → T024 → T025
T021 + T025 → T030 → T031 → T032/T033
T034 + T030 → T041 → T042 → T044
T050 → T051 → T052 → T053
```

## MVP cut

Phases 0–4 deliver every mandatory case requirement (greeting/menu, Q&A, multi-item orders with the API total,
address management, conversation state, single command). Phase 5 adds the embeddings differentiator. Phase 6 adds the
flowchart's returning-customer paths.
