# Implementation Plan: Greeting Intent, Intent Log and Stored Static Replies

**Spec**: [spec.md](spec.md)

## Modules

```
src/delivery_agent/guardrails/
├── router.py        + greeting intent, first_message_of_day in the prompt, guard, RunStatus.error → fallback
├── greeting.py      GreetingResponder (Order API history, random menu flavors), Greeting
├── intent_store.py  IntentStore (SQLite data/intent.db), IntentLogEntry
├── messages.py      + greeting_new, greeting_returning, BLOCKED_PLACEHOLDER
├── rewriter.py      RunStatus.error → "" (the error text is never sent)
└── protocols.py     + Greeter, IntentRecorder; IntentClassifier.classify(..., first_message_of_day)
src/delivery_agent/agent/sessions.py   + append_turn; replace_last_reply persists the run (db.upsert_run)
src/delivery_agent/api/service.py      first-of-day lookup, greeting branch, static turns stored, intent log
```

## Request flow (`ConversationService.handle`, under the per-customer lock)

1. Resolve the session, then run the input filter (003).
2. `first_of_day = not intents.has_message_on(user_id, today)`.
3. Read the stored state. Route with `router.classify(message, flow_step, last_reply, first_of_day)`.
4. On `greeting`, call `greeter.greet(customer, today)`. A `Greeting` → output check → static turn. `None` (an order today) → the order agent.
5. Append the PII notice. For a static turn, `sessions.append_turn(session_id, user_id, stored_message, reply, state_updates, route)`.
6. Record an `IntentLogEntry`. Errors are logged and ignored.

## Agno storage

The installed Agno keeps runs in their own table (`agno_runs`). `Agent.asave_session` writes only the session row
(`upsert_session` uses `to_dict(include_runs=False)`). So:
- **`append_turn`:**
  - Builds an `AgentSession` (for a new session, its state filled with `ensure_defaults`) and a `RunOutput`. The run has `agent_id` = the order agent, `status=completed`, `metadata.static_route`, and user + assistant messages.
  - Then it calls `session.upsert_run(run)`, `asave_session(session)` and `db.upsert_run(run, run_index=resolve_run_index(...))`.
  - `get_messages` (the history) and `aget_last_run_output` (the router's last reply) both return this run.
- **`replace_last_reply`:** patches `runs[-1]` and writes it with `db.upsert_run`. This fixes the 003 bug: before, the patch was only in memory.

## Router prompt

`Primeira mensagem do cliente hoje: sim|não` comes first, before the step, the last reply and the message. The new
`greeting` rule lists the business examples. It says that any mention of a pizza, flavor, price, menu, options or
the order is `delivery`, and that a greeting is `delivery` when it's not the first message of the day.

## Configuration

| Variable | Default |
|---|---|
| `INTENT_DB_PATH` | `data/intent.db` |

## Logging

New key=value events (the message text is never logged):
- `route=<route> first_message_of_day=<bool>`
- `greeting customer_type=new|returning`
- `greeting skipped: the customer has an order today`
- `intent log write failed`
