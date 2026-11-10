# Contract: Order API Client (consumed)

Source of truth: `candidates-case-order-api` → `order/urls.py`, `order/views.py`, `order/serializers.py`,
`order/models.py`. Base URL: `ORDER_API_BASE_URL` (default `http://localhost:8000/api`). There is no authentication.

`OrderApiClient` (`clients/order_api_client.py`) wraps a shared `httpx.AsyncClient` and returns domain models
([data-model.md §2](../data-model.md)).

## Methods

### `create_order(customer, delivery_date, items, address=None) -> Order`

`POST /orders/` (`OrderCreateView` → `OrderSerializer.create`)

```json
{
  "client_name": "Thiago Santana",
  "client_document": "123456*****",
  "delivery_date": "2026-11-06",
  "items": [{"name": "Pizza Calabresa - Grande - Borda Tradicional", "quantity": 1, "unit_price": 47.00}],
  "delivery_address": {"street_name": "Rua das Flores", "number": "100", "complement": "Ap 12", "reference_point": null}
}
```

- `201` → full `Order` (with `id`, nested `items` incl. ids, `total_price`).
- `400` with `non_field_errors` containing the unique-set message → `OrderAlreadyExistsError`. The Order API enforces
  `unique_together(client_name, client_document, delivery_date)`.
- `400` other → `OrderApiValidationError(errors)`.
- `delivery_address` is optional. Omit it when unknown.

### `get_order(order_id) -> Order`

`GET /orders/{id}/` → `200` `Order` (includes `total_price` = Σ quantity × unit_price). `404` → `OrderNotFoundError`.

### `find_orders(client_document, delivery_date=None) -> list[Order]`

`GET /orders/filter/?client_document={doc}[&delivery_date=YYYY-MM-DD]` → `200` list (no pagination, no guaranteed
order). The client sorts by `id` descending, so the first element is the most recent order.

### `add_items(order_id, items) -> None`

`PATCH /orders/{id}/add-items/` with `{"items": [{name, quantity, unit_price}, ...]}`

- `200` `{"detail": "N itens adicionados com sucesso."}`. The response doesn't contain item ids, so callers must call
  `get_order` afterwards.
- `400` `{"errors": [...]}` → `OrderApiValidationError`. **Caveat**: the view saves the valid items before
  reporting errors (no transaction), so after a 400 the caller must re-read the order instead of assuming nothing
  changed.
- `404` → `OrderNotFoundError`.

### `remove_item(order_id, item_id) -> None`

`DELETE /orders/{id}/items/{item_id}/` → `204`. `404` (item not in order) → `ItemNotFoundError`.
- **Caveat**: the `204` carries a JSON body (`{"detail": "Item removido com sucesso."}`). httpx treats 204 as
  bodyless, so on a reused keep-alive connection the next response fails with `illegal status line`. The client
  sends `Connection: close` on every delete.

### `update_address(order_id, address) -> None`

`PATCH /orders/{id}/update-address/` with `{"delivery_address": {street_name, number, complement?, reference_point?}}`

- `200` `{"detail": "Endereço atualizado com sucesso."}`. `400` if `delivery_address` is missing. `404` order not found.
- **Caveat**: the view does `setattr` for each provided key and doesn't validate. The client must always send all four
  keys, with `null` for absent optional fields, so stale `complement`/`reference_point` values from a previous address
  are cleared.

## Error model (`clients/errors.py`)

```
OrderApiError (base)
├── OrderApiUnavailableError   # timeout, connection error, 5xx
├── OrderApiValidationError    # 400, carries `errors`
│   └── OrderAlreadyExistsError
├── OrderNotFoundError         # 404 on order
└── ItemNotFoundError          # 404 on item delete
```

## Transport policy

| Setting     | Value                                                                 |
|-------------|-----------------------------------------------------------------------|
| Timeout     | `ORDER_API_TIMEOUT_SECONDS` (5 s) for connect and read                |
| Retries     | 1 retry with 300 ms backoff, **GET only**, on timeout/connect/5xx     |
| Decimal     | Requests send `unit_price` as a string with 2 decimals (`"47.00"`): DRF `DecimalField` parses it without float rounding. Responses are parsed into `Decimal`. |
| Logging     | Method, path, status, latency. CPF in query strings is masked.        |

## Known Order API limitations (handled by the agent, not fixed)

| Limitation | Impact | Handling |
|------------|--------|----------|
| No order status field | Can't mark "placed" | Confirmation lives in session state |
| `updated_at` not bumped by add-items / address update | Unreliable for activity | Not used ([research R-02](../research.md)) |
| One order per name+document+date | No 2nd order the same day | Adopt today's order ([research R-04](../research.md)) |
| No quantity update endpoint | Changing quantity needs delete + add | `remove_item` + `add_pizza` |
| `quantity` accepts 0 | Invalid items possible | Validated client-side (`ge=1`) |
| No delete-order endpoint | Can't cancel in the API | Remove all items + `cancelled` status |
| Item delete returns `204` with a body | Corrupts the next response on the same connection | `Connection: close` on delete |
