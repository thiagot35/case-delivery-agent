from decimal import Decimal
from pathlib import Path

import pytest

from menu_service import store
from menu_service.bootstrap import ensure_menu_db
from menu_service.repository import MenuRepository
from menu_service.resolver import MenuResolver
from tests.conftest import KB_SQL, FakeIndex


def test_bootstrap_seeds_only_once(tmp_path: Path) -> None:
    """The database is seeded when missing and never rebuilt afterwards (API edits survive restarts)."""
    db = tmp_path / "menu.db"
    assert ensure_menu_db(KB_SQL, db) is True
    with store.transaction(db) as conn:
        store.upsert(conn, "tamanhos", {"tamanho": "Família"})
    assert ensure_menu_db(KB_SQL, db) is False
    assert "Família" in MenuRepository.from_path(db).list_sizes()


def test_bootstrap_missing_sql(tmp_path: Path) -> None:
    """A missing SQL file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        ensure_menu_db(tmp_path / "nope.sql", tmp_path / "menu.db")


def test_repository_content(repository: MenuRepository) -> None:
    """The repository loads 6 pizzas, 3 sizes and 3 crusts."""
    assert len(repository.list_pizzas()) == 6
    assert repository.list_sizes() == ["Pequena", "Média", "Grande"]
    assert repository.list_crusts() == ["Tradicional", "Recheada com Cheddar", "Recheada com Catupiry"]
    assert sum(len(repository.price_matrix(p.id)) for p in repository.list_pizzas()) == 38


def test_price_lookup(repository: MenuRepository) -> None:
    """Prices match the knowledge base for valid combinations."""
    calabresa = repository.get_pizza_by_flavor("calabresa")
    assert calabresa is not None
    quote = repository.get_price(calabresa.id, "Grande", "Recheada com Cheddar")
    assert quote.available and quote.entry is not None
    assert quote.entry.price == Decimal("50.00")


@pytest.mark.parametrize(
    ("flavor", "size", "crust"),
    [
        ("Margherita", "Pequena", "Recheada com Cheddar"),  # Pequena only Tradicional
        ("Doce de Leite com Coco", "Grande", "Recheada com Catupiry"),  # sweet pizza only Tradicional
    ],
)
def test_unavailable_combinations(repository: MenuRepository, flavor: str, size: str, crust: str) -> None:
    """Unavailable combinations return the flavor's valid alternatives."""
    pizza = repository.get_pizza_by_flavor(flavor)
    assert pizza is not None
    quote = repository.get_price(pizza.id, size, crust)
    assert not quote.available
    assert quote.alternatives
    assert all(alt.pizza_id == pizza.id for alt in quote.alternatives)


def test_price_range(repository: MenuRepository) -> None:
    """min_price and max_price span every combination of a pizza."""
    margherita = repository.get_pizza_by_flavor("Margherita")
    assert margherita is not None
    assert (repository.min_price(margherita.id), repository.max_price(margherita.id)) == (
        Decimal("25.00"),
        Decimal("49.00"),
    )


@pytest.mark.parametrize(
    ("text", "flavor", "method"),
    [
        ("Calabresa", "Calabresa", "exact"),
        ("pizza de calabresa", "Calabresa", "exact"),
        ("calabreza", "Calabresa", "fuzzy"),
        ("4 queijos", "Quatro Queijos", "exact"),
        ("frango catupiri", "Frango com Catupiry", "fuzzy"),
        ("MARGUERITA", "Margherita", "fuzzy"),
    ],
)
def test_resolve_flavor(resolver: MenuResolver, text: str, flavor: str, method: str) -> None:
    """Exact, filler-word, numeric and misspelled flavors resolve by exact or fuzzy match."""
    match = resolver.resolve_flavor(text)
    assert match.pizza is not None
    assert match.pizza.flavor == flavor
    assert match.method == method
    assert not match.needs_confirmation


def test_resolve_flavor_semantic_needs_confirmation(resolver: MenuResolver) -> None:
    """Semantic-only flavor matches require customer confirmation."""
    match = resolver.resolve_flavor("aquela doce")
    assert match.method == "semantic"
    assert match.pizza is not None and match.pizza.flavor == "Doce de Leite com Coco"
    assert match.needs_confirmation


def test_resolve_flavor_not_found(repository: MenuRepository) -> None:
    """Unrelated text resolves to no flavor."""
    resolver = MenuResolver(repository, FakeIndex(distance=0.95), semantic_max_distance=0.75)
    assert resolver.resolve_flavor("aquela doce").method == "none"  # semantic too far
    assert resolver.resolve_flavor("sushi").pizza is None
    assert MenuResolver(repository).resolve_flavor("").pizza is None


@pytest.mark.parametrize(
    ("text", "size"),
    [("grande", "Grande"), ("G", "Grande"), ("media", "Média"), ("Média", "Média"), ("tamanho pequena", "Pequena")],
)
def test_resolve_size(resolver: MenuResolver, text: str, size: str) -> None:
    """Size synonyms resolve to the menu sizes."""
    assert resolver.resolve_size(text) == size


def test_resolve_size_unknown(resolver: MenuResolver) -> None:
    """Unknown sizes resolve to None."""
    assert resolver.resolve_size("gigante") is None


@pytest.mark.parametrize(
    ("text", "crust"),
    [
        (None, "Tradicional"),
        ("borda de cheddar", "Recheada com Cheddar"),
        ("Recheada com Catupiry", "Recheada com Catupiry"),
        ("catupiri", "Recheada com Catupiry"),
        ("sem borda", "Tradicional"),
        ("normal", "Tradicional"),
    ],
)
def test_resolve_crust(resolver: MenuResolver, text: str | None, crust: str) -> None:
    """Crust synonyms resolve, defaulting to Tradicional."""
    assert resolver.resolve_crust(text) == crust


def test_resolve_crust_unknown(resolver: MenuResolver) -> None:
    """Unknown crusts resolve to None."""
    assert resolver.resolve_crust("chocolate") is None


def test_resolver_follows_menu_changes(writable_menu_db: Path) -> None:
    """Sizes and crusts added through the API resolve; unpriced pizzas are not offered."""
    with store.transaction(writable_menu_db) as conn:
        store.upsert(conn, "tamanhos", {"tamanho": "Família"})
        store.upsert(conn, "bordas", {"tipo": "Recheada com Chocolate"})
        store.upsert(conn, "pizzas", {"sabor": "Portuguesa", "descricao": "Clássica.", "ingredientes": "Ovo."})
    resolver = MenuResolver(MenuRepository.from_path(writable_menu_db))
    assert resolver.resolve_size("familia") == "Família"
    assert resolver.resolve_crust("Recheada com Chocolate") == "Recheada com Chocolate"
    assert resolver.resolve_crust("catupiry") == "Recheada com Catupiry"
    assert resolver.resolve_flavor("Portuguesa").pizza is None  # no price yet: not orderable
