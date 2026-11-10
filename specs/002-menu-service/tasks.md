# Tasks: Menu Service

**Input**: [spec.md](spec.md), [plan.md](plan.md), [contracts/menu-service-api.md](contracts/menu-service-api.md)

Definition of done for every task: `ruff check` passes, the related tests pass, and every function has a docstring.

## Phase 1 — Menu service core

- [x] **T201** `src/menu_service/{config,models,formatting,bootstrap}.py`. Bootstrap seeds only when `menu.db` is
  missing. **Done when** `test_bootstrap_seeds_only_once` passes.
- [x] **T202** `repository.py`: snapshot from a path or an open connection, `orderable_pizzas`, `min_price`/`max_price`.
- [x] **T203** `store.py`: upsert/delete per table, `precos` reference check, cascade, affected pizza ids,
  `transaction()`. **Done when** `tests/unit/menu_service/test_store.py` passes.
- [x] **T204** `resolver.py` (moved from 001) + `protocols.py`. Sizes/crusts added through the API resolve.
- [x] **T205** `vector_index.py`: one document per pizza with `min_price`/`max_price`, `sync`, `refresh`.
  **Done when** `test_vector_index.py` passes (including the cleanup of old-version rows).
- [x] **T206** `service.py`: reads, plus writes with index-before-commit and rollback on index failure.
- [x] **T207** `schemas.py` + `app.py` + `main.py`: endpoints, error mapping, admin token, `menu-service` script.
  **Done when** `test_api.py` passes.

## Phase 2 — Agent uses the API

- [x] **T210** `clients/menu_api_client.py` + `MenuServiceError`/`MenuServiceUnavailableError`.
  **Done when** `test_menu_api_client.py` passes.
- [x] **T211** `MenuTools(menu_client)` and `OrderTools(order_client, menu_client)`, with `menu_unavailable` on
  outages. **Done when** the menu/order tool tests pass with `FakeMenuApi`.
- [x] **T212** Remove `delivery_agent/menu/` and its settings. `render_menu` moves to `domain/formatting.py`.
  **Done when** `grep -r "sqlite3\|lancedb\|MenuRepository" src/delivery_agent` returns nothing.
- [x] **T213** `/health` reports `menu_service` and `menu_index`.
- [x] **T214** `main.py` autostarts the Menu service. **Done when** `test_main.py` passes.
- [x] **T215** The tool-set test proves the agent has no menu-change tool.

## Phase 3 — Docs

- [x] **T220** README (architecture, menu admin curl examples, configuration), `.env.example`.
- [x] **T221** Amend the 001 docs (plan, data model, agent tools, quickstart) and the constitution source-of-truth table.
