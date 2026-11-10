"""PII redaction with Microsoft Presidio: Brazilian CPF and credit card numbers only.

The analyzer registry holds just two pattern recognizers, so detection is deterministic: a CPF must pass both check
digits and a card number must pass the Luhn check. spaCy (`pt_core_news_sm`) is only Presidio's tokenizer here; no NER
recognizer is registered.
"""

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import CreditCardRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from delivery_agent.domain.cpf import is_valid_cpf
from delivery_agent.guardrails.protocols import Redactor
from delivery_agent.guardrails.redaction import CPF_ENTITY, REPLACEMENTS, Redaction

LANGUAGE = "pt"
SPACY_MODEL = "pt_core_news_sm"


class BrCpfRecognizer(PatternRecognizer):
    """Recognizes CPFs (formatted or bare 11 digits) and keeps only those with valid check digits."""

    def __init__(self) -> None:
        """Register the CPF pattern and pt-BR context words."""
        super().__init__(
            supported_entity=CPF_ENTITY,
            supported_language=LANGUAGE,
            patterns=[Pattern("cpf", r"(?<![\w.-])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![\w-])", 0.5)],
            context=["cpf", "documento", "cadastro"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        """Accept only CPFs whose check digits are valid."""
        return is_valid_cpf(pattern_text)


class PiiRedactor(Redactor):
    """Masks CPFs and credit card numbers with Presidio."""

    def __init__(self) -> None:
        """Load the spaCy tokenizer once and build the analyzer with the two recognizers."""
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": LANGUAGE, "model_name": SPACY_MODEL}],
            }
        ).create_engine()
        registry = RecognizerRegistry(supported_languages=[LANGUAGE])
        registry.add_recognizer(BrCpfRecognizer())
        registry.add_recognizer(CreditCardRecognizer(supported_language=LANGUAGE))
        self._analyzer = AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=[LANGUAGE])
        self._anonymizer = AnonymizerEngine()
        self._operators = {
            entity: OperatorConfig("replace", {"new_value": value}) for entity, value in REPLACEMENTS.items()
        }

    def redact(self, text: str) -> Redaction:
        """Replace every CPF and card number in `text` and report which types were found."""
        results = self._analyzer.analyze(text=text, language=LANGUAGE, entities=list(REPLACEMENTS))
        if not results:
            return Redaction(text)
        anonymized = self._anonymizer.anonymize(text=text, analyzer_results=results, operators=self._operators)
        return Redaction(anonymized.text, frozenset(result.entity_type for result in results))
