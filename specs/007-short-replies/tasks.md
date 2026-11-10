# Tasks: Short Replies and a Smaller History

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Definition of done for every task: `ruff check` passes, the related tests pass, and every function has a docstring.

- [x] **T701** Measure the current behavior live: the reply length, the token usage per run, and whether the history settings reach the model. Found: the full menu in the first reply, and response chaining.
- [x] **T702** `menu_summary` and `list_menu` returning it. **Done when** `test_domain.py::test_menu_summary_*` and `test_menu_tools.py::test_list_menu_is_a_summary` pass.
- [x] **T703** Instructions: short replies, the one-sentence menu, a flavor's options in full, step 1c, special dates in one sentence. **Done when** eval S14 passes.
- [x] **T704** `AGENT_MAX_TOOL_CALLS_FROM_HISTORY` and `max_tool_calls_from_history`. **Done when** `test_agent_factory.py::test_order_agent_trims_tool_calls_from_history` passes.
- [x] **T705** Measure replay against chaining (see [plan.md](plan.md)). Decision, with cost as the priority: keep chaining. **Done when** `test_agent_factory.py::test_order_agent_model_chains_responses` passes.
- [x] **T706** README, `.env.example`, the `list_menu` contract.
