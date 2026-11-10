# Feature Spec: Short Replies and a Smaller History

| Field     | Value                                                    |
|-----------|----------------------------------------------------------|
| Feature   | 007-short-replies                                        |
| Status    | Implemented                                              |
| Depends   | [001-pizza-ordering-agent](../001-pizza-ordering-agent/spec.md), [005-special-dates](../005-special-dates/spec.md) |
| Plan      | [plan.md](plan.md) · [tasks.md](tasks.md)                |

## Context

When a new customer asked "quais pizzas vocês têm?", the order agent sent the whole menu: each of the 6 flavors with
its ingredients and all of its size and crust prices. The reply ran to 1,718 characters (measured in a live run). That
is too long for a chat.

Each run also sends the previous runs of the session to the model, tool results included. The business asked to use
Agno's `max_tool_calls_from_history` to cut those tokens. **Cost is the priority.**

## Requirements (from the request)

| # | Requirement |
|---|---|
| R1 | Avoid long answers. |
| R2 | Don't send the full menu. Present it in a sentence like "Temos pizzas de [2 sabores] entre outros nos tamanhos pequeno a partir de X, médio a partir de X e grande a partir de X". |
| R3 | When the customer asks about a specific flavor, all the options for that flavor may be sent. |
| R4 | Use the `max_tool_calls_from_history` parameter to reduce the processed tokens. |
| R5 | Cost is the priority (follow-up decision, after the measurement in [plan.md](plan.md)). |

## Decisions

| Topic | Decision |
|---|---|
| Brevity | A persona rule: a few short sentences (preferably up to 3), and nothing the conversation already said. The exceptions are the order review (step 4) and the options of a flavor the customer asked about. |
| Menu presentation | `list_menu` returns a summary instead of the rendered menu. The summary has the flavor names with their starting prices, the starting price of each size (`from_price_by_size`) and the crusts. The model can't send what it doesn't receive, and the prices still come from a tool (constitution II). The full options of one flavor come from `get_pizza_details`. "Quais pizzas vocês têm?", "quais as opções?" and "me mostra o cardápio" all get the one-sentence template. Only an explicit request for every flavor gets the list of names. |
| The 2 flavors | They are the 2 `suggest_pizzas` picks: relevant, personalized and never random. Without suggestions, the model uses any 2 flavors from `list_menu`. |
| "A partir de" price | The cheapest price of that size across all flavors and crusts, computed in code from the Menu service menu. A size with no price is left out. |
| `max_tool_calls_from_history` | Set from `AGENT_MAX_TOOL_CALLS_FROM_HISTORY` (default 3). It applies only when Agno replays the context. With response chaining on (below), it has no effect. It is kept so that turning chaining off trims the history immediately. |
| Response chaining kept (cost) | For `gpt-5*` and `gpt-6*` models, Agno chains each request to the previous OpenAI response (`previous_response_id`) and sends only the new messages. OpenAI rebuilds the conversation from its stored response and serves about 91% of it from its cache. Replaying the context instead (`use_previous_response_id=False`) made the history limits work and cut input tokens by 19%. But Agno writes the time and the session state into the system prompt, and any change there resets this model's cache. So 45% of the input was billed at the full rate, and the cost rose 64%. Chaining is kept. |
| Known limits of chaining | `NUM_HISTORY_RUNS` and `max_tool_calls_from_history` don't reach the model. The system prompt, with the session state, is sent on the first request of the session only. Later turns know the state from the tool results in the conversation. |
| Router and rewriter | Unchanged. They have no history, so there is nothing to chain or trim. |

## Edge cases

| Case | Behavior |
|---|---|
| "Quais pizzas vocês têm?" / "me mostra o cardápio" | The one-sentence template, with the 2 suggestions and the starting price of each size. |
| "Quais são todos os sabores?" | The flavor names in one line, from `list_menu`. |
| The model needs an older tool result | It is still in the chained conversation. |
| Special date in the first agent reply | Mentioned in one short sentence with the suggestion ([005](../005-special-dates/spec.md)). |
| Menu service down | `menu_unavailable`, as before. |

## Functional requirements

| ID | Requirement |
|---|---|
| FR-701 | `list_menu` returns the flavor names with their starting prices, the starting price of each size and the crusts. It never returns descriptions, ingredients or the full price table. |
| FR-702 | The instructions ask for short replies, the one-sentence menu presentation, and the full options only for a flavor the customer asked about. |
| FR-703 | The order agent is built with `max_tool_calls_from_history = AGENT_MAX_TOOL_CALLS_FROM_HISTORY`. |
| FR-704 | The order agent's model keeps response chaining, for cost. |

## Traceability

| Req | Implementation | Tests |
|---|---|---|
| R2, FR-701 | `formatting.menu_summary`, `MenuTools.list_menu` | `test_domain.py::test_menu_summary_has_starting_prices`, `test_domain.py::test_menu_summary_skips_a_size_without_prices`, `test_menu_tools.py::test_list_menu_is_a_summary` |
| R1–R3, FR-702 | `agent/instructions.py` | eval `test_conversations.py::test_s14_menu_is_one_sentence_and_a_flavor_is_detailed` |
| R4, FR-703 | `config.py`, `agent/factory.py` | `test_agent_factory.py::test_order_agent_trims_tool_calls_from_history` |
| R5, FR-704 | `agent/factory.build_model` | `test_agent_factory.py::test_order_agent_model_chains_responses`, live measurement ([plan.md](plan.md)) |
