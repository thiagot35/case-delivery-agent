"""Deterministic term lists (`terms.json`) and a whole-word, accent-insensitive matcher."""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

DEFAULT_TERMS_PATH = Path(__file__).with_name("terms.json")

Category = Literal["prompt_injection", "input_hard_block", "profanity", "competitors", "undesired_terms"]
INPUT_CATEGORIES: tuple[Category, ...] = ("prompt_injection", "input_hard_block")
OUTPUT_CATEGORIES: tuple[Category, ...] = ("profanity", "competitors", "undesired_terms")

_APOSTROPHES = re.compile(r"['’`´]")
_NON_WORD = re.compile(r"[^\w]+")


def normalize(text: str) -> str:
    """Casefold, strip accents, drop apostrophes and turn punctuation into single spaces."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_WORD.sub(" ", _APOSTROPHES.sub("", without_accents)).strip()


class TermList(BaseModel):
    """The validated content of `terms.json`."""

    version: int = 1
    description: str = ""
    prompt_injection: list[str] = []
    input_hard_block: list[str] = []
    profanity: list[str] = []
    competitors: list[str] = []
    undesired_terms: list[str] = []

    @field_validator(*(*INPUT_CATEGORIES, *OUTPUT_CATEGORIES))
    @classmethod
    def _non_empty_terms(cls, terms: list[str]) -> list[str]:
        """Reject blank terms, which would match everything."""
        if any(not normalize(term) for term in terms):
            raise ValueError("terms must not be blank")
        return terms

    @classmethod
    def load(cls, path: Path = DEFAULT_TERMS_PATH) -> "TermList":
        """Read and validate a terms file."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Violation:
    """A term of a category found in a text."""

    category: Category
    term: str


class TermMatcher:
    """Finds the terms of each category in a text, ignoring case, accents and punctuation."""

    def __init__(self, terms: TermList):
        """Precompile one whole-word regex per term."""
        self._patterns: dict[Category, list[tuple[str, re.Pattern[str]]]] = {
            category: [(term, _compile(term)) for term in getattr(terms, category)]
            for category in (*INPUT_CATEGORIES, *OUTPUT_CATEGORIES)
        }

    def find(self, text: str, categories: tuple[Category, ...]) -> list[Violation]:
        """Return every term of the given categories present in `text`."""
        normalized = normalize(text)
        return [
            Violation(category, term)
            for category in categories
            for term, pattern in self._patterns[category]
            if pattern.search(normalized)
        ]


def _compile(term: str) -> re.Pattern[str]:
    """Whole-word regex for a normalized term; spaces match any run of whitespace."""
    words = [re.escape(word) for word in normalize(term).split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)")
