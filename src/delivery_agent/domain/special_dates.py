"""Holidays and commemorative dates (`special_dates.json`, specs/005-special-dates).

The JSON keys and names are pt-BR data and are kept verbatim (constitution I.3). Which dates are close is decided in
code, never by the model: the agent only receives the dates that fall within the window.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, field_validator

DEFAULT_SPECIAL_DATES_PATH = Path(__file__).with_name("special_dates.json")
DEFAULT_WINDOW_DAYS = 7

# Free-text `location` → city key of `feriados_cidades` (normalized: lowercase, no accents, single spaces).
CITY_ALIASES: dict[str, str] = {
    "sao paulo": "Sao_Paulo",
    "sp": "Sao_Paulo",
    "sampa": "Sao_Paulo",
    "rio de janeiro": "Rio_de_Janeiro",
    "rio": "Rio_de_Janeiro",
    "rj": "Rio_de_Janeiro",
    "florianopolis": "Florianopolis",
    "floripa": "Florianopolis",
}

_DAY_MONTH = re.compile(r"^\d{2}/\d{2}$")


def normalize_location(text: str) -> str:
    """Lowercase, strip accents, turn `_`, `-`, `,` and `/` into spaces and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[_\-,/]", " ", plain).split())


def resolve_city(location: str | None) -> str | None:
    """Map a free-text location ("São Paulo", "Floripa", "Rio de Janeiro - RJ") to a city key, or None."""
    if not location or not location.strip():
        return None
    text = normalize_location(location)
    if text in CITY_ALIASES:
        return CITY_ALIASES[text]
    # "rio de janeiro rj", "sao paulo sp", "florianopolis sc": the longest alias at the start wins.
    for alias in sorted(CITY_ALIASES, key=len, reverse=True):
        if len(alias) > 3 and text.startswith(alias + " "):
            return CITY_ALIASES[alias]
    return None


def _check_day_month(value: str) -> str:
    """Validate a `DD/MM` value (29/02 is accepted)."""
    if not _DAY_MONTH.match(value):
        raise ValueError(f"expected DD/MM, got {value!r}")
    datetime.strptime(f"{value}/2028", "%d/%m/%Y")  # leap year: accepts 29/02
    return value


class FixedDate(BaseModel):
    """A national date repeated every year on the same day."""

    nome: str
    data: str
    tipo: str

    @field_validator("data")
    @classmethod
    def _valid(cls, value: str) -> str:
        """Reject malformed `DD/MM` values at startup."""
        return _check_day_month(value)


class MovableDate(BaseModel):
    """A national date whose day changes every year (listed per year)."""

    nome: str
    regra: str
    datas_por_ano: dict[str, str]

    @field_validator("datas_por_ano")
    @classmethod
    def _valid(cls, value: dict[str, str]) -> dict[str, str]:
        """Reject malformed `DD/MM/YYYY` values or a year that doesn't match its key."""
        for year, text in value.items():
            if datetime.strptime(text, "%d/%m/%Y").year != int(year):
                raise ValueError(f"{text} is not in {year}")
        return value


class CityDate(BaseModel):
    """A municipal or state date of one city."""

    nome: str
    data: str
    abrangencia: str

    @field_validator("data")
    @classmethod
    def _valid(cls, value: str) -> str:
        """Reject malformed `DD/MM` values at startup."""
        return _check_day_month(value)


class SpecialDatesFile(BaseModel):
    """Schema of `special_dates.json`."""

    datas_fixas_nacionais: list[FixedDate]
    datas_moveis: list[MovableDate]
    feriados_cidades: dict[str, list[CityDate]]


@dataclass(frozen=True)
class UpcomingDate:
    """A special date within the window, as handed to the order agent."""

    name: str
    date: date
    kind: str
    days_until: int
    city: str | None = None

    def as_state(self) -> dict[str, object]:
        """JSON-serializable view stored in the session state (`days_until` 0 = today)."""
        return {
            "name": self.name,
            "date": self.date.strftime("%d/%m/%Y"),
            "kind": self.kind,
            "days_until": self.days_until,
            "city": self.city,
        }


def _on_or_after(day_month: str, today: date) -> date | None:
    """The next occurrence of `DD/MM` on or after `today` (this year or the next), None if it doesn't exist."""
    day, month = (int(part) for part in day_month.split("/"))
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:  # 29/02 in a non-leap year
            continue
        if candidate >= today:
            return candidate
    return None


class SpecialDateCalendar:
    """Finds the national (and, with a known location, city) dates of the next days."""

    def __init__(self, data: SpecialDatesFile, window_days: int = DEFAULT_WINDOW_DAYS):
        """Keep the validated file and the look-ahead window (in days, inclusive)."""
        self._data = data
        self._window_days = window_days

    @classmethod
    def load(
        cls, path: Path = DEFAULT_SPECIAL_DATES_PATH, window_days: int = DEFAULT_WINDOW_DAYS
    ) -> "SpecialDateCalendar":
        """Load and validate the JSON file (fails at startup on a malformed date)."""
        data = SpecialDatesFile.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        return cls(data, window_days)

    @property
    def cities(self) -> list[str]:
        """City keys present in the file."""
        return list(self._data.feriados_cidades)

    def upcoming(self, today: date, location: str | None = None) -> list[UpcomingDate]:
        """Dates from today to `window_days` ahead, nearest first; city dates only when `location` is a known city."""
        found: list[UpcomingDate] = []
        for fixed in self._data.datas_fixas_nacionais:
            self._add(found, fixed.nome, _on_or_after(fixed.data, today), fixed.tipo, today)
        for movable in self._data.datas_moveis:
            for year in (today.year, today.year + 1):
                text = movable.datas_por_ano.get(str(year))
                if text:
                    self._add(found, movable.nome, datetime.strptime(text, "%d/%m/%Y").date(), "Data Móvel", today)
        city = resolve_city(location)
        for city_date in self._data.feriados_cidades.get(city, []) if city else []:
            occurrence = _on_or_after(city_date.data, today)
            self._add(found, city_date.nome, occurrence, city_date.abrangencia, today, city)
        return sorted(found, key=lambda d: (d.days_until, d.name))

    def _add(
        self,
        found: list[UpcomingDate],
        name: str,
        when: date | None,
        kind: str,
        today: date,
        city: str | None = None,
    ) -> None:
        """Append the date when it falls within the window."""
        if when is None:
            return
        days = (when - today).days
        if 0 <= days <= self._window_days:
            found.append(UpcomingDate(name, when, kind, days, city))
