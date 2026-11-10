"""Resolve free-text flavor, size and crust mentions to knowledge-base values.

Pipeline for flavors: normalize → exact → fuzzy (rapidfuzz) → semantic (vector index, needs confirmation).
Sizes and crusts: known synonyms → exact name → fuzzy name, always restricted to the names currently in the menu.
"""

import logging
import re
import unicodedata

from rapidfuzz import fuzz, process

from menu_service.models import FlavorMatch, Pizza
from menu_service.protocols import SemanticIndex
from menu_service.repository import MenuRepository

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 85

_FILLER = re.compile(r"\b(pizzas?|de|da|do|com|uma|um|a|o|sabor)\b")
_SPACES = re.compile(r"\s+")

_SIZE_SYNONYMS = {
    "pequena": "Pequena",
    "pequeno": "Pequena",
    "p": "Pequena",
    "broto": "Pequena",
    "media": "Média",
    "medio": "Média",
    "m": "Média",
    "grande": "Grande",
    "g": "Grande",
}

_CRUST_KEYWORDS = (
    ("cheddar", "Recheada com Cheddar"),
    ("chedar", "Recheada com Cheddar"),
    ("catupiry", "Recheada com Catupiry"),
    ("catupiri", "Recheada com Catupiry"),
    ("tradicional", "Tradicional"),
    ("normal", "Tradicional"),
    ("simples", "Tradicional"),
    ("comum", "Tradicional"),
    ("sem borda", "Tradicional"),
    ("sem recheio", "Tradicional"),
)

DEFAULT_CRUST = "Tradicional"


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, and spell out '4' as 'quatro'."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\b4\b", "quatro", text)
    return _SPACES.sub(" ", text).strip()


def _flavor_key(text: str) -> str:
    """Normalize a flavor and drop filler words ('pizza', 'de', 'com', ...)."""
    return _SPACES.sub(" ", _FILLER.sub(" ", normalize(text))).strip()


def _match_name(key: str, names: list[str]) -> str | None:
    """Match a normalized key to one of `names` exactly or by fuzzy score."""
    normalized = {name: normalize(name) for name in names}
    for name, value in normalized.items():
        if key == value:
            return name
    best = process.extractOne(key, normalized, scorer=fuzz.WRatio) if key else None
    if best is not None and best[1] >= FUZZY_THRESHOLD:
        return best[2]
    return None


class MenuResolver:
    """Maps customer wording to the flavors, sizes and crusts of one menu snapshot."""

    def __init__(
        self,
        repository: MenuRepository,
        index: SemanticIndex | None = None,
        semantic_max_distance: float = 0.75,
    ):
        """Keep the snapshot and the optional semantic index; precompute normalized keys of orderable flavors."""
        self._repository = repository
        self._index = index
        self._semantic_max_distance = semantic_max_distance
        self._keys = {pizza.id: _flavor_key(pizza.flavor) for pizza in repository.orderable_pizzas()}

    def resolve_flavor(self, text: str) -> FlavorMatch:
        """Match free text to an orderable flavor: exact, then fuzzy, then semantic (needs confirmation)."""
        key = _flavor_key(text)
        if not key or not self._keys:
            return FlavorMatch(pizza=None, score=0, method="none")

        for pizza_id, flavor_key in self._keys.items():
            if key == flavor_key:
                return FlavorMatch(pizza=self._pizza(pizza_id), score=100, method="exact")

        best = process.extractOne(key, self._keys, scorer=fuzz.WRatio)
        if best is not None and best[1] >= FUZZY_THRESHOLD:
            return FlavorMatch(pizza=self._pizza(best[2]), score=best[1], method="fuzzy")

        if self._index is not None:
            try:
                results = [r for r in self._index.search(text, limit=1) if r[0] in self._keys]
            except Exception:
                logger.exception("Semantic flavor resolution failed")
                results = []
            if results and results[0][1] <= self._semantic_max_distance:
                pizza_id, distance = results[0]
                return FlavorMatch(
                    pizza=self._pizza(pizza_id),
                    score=round(1 - distance, 3),
                    method="semantic",
                    needs_confirmation=True,
                )

        return FlavorMatch(pizza=None, score=0, method="none")

    def resolve_size(self, text: str) -> str | None:
        """Map free text ('media', 'broto', 'G') to a size name of the menu, or None."""
        sizes = self._repository.list_sizes()
        key = normalize(text)
        candidates = [_SIZE_SYNONYMS.get(key)] + [_SIZE_SYNONYMS.get(w) for w in key.split() if len(w) > 1]
        for candidate in candidates:
            if candidate in sizes:
                return candidate
        return _match_name(key, sizes)

    def resolve_crust(self, text: str | None) -> str | None:
        """Map free text ('cheddar', 'sem borda') to a crust name of the menu, or None; defaults to Tradicional."""
        crusts = self._repository.list_crusts()
        key = normalize(text or DEFAULT_CRUST)
        if not key or key == "borda":
            key = normalize(DEFAULT_CRUST)
        exact = _match_name(key, crusts)
        if exact is not None and normalize(exact) == key:
            return exact
        for keyword, crust in _CRUST_KEYWORDS:
            if keyword in key and crust in crusts:
                return crust
        return exact

    def _pizza(self, pizza_id: int) -> Pizza:
        """Return a pizza known to exist."""
        pizza = self._repository.get_pizza(pizza_id)
        assert pizza is not None
        return pizza
