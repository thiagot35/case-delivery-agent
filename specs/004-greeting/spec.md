# Feature Spec: Greeting Intent, Intent Log and Stored Static Replies

| Field     | Value                                                    |
|-----------|----------------------------------------------------------|
| Feature   | 004-greeting                                             |
| Status    | Implemented                                              |
| Depends   | [003-guardrails](../003-guardrails/spec.md)              |
| Plan      | [plan.md](plan.md) · [tasks.md](tasks.md)                |

## Context

Before this feature, a customer's first "Oi" went to the order agent: a full model run with tools, just to say hello.
This feature adds a fourth router intent, `greeting`. It is answered with a static message built in code.

It also adds an intent log with one row per message. The log answers "is this the customer's first message of the
day?".

Finally, every static reply is now stored in the agent's history. Previously the order agent never saw those turns.

```
[Customer message] → [pre-filter] → [router: + "first message of the day?"]
   ├─► greeting (first message of the day, no item mentioned)
   │      ├─► new customer       → "Olá… temos diversos sabores como X, Y, Z e muito mais!"   (3 random flavors)
   │      ├─► returning customer → last order (items + total) + "Quer repetir esse pedido?"
   │      └─► order today        → order agent (it reports today's order)
   ├─► not_delivery / call_human / blocked (003)
   └─► delivery → order agent
Every static reply → stored in agent.db as a turn.   Every message → a row in intent.db.
```

## Requirements (from the business request)

| # | Requirement |
|---|---|
| R1 | A `greeting` intent: the first message of the day that doesn't explicitly mention an item ("Olá/Oi/Salve", "Bom dia/tarde/noite", "Tudo bem?", "Sim/Não"). The router agent is told whether the message is the first of the day. |
| R2 | The same messages are `delivery` when they are not the first of the day. A message that mentions an item ("Gostaria de comprar uma pizza de calabresa", "Vocês tem pizza de bacon?", "Queria saber o preço", "Quais são as opções") is never a greeting. |
| R3 | **New customer:** a static message with 3 random flavors from the menu: "Olá, tudo bem? Somos a Beauty Pizza e temos diversos sabores como sabor1, sabor2, sabor3 e muito mais! Você pode consultar e fazer sua escolha aqui pelo chat". |
| R4 | **Returning customer:** a static greeting that asks whether to repeat the last order. The order agent is not called. |
| R5 | An `intent.db` stores the customer message, intent, user_id, session_id, timestamp and other relevant fields. |
| R6 | Static replies are stored in `agent.db`, so later turns have the full conversation context. |

## Decisions

| Topic | Decision |
|---|---|
| First message of the day | No row in `intent.db` for this `user_id` on today's local date (`TIMEZONE`). Blocked messages are logged too, so they count. If the intent log can't be read, the message is treated as not the first, which means `delivery`. |
| Who decides `greeting` | The router agent, given `Primeira mensagem do cliente hoje: sim/não`. A deterministic guard turns `greeting` into `delivery` when it's not the first message. The 003 state check still runs first. |
| New vs returning | Decided from the Order API: any previous order with items means returning. If there is an order today, the order agent answers (flow step 1a). If the Order API is down, the customer is greeted as new. |
| Flavors | 3 distinct flavors, picked at random (`random.sample`) from `GET /menu`, which lists the orderable pizzas. The menu is read only through the Menu service API (constitution III). If the Menu service is down, the greeting is sent without flavors. |
| Returning greeting content | The last order's items (`2x Pizza Calabresa - Grande - Borda Tradicional`) and total, from the Order API. The state gets `greeted`, `repeat_offered`, `last_order_id` and `previous_flavors`, so on "sim" the agent calls `repeat_last_order`. |
| Static reply storage | Each static turn is stored as a completed Agno run of the order agent: user message + assistant reply, with metadata `static_route`. A blocked message is stored as `[mensagem bloqueada]`, so the injection text never enters the agent's context. The stored reply is exactly what was sent, PII notice included. |
| Output check on greetings | The greeting goes through the output guardrail, because flavor names come from data. If it fails, the greeting is sent without flavors. |

## Functional requirements

| ID | Requirement |
|---|---|
| FR-401 | `IntentClassification.intent` accepts `greeting`. The router prompt carries the first-message-of-the-day flag. |
| FR-402 | `greeting` with `first_message_of_day=False` → `delivery`. |
| FR-403 | `greeting` → `GreetingResponder.greet`. For a new customer, the reply has 3 menu flavors. For a returning customer, it has the last order's items and total and "Quer repetir esse pedido?". The response has `route=greeting`, and no model is called. |
| FR-404 | If the customer already has an order today, the greeting is skipped and the order agent answers. |
| FR-405 | `data/intent.db` (`INTENT_DB_PATH`), table `intent_log`, gets one row per message. It stores: `created_at`, `local_date`, `user_id`, `session_id`, `new_session`, `message` (redacted), `intent` (NULL if blocked), `method` (`state`, `llm`, `fallback` or `input_filter`), `reason`, `route`, `first_message_of_day`, `flow_step`, `customer_type`, `pii_found`, `human_requests` and `latency_ms`. A write failure never breaks the reply. |
| FR-406 | Every static reply (`greeting`, `not_delivery`, `human_handoff`, `blocked`) is appended to the session's history in `agent.db`. The session is created if needed, and the turn's state updates are merged. |
| FR-407 | After a returning-customer greeting, a "sim" makes the order agent repeat the last order. |
| FR-408 | `MessageResponse.route` accepts `greeting`. |

## Edge cases

- **A session that spans midnight:** the first message of the new day is a greeting candidate, but the 003 state check still routes "sim" in an active flow to `delivery`.
- **Session timeout:** stored static turns update the session's `updated_at`, so a greeting or an off-topic reply keeps the session active (this amends 003).
- **Fewer than 3 flavors on the menu:** the greeting has no flavor list.
- **Router failure:** fails open to `delivery`, as in 003, so no greeting is sent.

## Traceability

| Req | Implementation | Tests |
|---|---|---|
| R1, R2, FR-401/402 | `guardrails/router.py` | `test_router.py`, evals S11, S12 |
| R3, R4, FR-403/404/407 | `guardrails/greeting.py`, `messages.greeting_*`, `ConversationService._answer`, instruction "SAUDAÇÃO JÁ ENVIADA" | `test_greeting.py`, `test_api.py::test_greeting_*`, evals S1, S2, S10 |
| R5, FR-405 | `guardrails/intent_store.py`, `ConversationService.handle` | `test_intent_store.py`, `test_api.py::test_every_message_is_logged`, `test_api.py::test_intent_log_failure_does_not_break_the_reply`, eval S10 |
| R6, FR-406 | `agent/sessions.py` (`append_turn`) | `test_sessions.py`, `test_api.py::test_static_replies_are_stored_in_the_history`, eval S2 |
| FR-408 | `api/schemas.py` | `test_api.py::test_greeting_is_static` |
