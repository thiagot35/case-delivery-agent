# Contract: Delivery Agent HTTP API (provided by this project)

Base URL: `http://{HOST}:{PORT}` (default `http://127.0.0.1:8080`). Content type: `application/json; charset=utf-8`.

## `POST /messages`

Sends one customer message to the agent and returns the agent's reply.

### Request

```json
{
  "client_name": "Thiago Santana",
  "client_document": "123456*****",
  "message": "Oi, quero uma pizza de calabresa grande",
  "location": "Florianópolis"
}
```

| Field             | Type   | Rules                                                                      |
|-------------------|--------|----------------------------------------------------------------------------|
| `client_name`     | string | Required, trimmed, 1–300 chars (Order API `client_name` max length)        |
| `client_document` | string | Required, CPF. Punctuation is stripped, the result must be 11 digits with valid check digits, and repeated-digit CPFs (`111.111.111-11`) are rejected. |
| `message`         | string | Required, trimmed, 1–2000 chars                                             |
| `location`        | string | Optional, trimmed, max 100 chars; blank = not set. The customer's city: São Paulo, Rio de Janeiro or Florianópolis add their local holidays to the first agent reply of the session ([005-special-dates](../../005-special-dates/spec.md)). Other values use the national dates only. Ignored after the first agent reply. |

### Response `200 OK`

```json
{
  "reply": "Olá, Thiago! Anotei 1 Pizza Calabresa Grande com borda Tradicional (R$ 47,00). O total do seu pedido está em R$ 47,00. Deseja mais alguma coisa?",
  "session_id": "6f1c2a1e-3b7d-4a7e-9a52-1f0c9b7d2e11",
  "new_session": false,
  "order": {
    "order_id": 12,
    "status": "open",
    "delivery_date": "2026-11-06",
    "items": [
      {"item_id": 31, "name": "Pizza Calabresa - Grande - Borda Tradicional", "quantity": 1, "unit_price": 47.0, "subtotal": 47.0}
    ],
    "delivery_address": null,
    "total_price": 47.0
  }
}
```

| Field         | Type                 | Notes                                                    |
|---------------|----------------------|----------------------------------------------------------|
| `reply`       | string               | Agent message in pt-BR                                   |
| `session_id`  | string (UUID)        | Agno session used for this message                       |
| `new_session` | bool                 | True when this message started a new session             |
| `order`       | `OrderSnapshot`/null | Snapshot of the current order. Null if none yet.         |

`OrderSnapshot.status` ∈ `open | confirmed | cancelled`.

### Errors

| Status | When                                                     | Body                                                     |
|--------|----------------------------------------------------------|----------------------------------------------------------|
| 422    | Invalid payload / invalid CPF                            | FastAPI validation error format                          |
| 503    | Model provider unavailable after the agent run fails     | `{"detail": "Agent temporarily unavailable"}`            |
| 500    | Unexpected error                                         | `{"detail": "Internal error"}`. The details are logged with the masked CPF. |

Order API failures do **not** produce HTTP errors here. The agent explains them to the customer in the `reply`.

### Behavior

- Requests for the same `client_document` are processed sequentially (per-customer lock).
- Session resolution follows [plan.md §3.1](../plan.md).

## `GET /health`

`200 OK` → `{"status": "ok", "order_api": "ok" | "unreachable", "menu_index": "ready" | "missing"}`.
It checks the Order API with `GET /api/orders/filter/?client_document=00000000000` (empty list expected).
