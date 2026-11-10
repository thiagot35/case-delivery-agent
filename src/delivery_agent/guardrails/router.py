"""Intent router: a deterministic state check, then a small classification agent (fail-open to `delivery`).

`greeting` (specs/004-greeting) is only valid for the first message of the day; otherwise it becomes `delivery`.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from agno.run.base import RunStatus
from pydantic import BaseModel, Field

from delivery_agent.agent.runner import AgentRunner
from delivery_agent.guardrails.protocols import IntentClassifier
from delivery_agent.guardrails.terms import normalize

logger = logging.getLogger(__name__)

Intent = Literal["delivery", "not_delivery", "call_human", "greeting"]
RouteMethod = Literal["state", "llm", "fallback"]
INTENTS: tuple[Intent, ...] = ("delivery", "not_delivery", "call_human", "greeting")

# Short answers to the agent's last question ("Podemos entregar em X?", "Deseja mais alguma coisa?", ...).
_SHORT_REPLY = re.compile(
    r"^(sim|s|nao|n|ok|okay|pode|pode ser|isso|isso mesmo|claro|beleza|blz|certo|correto|confirmo|confirmar|"
    r"quero|nao quero|so isso|e isso|mais nada|nada|obrigad[oa]|valeu|\d{1,3})$"
)
_ADDRESS = re.compile(r"^(rua|r|av|avenida|travessa|tv|alameda|al|estrada|rodovia|praca|largo)\s")

ROUTER_INSTRUCTIONS = [
    "Você classifica a intenção da mensagem de um cliente no chat de atendimento da pizzaria Beauty Pizza. "
    "Responda apenas com a classificação, sem conversar com o cliente.",
    "delivery: qualquer coisa relacionada ao atendimento da pizzaria: cumprimentos, cardápio, sabores, ingredientes, "
    "preços, montar, alterar, repetir, confirmar ou cancelar o pedido, endereço de entrega, respostas curtas à última "
    "pergunta da atendente, reclamações sobre o pedido e dúvidas sobre a pizzaria.",
    "not_delivery: assuntos sem relação com a pizzaria (política, esportes, notícias, tarefas escolares, programação, "
    "outros produtos ou serviços, perguntas gerais de conhecimento).",
    "call_human: o cliente pede explicitamente para falar com uma pessoa, um atendente humano, o gerente ou o "
    "responsável.",
    "greeting: SOMENTE quando 'Primeira mensagem do cliente hoje' for 'sim' e a mensagem for apenas um "
    "cumprimento, uma cortesia ou um sim/não sem citar nenhum item, por exemplo: 'Olá', 'Oi', 'Salve', 'Bom dia', "
    "'Boa tarde', 'Boa noite', 'Tudo bem?', 'Sim', 'Não'. Se a mensagem citar pizza, sabor, preço, cardápio, "
    "opções ou pedido (ex.: 'Gostaria de comprar uma pizza de calabresa', 'Vocês têm pizza de bacon?', "
    "'Queria saber o preço', 'Quais são as opções?'), é delivery. Se 'Primeira mensagem do cliente hoje' for "
    "'não', um cumprimento é delivery.",
    "Use a última resposta da atendente e a etapa do atendimento para entender respostas curtas. Na dúvida entre "
    "delivery e not_delivery, escolha delivery.",
]


class IntentClassification(BaseModel):
    """Structured output of the router agent."""

    intent: Intent = Field(description="delivery, not_delivery, call_human ou greeting")
    reason: str = Field(default="", description="Motivo curto da classificação")


@dataclass(frozen=True)
class RouteDecision:
    """The routed intent and how it was decided."""

    intent: Intent
    method: RouteMethod
    reason: str = ""


def state_check(message: str, flow_step: str | None) -> RouteDecision | None:
    """Route short answers and addresses in an active conversation to `delivery` without calling a model."""
    if not flow_step or flow_step == "start":
        return None
    text = normalize(message)
    if _SHORT_REPLY.match(text) or _ADDRESS.match(text):
        return RouteDecision("delivery", "state", "short reply in an active flow")
    return None


def build_router_input(
    message: str, flow_step: str | None, last_reply: str | None, first_message_of_day: bool = False
) -> str:
    """Prompt with the conversation context the classifier needs."""
    return (
        f"Primeira mensagem do cliente hoje: {'sim' if first_message_of_day else 'não'}\n"
        f"Etapa do atendimento: {flow_step or 'start'}\n"
        f"Última resposta da atendente: {last_reply or '(nenhuma, início da conversa)'}\n"
        f"Mensagem do cliente: {message}"
    )


def parse_intent(content: Any) -> IntentClassification | None:
    """Read the classification from the agent output (model instance or dict), or None if invalid."""
    if isinstance(content, IntentClassification):
        return content
    try:
        return IntentClassification.model_validate(content)
    except ValueError:
        return None


class IntentRouter(IntentClassifier):
    """Classifies each customer message; any failure routes to `delivery` (the order agent still refuses off-topic)."""

    def __init__(self, classifier_agent: AgentRunner):
        """Keep the classification agent (no tools, no storage, `output_schema=IntentClassification`)."""
        self._classifier_agent = classifier_agent

    async def classify(
        self,
        message: str,
        flow_step: str | None,
        last_reply: str | None,
        first_message_of_day: bool = False,
        session_id: str | None = None,
    ) -> RouteDecision:
        """Return the intent of the message: state check first, then the classification agent."""
        decided = state_check(message, flow_step)
        if decided is not None:
            return decided
        try:
            # Without a session id Agno makes one up (and keeps it on the shared agent): the trace of the run
            # would then land in a session of its own instead of the customer's conversation.
            run = await self._classifier_agent.arun(
                build_router_input(message, flow_step, last_reply, first_message_of_day), session_id=session_id
            )
        except Exception:
            logger.warning("router failed, routing to delivery", exc_info=True)
            return RouteDecision("delivery", "fallback", "router error")
        # Agno reports model errors as a run with status=error (content = the error text) instead of raising.
        if getattr(run, "status", None) == RunStatus.error:
            logger.warning("router run failed, routing to delivery")
            return RouteDecision("delivery", "fallback", "router error")
        parsed = parse_intent(getattr(run, "content", None))
        if parsed is None:
            logger.warning("router returned an invalid classification, routing to delivery")
            return RouteDecision("delivery", "fallback", "invalid router output")
        if parsed.intent == "greeting" and not first_message_of_day:
            return RouteDecision("delivery", "llm", "greeting outside the first message of the day")
        return RouteDecision(parsed.intent, "llm", parsed.reason)
