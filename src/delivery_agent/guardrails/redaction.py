"""PII redaction result and entity names, kept free of the Presidio import so callers stay lightweight."""

from dataclasses import dataclass, field

CPF_ENTITY = "BR_CPF"
CARD_ENTITY = "CREDIT_CARD"
REPLACEMENTS = {CPF_ENTITY: "[CPF removido]", CARD_ENTITY: "[cartão removido]"}


@dataclass(frozen=True)
class Redaction:
    """The redacted text and the PII entity types that were found."""

    text: str
    found: frozenset[str] = field(default_factory=frozenset)
