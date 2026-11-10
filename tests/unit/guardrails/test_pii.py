import pytest

from delivery_agent.guardrails.pii import PiiRedactor
from delivery_agent.guardrails.redaction import CARD_ENTITY, CPF_ENTITY


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Valid CPF numbers here are random test values generated via https://www.geradordecpf.org/ (no real person);
        # the invalid ones are made up to fail validation.
        ("meu cpf é 529.982.247-25", "meu cpf é [CPF removido]"),
        ("cpf 52998224725 ok", "cpf [CPF removido] ok"),
        ("cartão 4111 1111 1111 1111 validade 12/30", "cartão [cartão removido] validade 12/30"),
        ("cartão 5555-5555-5555-4444", "cartão [cartão removido]"),
        ("4111111111111111", "[cartão removido]"),
    ],
)
def test_masks_valid_cpf_and_cards(pii_redactor: PiiRedactor, text: str, expected: str) -> None:
    """Valid CPFs (formatted or bare) and Luhn-valid card numbers are replaced."""
    assert pii_redactor.redact(text).text == expected


@pytest.mark.parametrize(
    "text",
    [
        "cpf 123.456.789-00",
        "cartão 4111 1111 1111 1112",
        "pedido 12345678901",
        "meu telefone é (11) 98765-4321",
        "o total ficou R$ 120,00 com 2 pizzas",
        "Rua das Acácias, 45, apto 1203",
    ],
)
def test_keeps_non_pii_numbers(pii_redactor: PiiRedactor, text: str) -> None:
    """Invalid CPFs, non-Luhn numbers, phones, prices and addresses are left untouched."""
    redaction = pii_redactor.redact(text)
    assert redaction.text == text
    assert redaction.found == frozenset()


def test_reports_found_types(pii_redactor: PiiRedactor) -> None:
    """Both entity types are reported when both appear."""
    redaction = pii_redactor.redact("cpf 529.982.247-25 e cartão 4111 1111 1111 1111")
    assert redaction.found == {CPF_ENTITY, CARD_ENTITY}
    assert "529" not in redaction.text and "4111" not in redaction.text
