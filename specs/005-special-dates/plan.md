# Implementation Plan: Special Dates in the First Agent Reply

**Spec**: [spec.md](spec.md)

## Modules

```
src/delivery_agent/domain/
├── special_dates.json   the business file, verbatim
└── special_dates.py     schema (pydantic), resolve_city, SpecialDateCalendar.upcoming, UpcomingDate
src/delivery_agent/api/schemas.py      MessageRequest.location
src/delivery_agent/api/service.py      ConversationService(special_dates=...), _special_dates_state
src/delivery_agent/agent/state.py      upcoming_dates, special_dates_sent
src/delivery_agent/agent/instructions.py   "DATAS ESPECIAIS"
src/delivery_agent/api/app.py          SpecialDateCalendar.load(SPECIAL_DATES_PATH, SPECIAL_DATES_WINDOW_DAYS)
```

## Flow

1. `POST /messages` accepts `location`. A blank value becomes `None`.
2. The pipeline from 003/004 runs unchanged. Static replies don't touch the dates.
3. Just before the order agent runs, `_special_dates_state(state, location)` decides what to send:
   - `state.special_dates_sent` is true: send `{"upcoming_dates": []}`.
   - Otherwise: send `{"upcoming_dates": [d.as_state() for d in calendar.upcoming(today, location)], "special_dates_sent": True}`.

   The result is merged into the run's `session_state`. Agno's precedence is run parameters > stored state, and lists are replaced, not merged.
4. The model sees `upcoming_dates` in the session state (`add_session_state_to_context`) and follows "DATAS ESPECIAIS".

## Calendar

- **Fixed dates:** the next occurrence on or after today, this year or next. A 29/02 in a non-leap year is skipped.
- **Movable dates:** `datas_por_ano` for this year and next.
- **City dates:** only for `resolve_city(location)`.
- **Window:** `0 <= days_until <= window_days`. Results are sorted by `(days_until, name)`.

## Logging

`special_dates city=<key|None> upcoming=[names]` is logged on the first agent run. The free-text location itself is
never logged.
