from pathlib import Path

import pytest
from pydantic import ValidationError

from delivery_agent.guardrails.terms import OUTPUT_CATEGORIES, TermList, TermMatcher, normalize


def test_packaged_terms_load() -> None:
    """The packaged terms.json is valid and every category has terms."""
    terms = TermList.load()
    for category in ("prompt_injection", "input_hard_block", "profanity", "competitors", "undesired_terms"):
        assert getattr(terms, category), category


def test_blank_term_is_rejected(tmp_path: Path) -> None:
    """A blank term would match everything, so it fails validation."""
    path = tmp_path / "terms.json"
    path.write_text('{"profanity": ["  "]}', encoding="utf-8")
    with pytest.raises(ValidationError):
        TermList.load(path)


def test_normalize() -> None:
    """Case, accents, apostrophes and punctuation are normalized away."""
    assert normalize("  Domino’s, PIZZA-Hut! Ação ") == "dominos pizza hut acao"


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Peça também na PIZZA   HUT", "pizza hut"),
        ("melhor que o Domino's", "domino's"),
        ("que PÔRRA é essa", "porra"),
        ("foda-se", "foda se"),
    ],
)
def test_matches_ignoring_case_accents_and_spacing(term_matcher: TermMatcher, text: str, term: str) -> None:
    """Terms match regardless of case, accents, spacing and punctuation."""
    assert term in [v.term for v in term_matcher.find(text, OUTPUT_CATEGORIES)]


@pytest.mark.parametrize(
    "text",
    [
        "Tome cuidado com a borda quente!",
        "A pizza de Calabresa custa R$ 47,00.",
        "Podemos entregar na Rua das Flores, 100?",
        "Não temos cupons nem descontos, mas temos ótimas pizzas.",
    ],
)
def test_no_match_inside_words_or_clean_text(term_matcher: TermMatcher, text: str) -> None:
    """Terms only match whole words ("cu" is not found in "cuidado"), and clean replies pass."""
    assert term_matcher.find(text, OUTPUT_CATEGORIES) == []
