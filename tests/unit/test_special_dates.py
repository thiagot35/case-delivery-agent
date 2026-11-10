import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from delivery_agent.domain.special_dates import SpecialDateCalendar, resolve_city


@pytest.fixture(scope="module")
def calendar() -> SpecialDateCalendar:
    """The packaged special-dates calendar with the default 7-day window."""
    return SpecialDateCalendar.load()


def names(calendar: SpecialDateCalendar, today: date, location: str | None = None) -> list[tuple[str, int]]:
    """Upcoming (name, days_until) pairs."""
    return [(d.name, d.days_until) for d in calendar.upcoming(today, location)]


def test_file_loads_with_the_three_cities(calendar: SpecialDateCalendar) -> None:
    """The packaged file validates and has the three city lists."""
    assert calendar.cities == ["Sao_Paulo", "Rio_de_Janeiro", "Florianopolis"]


def test_window_is_seven_days_inclusive(calendar: SpecialDateCalendar) -> None:
    """A date 7 days ahead is included, 8 days ahead is not, and today counts as 0."""
    assert names(calendar, date(2026, 11, 8)) == [("Proclamação da República", 7)]
    assert names(calendar, date(2026, 11, 7)) == []
    assert names(calendar, date(2026, 11, 15)) == [("Proclamação da República", 0)]
    assert names(calendar, date(2026, 11, 16)) == []


def test_nearest_first(calendar: SpecialDateCalendar) -> None:
    """Several dates in the window come nearest first."""
    assert names(calendar, date(2026, 12, 20)) == [("Véspera de Natal", 4), ("Natal", 5)]


def test_year_rollover(calendar: SpecialDateCalendar) -> None:
    """New Year's Day of the next year is found from late December."""
    upcoming = calendar.upcoming(date(2026, 12, 28))
    assert [(d.name, d.days_until) for d in upcoming] == [
        ("Véspera de Ano Novo (Réveillon)", 3),
        ("Confraternização Universal (Ano Novo)", 4),
    ]
    assert upcoming[1].as_state()["date"] == "01/01/2027"


def test_movable_dates_use_the_year_table(calendar: SpecialDateCalendar) -> None:
    """Movable dates come from `datas_por_ano`; a year missing from the table has none."""
    assert ("Black Friday", 5) in names(calendar, date(2026, 11, 22))
    assert names(calendar, date(2028, 2, 25)) == [("Carnaval (Terça-feira)", 4)]  # 29/02/2028
    assert names(calendar, date(2032, 5, 5)) == []  # Dia das Mães 2032 is not listed


@pytest.mark.parametrize(
    ("location", "today", "expected"),
    [
        ("São Paulo", date(2026, 1, 20), [("Aniversário da Cidade de São Paulo", 5)]),
        ("sao paulo, sp", date(2026, 7, 3), [("Revolução Constitucionalista de 1932", 6)]),
        ("Rio de Janeiro - RJ", date(2026, 4, 20), [("Tiradentes", 1), ("Dia de São Jorge", 3)]),
        ("Floripa", date(2026, 3, 20), [("Aniversário de Florianópolis", 3)]),
    ],
)
def test_city_dates_are_added(
    calendar: SpecialDateCalendar, location: str, today: date, expected: list[tuple[str, int]]
) -> None:
    """A known location adds its city dates to the national ones."""
    assert names(calendar, today, location) == expected


@pytest.mark.parametrize("location", [None, "", "   ", "Curitiba", "Rio Grande do Sul"])
def test_without_a_known_city_only_national_dates(calendar: SpecialDateCalendar, location: str | None) -> None:
    """No location, a blank one or an unknown city gives the national dates only."""
    assert names(calendar, date(2026, 1, 20), location) == []
    assert names(calendar, date(2026, 4, 20), location) == [("Tiradentes", 1)]


def test_city_dates_carry_the_city(calendar: SpecialDateCalendar) -> None:
    """City dates are tagged with the city key, national dates are not."""
    upcoming = calendar.upcoming(date(2026, 4, 20), "rio")
    assert [(d.name, d.city) for d in upcoming] == [("Tiradentes", None), ("Dia de São Jorge", "Rio_de_Janeiro")]
    assert upcoming[1].as_state() == {
        "name": "Dia de São Jorge",
        "date": "23/04/2026",
        "kind": "Estadual (RJ)",
        "days_until": 3,
        "city": "Rio_de_Janeiro",
    }


@pytest.mark.parametrize(
    ("location", "city"),
    [("SP", "Sao_Paulo"), ("Rio", "Rio_de_Janeiro"), ("FLORIANÓPOLIS", "Florianopolis"), ("Sao_Paulo", "Sao_Paulo")],
)
def test_resolve_city(location: str, city: str) -> None:
    """Aliases, accents, case and the JSON keys themselves resolve to the city key."""
    assert resolve_city(location) == city


def test_custom_window(tmp_path: Path) -> None:
    """The window is configurable."""
    assert [d.name for d in SpecialDateCalendar.load(window_days=10).upcoming(date(2026, 11, 5))] == [
        "Proclamação da República"
    ]


def test_malformed_date_fails_at_load(tmp_path: Path) -> None:
    """A malformed date is rejected when the file is loaded."""
    path = tmp_path / "dates.json"
    path.write_text(
        json.dumps(
            {
                "datas_fixas_nacionais": [{"nome": "X", "data": "31/02", "tipo": "Y"}],
                "datas_moveis": [],
                "feriados_cidades": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        SpecialDateCalendar.load(path)
