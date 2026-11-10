# Implementation Plan: Short Replies and a Smaller History

**Spec**: [spec.md](spec.md)

## Changes

```
src/delivery_agent/domain/formatting.py   menu_summary() replaces render_menu()
src/delivery_agent/tools/menu_tools.py    list_menu returns the summary (JSON, like the other tools)
src/delivery_agent/agent/instructions.py  RESPOSTAS CURTAS, APRESENTAR O CARDÁPIO, step 1c, special dates in one sentence
src/delivery_agent/config.py              AGENT_MAX_TOOL_CALLS_FROM_HISTORY (default 3)
src/delivery_agent/agent/factory.py       max_tool_calls_from_history; response chaining kept (documented)
```

## `list_menu` result

```json
{"ok": true,
 "flavors": [{"flavor": "Margherita", "from_price_display": "R$ 25,00"}, "..."],
 "from_price_by_size": [{"size": "Pequena", "from_price_display": "R$ 25,00"},
                        {"size": "Média", "from_price_display": "R$ 35,00"},
                        {"size": "Grande", "from_price_display": "R$ 45,00"}],
 "crusts": ["Tradicional", "Recheada com Cheddar", "Recheada com Catupiry"]}
```

## How the history reaches the model

1. Agno loads the last `NUM_HISTORY_RUNS` runs of the session.
2. `filter_tool_calls` keeps only the last `AGENT_MAX_TOOL_CALLS_FROM_HISTORY` tool results and their calls.
3. With response chaining (the default for `gpt-5*`/`gpt-6*`, kept), the request carries only the messages after the
   last stored response, plus its `previous_response_id`. OpenAI rebuilds the rest, so steps 1 and 2 don't change
   what the model processes. With `use_previous_response_id=False`, the request would carry the new system prompt
   and the trimmed history instead.

## Live measurement

The same 6 customer messages were sent to a scratch instance with its own databases: the menu question, the Calabresa
prices, two pizzas, "só isso" and the address. The model was `gpt-6-luna`, with `NUM_HISTORY_RUNS=5` and
`AGENT_TOOL_CALL_LIMIT=5` from `.env`. The token counts come from Agno's run metrics in `agent.db`. The cost uses the
Langfuse price of the model: $0.10 per 1M input tokens, $0.01 per 1M cached input tokens and $0.50 per 1M output tokens.

| | Before (chained, full menu) | Replay + trimmed history + short replies | **Shipped: chained + short replies** |
|---|---|---|---|
| First reply (menu) | 1,718 characters, every flavor and price | 290 characters, the template | The template, about 300 characters |
| Input tokens | 76,319 | 61,451 (−19%) | 65,524 (−14%) |
| Cached input | 91% | 55% | 91% |
| Output tokens | 1,657 | 1,038 (−37%) | 1,072 (−35%) |
| Estimated cost | $0.0022 | $0.0036 (+64%) | **$0.0017 (−23%)** |

The savings come from the shorter replies and the smaller `list_menu` result. They stay in the chained conversation,
so every later turn is cheaper too.

**Why replay cost more.** For this model, any change in the system (developer) content invalidates the whole cached
prompt. Different user messages keep the cache: 1,919 of 1,930 tokens were cached in a direct API probe. Agno writes
the time and the session state into the system prompt, and the state includes `current_run_id`, which changes on every
run. So with replay, the first call of each run was not cached. The calls inside a run were cached up to the previous
call.

**If replay is wanted later**, it needs a system prompt that is identical on every run, with the time and the session
state moved into the user message. Then `max_tool_calls_from_history` takes effect with no cache loss. A session
created with chaining on was continued with replay without errors: Agno sends old function calls without item ids.
