# Contract: Menu Service API

Base URL: `MENU_SERVICE_BASE_URL` (default `http://127.0.0.1:8081`). JSON in and out. Money values are decimal strings
(`"52.00"`). Errors have the shape `{"error_code": str, "message": str, ...}`; pydantic validation errors use FastAPI's
default 422 body.

## Write endpoints (admin)

When `MENU_ADMIN_TOKEN` is set, both require the header `X-Admin-Token: <token>`, otherwise 401 `unauthorized`.

### `PUT /menu/items`: add or edit

The body names the table and its fields. Field names are the table's column names.

| `table` | `fields` | Key |
|---|---|---|
| `pizzas` | `id?`, `sabor`, `descricao`, `ingredientes` | `id` |
| `tamanhos` | `id?`, `tamanho` | `id` |
| `bordas` | `id?`, `tipo` | `id` |
| `precos` | `pizza_id`, `tamanho_id`, `borda_id`, `preco` (> 0, 2 decimals) | `(pizza_id, tamanho_id, borda_id)` |

Rules:
- `pizzas` / `tamanhos` / `bordas` without `id` → **create**, and every column is required.
- With `id` → **partial update** (at least one column). 404 if the id doesn't exist.
- `precos` → **create** when the key is new, **update** `preco` otherwise. The three references must exist.
- After the write, the affected pizzas are re-embedded **before** the commit.

```json
{"table": "precos", "fields": {"pizza_id": 7, "tamanho_id": 3, "borda_id": 1, "preco": "52.00"}}
```

`200`:
```json
{"table": "precos", "action": "created",
 "item": {"pizza_id": 7, "tamanho_id": 3, "borda_id": 1, "preco": 52.0},
 "reindexed_pizza_ids": [7]}
```

Errors:

| Status | `error_code` | When |
|---|---|---|
| 422 | `reference_not_found` | `precos` references a missing pizza/size/crust. Body adds `"missing": {"borda_id": 9}` with every missing key |
| 422 | (FastAPI) | Unknown table, unknown field, missing columns on create, nothing to update, invalid price |
| 409 | `duplicate` | `tamanhos.tamanho` / `bordas.tipo` already exists |
| 404 | `not_found` | Update of a missing `id` |
| 503 | `index_unavailable` | Embedding failed. The change was rolled back |

### `DELETE /menu/items`: delete

```json
{"table": "bordas", "id": 2}
{"table": "precos", "id": {"pizza_id": 1, "tamanho_id": 1, "borda_id": 1}}
```

Deleting from `pizzas`, `tamanhos` or `bordas` also deletes every `precos` row that references the item. The affected
pizzas are re-embedded. A pizza that is deleted, or that has no price left, loses its vector document.

`200`:
```json
{"table": "bordas", "deleted": {"id": 2, "tipo": "Recheada com Cheddar"}, "cascaded_prices": 10,
 "reindexed_pizza_ids": [1, 2, 3, 4, 5], "removed_pizza_ids": []}
```

Errors: 404 `not_found`, 503 `index_unavailable`, 422 for an invalid body.

## Read endpoints (used by the agent)

A `PizzaView` is:
```json
{"id": 4, "flavor": "Calabresa", "description": "...", "ingredients": "...",
 "min_price": "27.00", "max_price": "51.00",
 "prices": [{"size": "Pequena", "crust": "Tradicional", "price": "27.00"}]}
```

Only **orderable** pizzas (at least one price) are listed, resolved or searched.

| Endpoint | Response |
|---|---|
| `GET /menu` | `{"pizzas": [PizzaView], "sizes": [str], "crusts": [str]}` |
| `GET /menu/flavors/resolve?text=` | `{"pizza": PizzaView \| null, "score", "method": "exact\|fuzzy\|semantic\|none", "needs_confirmation", "options": [flavor]}`. `options` is set when nothing matched |
| `GET /menu/quote?flavor=&size=&crust=` | `{"status", "pizza", "size", "crust", "price", "alternatives": [price row], "suggestion", "options", "sizes", "crusts"}` |
| `POST /menu/search` `{"query", "limit": 1..6}` | `{"query", "method": "semantic\|keyword", "results": [{"pizza": PizzaView, "relevance"}]}` |
| `GET /health` | `{"status": "ok", "index": "ready\|missing"}` |

`quote.status`:

| Status | Meaning | Extra fields |
|---|---|---|
| `ok` | Priced | `pizza`, `size`, `crust`, `price` |
| `flavor_not_found` | No flavor matched | `options` |
| `flavor_ambiguous` | Only a semantic match: confirm with the customer | `pizza`, `suggestion` |
| `unrecognized_option` | Size or crust not recognized | `pizza`, `alternatives`, `sizes`, `crusts` |
| `combination_unavailable` | The combination has no price | `pizza`, `size`, `crust`, `alternatives` |

The search query is embedded inside the Menu service. `relevance` is `1 - distance` for semantic results, and the
token-overlap count for the keyword fallback.

## Vector documents

One document per orderable pizza (chunk = menu item, never split):

| Field | Value |
|---|---|
| `id` / content hash | `pizza-{id}` |
| `content` | `"{sabor}. {descricao} Ingredientes: {ingredientes}. Tamanhos: …. Bordas: …. A partir de R$ x."` |
| `meta_data` | `{"pizza_id": int, "flavor": str, "min_price": float, "max_price": float}` |
