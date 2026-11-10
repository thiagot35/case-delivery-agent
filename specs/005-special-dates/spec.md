# Feature Spec: Special Dates in the First Agent Reply

| Field     | Value                                                    |
|-----------|----------------------------------------------------------|
| Feature   | 005-special-dates                                        |
| Status    | Implemented                                              |
| Depends   | [004-greeting](../004-greeting/spec.md)                  |
| Plan      | [plan.md](plan.md) · [tasks.md](tasks.md)                |

## Context

When a holiday or commemorative date is close, the agent should use it to make its pizza suggestion more appealing
("Natal chegando…"). The dates come from a JSON file maintained by the business, which has national fixed dates,
movable dates and per-city holidays. A new optional `location` field on `POST /messages` selects the city.

## Requirements (from the business request)

| # | Requirement |
|---|---|
| R1 | A new optional parameter `location` on the messages endpoint. |
| R2 | It is used only for the first LLM message of the session. |
| R3 | If `location` is not set or empty, use `datas_fixas_nacionais` and `datas_moveis`. |
| R4 | If a commemorative date is 7 days away or less, mention it in a creative and appealing way when suggesting the pizza. |
| R5 | If `location` is set, also include the `Sao_Paulo`, `Rio_de_Janeiro` or `Florianopolis` dates, depending on the location. |
| R6 | Save the dates file in the project. |

## Decisions

| Topic | Decision |
|---|---|
| File | [special_dates.json](../../src/delivery_agent/domain/special_dates.json), saved verbatim (pt-BR data, constitution I.3). It is validated with pydantic at startup: `DD/MM`, `DD/MM/YYYY`, and the year of each movable date must match its key. |
| "First LLM message of the session" | The first order-agent run of the session. Static replies, such as the 004 greeting, are not LLM messages and don't use the dates. After a static greeting, the first agent reply still gets them. Tracked by `session_state.special_dates_sent`. |
| Window | Inclusive: from today (`days_until` 0) to 7 days ahead (`SPECIAL_DATES_WINDOW_DAYS`). Computed in code with the local date (`TIMEZONE`). Fixed dates are looked up this year and next, so New Year's Day is found in late December. |
| Location matching | Case-, accent- and punctuation-insensitive, with aliases: `São Paulo`/`SP`/`Sampa`, `Rio de Janeiro`/`Rio`/`RJ`, `Florianópolis`/`Floripa`. The JSON keys also match, and so do forms like `Rio de Janeiro - RJ`. An unknown city uses the national dates only and is not an error. |
| Delivery to the model | `session_state.upcoming_dates` (name, date, kind, days_until, city), nearest first, on the first agent run. Every later run gets `upcoming_dates: []`. Agno lets run parameters win over the stored state, so the list is cleared. |
| Tone and guardrails | The agent ties the suggestion (from `suggest_pizzas`) to the nearest date and mentions it once. It must not invent dates, and must not promise discounts, promotions, gifts or delivery deals because of a date (Black Friday, Dia do Cliente). |
| Movable dates outside 2026–2031 | Not listed in the file, so they are not mentioned. |
| Santa Catarina "transferido para o domingo" | The file has this rule only as text (`abrangencia`). The listed date is used. |

## Functional requirements

| ID | Requirement |
|---|---|
| FR-501 | `MessageRequest.location`: optional string, trimmed, max 100 chars, blank → `null`. |
| FR-502 | `SpecialDateCalendar.upcoming(today, location)` returns the national fixed and movable dates of the window, plus the city's dates for a known location, nearest first. |
| FR-503 | The first order-agent run of a session receives `upcoming_dates` and sets `special_dates_sent=true`. Later runs receive `upcoming_dates=[]`. |
| FR-504 | Instruction "DATAS ESPECIAIS": when `upcoming_dates` is not empty, tie the pizza suggestion to the nearest date creatively, mention it once, never invent dates or promise discounts. |
| FR-505 | `SPECIAL_DATES_PATH` and `SPECIAL_DATES_WINDOW_DAYS` are configurable. A malformed file fails at startup. |

## Traceability

| Req | Implementation | Tests |
|---|---|---|
| R1, FR-501 | `api/schemas.py` | `test_api.py::test_location_is_optional`, `test_api.py::test_location_too_long_is_rejected` |
| R2, FR-503 | `ConversationService._special_dates_state`, `state.py` | `test_api.py::test_dates_only_for_the_first_agent_reply`, `test_api.py::test_static_greeting_does_not_use_the_dates` |
| R3, R5, FR-502 | `domain/special_dates.py` | `test_special_dates.py`, `test_api.py::test_location_adds_city_dates` |
| R4, FR-504 | `agent/instructions.py` | `test_api.py::test_first_agent_reply_gets_national_dates`, eval S13 |
| R6, FR-505 | `special_dates.json`, `config.py`, `api/app.py` | `test_special_dates.py::test_malformed_date_fails_at_load` |
