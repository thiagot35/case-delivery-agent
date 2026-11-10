# Feature Spec: Menu Service

| Field     | Value                                                    |
|-----------|----------------------------------------------------------|
| Feature   | 002-menu-service                                         |
| Status    | Implemented                                              |
| Depends   | [001-pizza-ordering-agent](../001-pizza-ordering-agent/spec.md) |
| Plan      | [plan.md](plan.md) · Contract: [menu-service-api.md](contracts/menu-service-api.md) · [tasks.md](tasks.md) |

## Context

In 001 the agent process owned the menu. It built `menu.db` from the SQL script, read it in memory and embedded it
into LanceDB, and there was no way to change the menu. This feature moves everything about the menu into a separate
**Menu service** with its own endpoints. The conversational agent reaches the menu **only through that API** and has no
way to change it.

## Requirements (from the business request)

| # | Requirement |
|---|---|
| R1 | Menu create, edit and delete are a separate service with its own endpoints. |
| R2 | The conversational flow has no menu-change action. It calls the Menu service over its API. |
| R3 | One add/edit endpoint. The contract carries the table and its fields. |
| R4 | For `precos`, `pizza_id`, `tamanho_id` and `borda_id` must exist in their reference tables, otherwise an error is returned. |
| R5 | After a `menu.db` write, the embedding is created or updated in the vector DB. |
| R6 | One delete endpoint. The contract carries the table and the item id. Deleting a `pizzas`, `tamanhos` or `bordas` row deletes the `precos` rows that reference it. |
| R7 | The existing tools stay, but query embedding and the DB/vector queries live in the menu side. |
| R8 | The vector DB is chunked per item. |
| R9 | Each vector Document has `min_price` and `max_price` keys. |

## User stories

### US1 — Admin adds or edits a menu item (P1)
**As** the pizzeria admin **I want** to add or edit a pizza, size, crust or price **so that** customers see the new menu
without a deploy.

- **Given** a new pizza without `id`, **when** `PUT /menu/items {"table":"pizzas","fields":{...}}` is called,
  **then** it is created and its id returned.
- **Given** an existing `id` and only some fields, **when** it is upserted, **then** only those fields change.
- **Given** a price whose `borda_id` does not exist, **when** it is upserted, **then** the API answers 422
  `reference_not_found` with `{"missing":{"borda_id":…}}` and nothing changes.
- **Given** a price for a new pizza, **when** it is upserted, **then** the pizza becomes orderable and the agent can
  quote it in the next message.
- **Given** any successful write, **then** the documents of the affected pizzas are re-embedded before the response.

### US2 — Admin deletes a menu item (P1)
- **Given** a crust used by several pizzas, **when** `DELETE /menu/items {"table":"bordas","id":2}` is called,
  **then** the crust and every price that references it are deleted, and the affected pizzas are re-embedded.
- **Given** a pizza, **when** it is deleted, **then** its prices and its vector document are removed.
- **Given** an id that does not exist, **then** the API answers 404.

### US3 — The agent reads the menu only through the API (P1)
- The agent's tools (`list_menu`, `get_pizza_details`, `get_price`, `search_menu`, `suggest_pizzas`, `add_pizza`,
  `repeat_last_order`) call the Menu service. The agent process never opens `menu.db` or LanceDB.
- The agent has no tool that changes the menu.
- **Given** the Menu service is down, **then** the tools answer `menu_unavailable` and the agent apologizes. It never
  guesses a price.

### US4 — Single command (P2)
- `uv run delivery-agent` starts the Menu service, waits until it is healthy and serves the agent. It stops the
  service on exit, and reuses one that is already running.

## Functional requirements

| ID | Requirement |
|---|---|
| FR-201 | `PUT /menu/items` upserts `pizzas`, `tamanhos`, `bordas` (by `id`) and `precos` (by composite key). |
| FR-202 | Creating needs every column; updating needs `id` plus at least one column. Unknown tables or fields → 422. |
| FR-203 | `precos` writes validate the three references and list every missing one (422 `reference_not_found`). |
| FR-204 | UNIQUE violations (`tamanho`, `tipo`) → 409 `duplicate`. Missing rows on update/delete → 404 `not_found`. |
| FR-205 | `DELETE /menu/items` deletes by `id` (`precos`: `{pizza_id, tamanho_id, borda_id}`) and cascades to `precos`. |
| FR-206 | Each write runs in one SQLite transaction and commits only after the vector index accepted the re-embedded documents. If embedding fails, it rolls back and answers 503 `index_unavailable`. |
| FR-207 | One vector document per orderable pizza (`id = content_hash = "pizza-{id}"`), never split. Metadata: `pizza_id`, `flavor`, `min_price`, `max_price`. |
| FR-208 | Pizzas without prices are not orderable: not listed, not resolvable and not indexed. |
| FR-209 | Read endpoints: `GET /menu`, `GET /menu/flavors/resolve`, `GET /menu/quote`, `POST /menu/search`, `GET /health`. |
| FR-210 | The search query is embedded by the Menu service. Without an index, search falls back to keyword overlap. |
| FR-211 | `menu.db` is seeded from `knowledge_base.sql` only when missing, so API edits survive restarts. |
| FR-212 | At startup the index is synced with `menu.db`: changed documents are upserted and orphans (including the rows of older index versions) are deleted. |
| FR-213 | When `MENU_ADMIN_TOKEN` is set, writes require `X-Admin-Token`. Reads stay open on the local network. |

## Edge cases

- Renaming a size or crust re-embeds every pizza with a price in it (the document lists sizes/crusts).
- Deleting the last price of a pizza removes its document. The pizza row stays and can be priced again later.
- A size or crust added through the API resolves by exact or fuzzy name, besides the built-in synonyms.
- Repeat-last-order only re-adds an item when the quote resolves to the **same** flavor. A fuzzy match to another
  flavor is skipped.
- Agno's `LanceDb.upsert` embeds before it deletes the old row. A failing embedder leaves the old document intact.

## Out of scope

- The LanceDB L2-vs-cosine distance mismatch and recalibrating `SEMANTIC_MAX_DISTANCE`.
- Negation in semantic queries ("sem carne").
- Authentication beyond the optional shared admin token.

## Traceability

| Req | FR | Code | Tests |
|---|---|---|---|
| R1 | FR-201, FR-205, FR-209 | `menu_service/app.py`, `service.py` | `tests/unit/menu_service/test_api.py` |
| R2 | — | `delivery_agent/tools/*`, `clients/menu_api_client.py` (read-only) | `test_menu_tools.py::test_agent_has_no_menu_change_tools` |
| R3 | FR-201, FR-202 | `schemas.py` (`UpsertRequest`), `store.upsert` | `test_store.py`, `test_api.py::test_upsert_errors` |
| R4 | FR-203 | `store._upsert_price` | `test_store.py::test_price_requires_existing_references` |
| R5 | FR-206 | `MenuService._write`, `MenuVectorIndex.refresh` | `test_api.py::test_index_failure_rolls_back_the_write`, `test_vector_index.py` |
| R6 | FR-205 | `store._delete_entity` | `test_store.py::test_delete_cascades_to_prices` |
| R7 | FR-209, FR-210 | `MenuService.search/quote/resolve_flavor` | `test_menu_tools.py`, `test_order_tools.py` |
| R8 | FR-207 | `vector_index.build_document` | `test_vector_index.py::test_build_documents` |
| R9 | FR-207 | `vector_index.build_document` | `test_vector_index.py::test_build_documents` |
