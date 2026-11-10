# Implementation Plan: Conversation Guardrails

**Spec**: [spec.md](spec.md)

## Modules

```
src/delivery_agent/guardrails/
├── terms.json         term lists (prompt_injection, input_hard_block, profanity, competitors, undesired_terms)
├── terms.py           TermList (pydantic) + TermMatcher (whole-word, accent/case/punctuation-insensitive)
├── redaction.py       Redaction result + entity names (no Presidio import)
├── pii.py             PiiRedactor: Presidio analyzer (BrCpfRecognizer + CreditCardRecognizer) + anonymizer
├── filters.py         InputFilter (PII → injection → hard block) and OutputGuardrail (concurrent category checks)
├── router.py          state_check + IntentRouter (classification agent, fail-open)
├── rewriter.py        ReplyRewriter (tool-less agent, draft + critique)
├── handoff_store.py   HandoffStore (SQLite data/human_attendant.db, one ticket per session)
├── messages.py        static pt-BR replies
└── protocols.py       Redactor, IntentClassifier, Rewriter, HandoffRecorder
src/delivery_agent/agent/
├── runner.py          AgentRunner protocol (moved from api/service.py, re-exported there)
├── sessions.py        SessionAccess + AgnoSessionAccess (read state / last reply, merge state, patch last reply)
└── factory.py         + tool_call_limit, build_router_agent, build_rewriter_agent
src/delivery_agent/api/service.py   ConversationService runs the pipeline; Guardrails dataclass
```

## Request flow (`ConversationService.handle`, under the per-customer lock)

1. Resolve the session (unchanged).
2. `InputFilter.check(message)`. Only the redacted text is used from here on. If blocked → static reply.
3. Read the state and the last reply (`SessionAccess`; empty for a new session), then call `IntentRouter.classify`.
4. Branch:
   - `not_delivery` → static reply.
   - `call_human`: `human_requests += 1`. At the threshold, persist the counter (`update_state`), record the ticket and send the static handoff reply.
   - Otherwise → the order agent with `session_state={"customer_first_name", "human_requests"}`. Agno merges run parameters over the stored state.
5. `OutputGuardrail.check(reply)`.
   - If it fails → `ReplyRewriter.rewrite`, then check again.
   - If that fails too → the "pode reformular?" reply.
   - Then `replace_last_reply`.
6. If PII was removed, append the notice. The response's `route` is set.

## Agno usage

- **Order agent:** `tool_call_limit=10`.
- **Router:**
  - `Agent(output_schema=IntentClassification)` with no tools and no db, so `run.content` is the pydantic model.
  - The model is `ROUTER_MODEL_ID`, defaulting to `MODEL_ID`.
- **Rewriter:** `Agent` with no tools and no db.
- **Session access for the static routes:**
  - `aget_session` / `asave_session` to merge state.
  - `aget_last_run_output` for the last reply.
  - Patching `runs[-1].content` and its last assistant message keeps the history in step.

## Configuration

| Variable | Default |
|---|---|
| `ROUTER_MODEL_ID` | `MODEL_ID` |
| `AGENT_TOOL_CALL_LIMIT` | `10` |
| `HUMAN_HANDOFF_THRESHOLD` | `3` |
| `HUMAN_ATTENDANT_DB_PATH` | `data/human_attendant.db` |
| `GUARDRAIL_TERMS_PATH` | packaged `guardrails/terms.json` |

## Dependencies

`presidio-analyzer`, `presidio-anonymizer` and `pt_core_news_sm` 3.8.0, a wheel URL in `[tool.uv.sources]`. They
pull in spaCy, thinc and blis. With them, the lock resolves numpy to 2.4.6 (it was 2.5.4). Presidio loads in about
0.5 s at startup and redacts a message in about 6 ms.

## Logging

Each event is one key=value line, and the message text is never logged:
- `guardrail input pii=[...]`
- `guardrail input blocked=<reason> terms=[...]`
- `intent=<intent> method=state|llm|fallback human_requests=<n>`
- `human_handoff created=<bool> count=<n>`
- `guardrail output_violation [(category, term)]`
- `guardrail rewrite=ok|failed`
