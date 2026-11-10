# Contract: Agent Tools

> **Amended by [002-menu-service](../../002-menu-service/spec.md).** The menu (data, flavor resolution, pricing and semantic search) now lives in a separate
> Menu service. The agent reads it only through the [Menu service API](../../002-menu-service/contracts/menu-service-api.md), and every agent tool that
> touches the menu calls that API. Where this document describes the agent reading `menu.db`/LanceDB directly, the
> 002 documents take precedence.
> The tool names, arguments and results below are unchanged. A Menu service outage returns
> `{"ok": false, "error_code": "menu_unavailable"}`. No tool changes the menu.

Tools are Agno `Toolkit` methods. They implement the flowchart's 🟨 yellow nodes (Order API / menu calls). The 🟦 light
blue nodes (customer interactions) are agent turns, not tools. A tool never talks to the customer, and the agent never
asks the customer a question that a tool could answer from data (see [flowchart-review.md §0](../flowchart-review.md)).

Conventions:

- Every tool receives `run_context: RunContext`. It is hidden from the model, and the tool reads
  `run_context.dependencies["customer"]` (`CustomerContext`), `run_context.dependencies["today"]` and
  `run_context.session_state` from it.
- Tool **docstrings** (which the model reads) are in English. The **returned text** is concise and factual. Customer-facing
  fragments (menu lines, prices) are pre-formatted in pt-BR so the model can relay them verbatim.
- Results are returned as compact JSON strings: `{"ok": true, ...}` or
  `{"ok": false, "error_code": "...", "message": "...", ...}`. Tools never raise to the model.
- Money is formatted `R$ 47,00` in `*_display` fields. Raw values are strings with 2 decimals.
- Every write tool refreshes `session_state.order_snapshot` from `GET /orders/{id}/` and returns it.

## Shared error codes

| `error_code`             | Meaning                                                    |
|--------------------------|------------------------------------------------------------|
| `flavor_not_found`       | No flavor matched. `options` lists all flavors.            |
| `flavor_ambiguous`       | Semantic-only match. `suggestion` holds the candidate to confirm. |
| `combination_unavailable`| Size/crust not offered for the flavor. `alternatives` lists valid combos with prices. |
| `invalid_quantity`       | Quantity < 1 or > 20                                       |
| `no_open_order`          | Tool requires an order but there is none                   |
| `item_not_found`         | `item_id` not in the current order                         |
| `missing_items`          | Confirm attempted with no items                            |
| `missing_address`        | Confirm attempted without an address                       |
| `no_history`             | No previous orders                                         |
| `no_address_candidate`   | `confirm_delivery_address` called before `get_delivery_address` |
| `invalid_address`        | Street or number missing / too long                        |
| `order_api_unavailable`  | Order API down / timeout                                   |
| `order_api_rejected`     | Order API 400 with details                                 |

---

## MenuTools (`tools/menu_tools.py`)

### `list_menu() -> str`
Returns a menu summary ([007-short-replies](../../007-short-replies/spec.md)):
`{flavors: [{flavor, from_price_display}], from_price_by_size: [{size, from_price_display}], crusts}`. It has no
descriptions, ingredients or price table, so the agent can't send the full menu. Used to present the menu in one
sentence. Errors: `menu_unavailable`. No side effects.

### `get_pizza_details(flavor: str) -> str`
Resolves the flavor ([plan §3.5](../plan.md)) and returns `{flavor, description, ingredients, prices: [{size, crust,
price_display}]}`. Errors: `flavor_not_found`, `flavor_ambiguous`.

### `get_price(flavor: str, size: str, crust: str = "Tradicional") -> str`
Returns `{available: true, flavor, size, crust, price, price_display}` or `combination_unavailable` with
`alternatives`. No side effects.

### `search_menu(query: str, limit: int = 3) -> str`
Semantic search over the LanceDB index. Returns `[{flavor, description, ingredients, from_price_display, score}]`,
joined with KB data. Used for descriptive questions ("sem carne", "algo doce"). `limit` is clamped to 1..6.

### `suggest_pizzas(preferences: str | None = None) -> str`
Returns exactly 2 suggestions `[{flavor, reason, from_price_display}]`.
- With `preferences`: semantic search on them.
- Without, for a returning customer: query built from previously ordered flavors, which are excluded from the results.
- Without, for a new customer: default query ("pizzas clássicas mais pedidas").

`reason` cites matching ingredients. It is never random.

---

## OrderTools (`tools/order_tools.py`)

### `get_customer_history() -> str`
Calls `find_orders(document)` (sorted by id desc). Returns:
```json
{"ok": true, "has_history": true,
 "today_order": {"order_id": 12, "items": [...], "total_price_display": "R$ 47,00"} ,
 "last_order": {"order_id": 9, "delivery_date": "2026-10-30", "items": [...], "total_price_display": "R$ 85,00"}}
```
- `today_order` = the order with `delivery_date == today`, if any.
- `last_order` = the most recent order **before** today.

The address is deliberately **not** returned here. It is fetched later, at the address step, by `get_delivery_address`,
so the greeting stays short and the address is read fresh from the Order API when it matters.

Side effects: sets `session_state.last_order_id`, and if `today_order` exists and `order_id` is None, adopts it
(`order_id`, `order_status=open`).

### `get_current_order() -> str`
Returns the refreshed `OrderSnapshot` with `total_price_display` and per-item `subtotal_display`. Error `no_open_order`.

### `add_pizza(flavor: str, size: str, crust: str = "Tradicional", quantity: int = 1) -> str`
1. Validate `quantity` (1..20). Resolve the flavor, size and crust, then the price (`MenuRepository.get_price`).
2. Build the item: `name = build_item_name(...)`, `unit_price` from the KB.
3. If `order_id` is None: look up today's order (adopt it if found), else `create_order` with this item. If creation
   raises `OrderAlreadyExistsError`, re-query and adopt, then `add_items`.
4. Else `add_items(order_id, [item])`. If this raises `OrderNotFoundError`, reset the state and go back to step 3.
5. Refresh the snapshot. Set `order_status=open` (also when it was `confirmed` or `cancelled`) and `flow_step=building`.

Returns `{ok, added: {name, quantity, unit_price_display}, order: OrderSnapshot}`.
Errors: `invalid_quantity`, `flavor_not_found`, `flavor_ambiguous`, `combination_unavailable`, `order_api_*`.

### `remove_item(item_id: int) -> str`
Requires an open order and an `item_id` from the current snapshot (the model reads ids from `get_current_order` /
the snapshot). Calls `remove_item`, refreshes, sets `order_status=open`. Errors: `no_open_order`, `item_not_found`.

### `repeat_last_order() -> str`
Requires `last_order_id` (else `no_history`). Steps:
1. Parse each item name of the last order (`parse_item_name`). Skip foreign or no-longer-valid combinations.
2. Re-price from the KB.
3. Create today's order (or adopt today's existing one and append) with the items and the last address.

Returns `{ok, added_items, skipped_items, price_changes: [{name, old, new}], order}`. Sets `repeat_offered=true`.

### `get_delivery_address() -> str`
Called when the customer finishes choosing items, **before** the agent asks anything about the address. Read-only.
1. `get_order(order_id)`. If the current order has a `delivery_address`, the candidate is that address
   (`source = "current_order"`).
2. Otherwise `find_orders(document)`: take the most recent **previous** order (id ≠ current) with a non-null address
   (`source = "last_order"`, plus `from_order_id`).
3. Otherwise `source = "none"`.

Returns:
```json
{"ok": true, "source": "last_order", "from_order_id": 9,
 "address": {"street_name": "Rua das Flores", "number": "100", "complement": "Ap 12", "reference_point": "Perto da praça"},
 "address_display": "Rua das Flores, 100 - Ap 12 (Perto da praça)"}
```
or `{"ok": true, "source": "none", "address": null}`. Stores the result in `session_state.address_candidate` and sets
`flow_step=address`. Error: `no_open_order`.

Agent behavior after the call (🟦): with a candidate it asks "Podemos entregar em {address_display}?". With `none` it
asks for street, number, complement and reference point.

### `confirm_delivery_address() -> str`
The customer accepted the candidate. Uses `session_state.address_candidate` (never LLM arguments):
- `source = "current_order"`: no API write (the address is already on the order).
- `source = "last_order"`: `update_address(order_id, candidate)` copies it to the current order.

Sets `address_set=true`, clears `address_candidate`, `flow_step=review`. Returns the refreshed `OrderSnapshot`.
Errors: `no_address_candidate` (call `get_delivery_address` first), `no_open_order`.

### `set_delivery_address(street_name: str, number: str, complement: str | None = None, reference_point: str | None = None) -> str`
A new address typed by the customer: no candidate, a changed address, or an address volunteered earlier in the chat.
Validates with `DeliveryAddress` and calls `update_address` with all 4 keys. Sets `address_set=true`, clears
`address_candidate`, sets `order_status=open` if it was confirmed, `flow_step=review`. Requires an open order
(`no_open_order`, because the address belongs to an order in the API). Errors: `invalid_address` (missing street or number).

### `confirm_order() -> str`
Refreshes the order and checks the invariants: ≥ 1 item (`missing_items`) and an address (`missing_address`). On success
it sets `order_status=confirmed`, `confirmed_at=now`, `flow_step=confirmed`. Returns `{ok, order_id, total_price_display,
delivery_address, items}`.

### `cancel_order() -> str`
Removes every item of the current order (the API has no delete-order endpoint), sets `order_status=cancelled`,
`order_id` kept (so it can be reopened today), `flow_step=start`. Returns `{ok, order_id}`.

---

## Tool-to-flow mapping

| Revised flow step (flowchart-review.md §5)   | Tool(s)                                   |
|----------------------------------------------|-------------------------------------------|
| Pedidos históricos? / último pedido          | `get_customer_history`                    |
| Repetir último pedido                        | `repeat_last_order`                       |
| Cardápio + 2 sugestões                       | `list_menu`, `suggest_pizzas`             |
| Perguntas sobre o cardápio (anytime)         | `get_pizza_details`, `get_price`, `search_menu` |
| Adicionar / remover / alterar itens          | `add_pizza`, `remove_item`                |
| Total atual                                  | returned by every write, `get_current_order` |
| Buscar endereço (before "Endereço é o mesmo?") | `get_delivery_address`                  |
| Endereço é o mesmo? → Sim                    | `confirm_delivery_address`                |
| Endereço é o mesmo? → Não / sem endereço     | `set_delivery_address`                    |
| Resumo + confirmação                         | `get_current_order`, `confirm_order`      |
| Cancelar                                     | `cancel_order`                            |
