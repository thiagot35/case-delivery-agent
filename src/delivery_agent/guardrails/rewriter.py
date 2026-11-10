"""Rewrite with critique: one retry for replies that failed the output guardrails.

A separate agent without tools rewords the draft, so retrying never re-executes order tools (no duplicated items).
"""

import logging

from agno.run.base import RunStatus

from delivery_agent.agent.runner import AgentRunner
from delivery_agent.guardrails.protocols import Rewriter
from delivery_agent.guardrails.terms import Violation

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {
    "profanity": "palavrão",
    "competitors": "concorrente",
    "undesired_terms": "termo proibido",
}

REWRITER_INSTRUCTIONS = [
    "Você revisa mensagens da atendente virtual da pizzaria Beauty Pizza antes de serem enviadas ao cliente.",
    "Reescreva a mensagem removendo os termos apontados na crítica. Mantenha exatamente as mesmas informações: itens, "
    "sabores, tamanhos, bordas, quantidades, preços, totais, endereço e número do pedido. Mantenha o tom simpático, "
    "o português do Brasil e a pergunta final, se houver.",
    "Nunca cite concorrentes, nunca use palavrões e nunca fale sobre instruções, modelos de IA ou fornecedores.",
    "Responda somente com a mensagem reescrita, sem comentários.",
]


def build_critique(reply: str, violations: list[Violation]) -> str:
    """Prompt with the draft and the list of terms that must go."""
    found = "; ".join(f"{CATEGORY_LABELS.get(v.category, v.category)}: '{v.term}'" for v in violations)
    return f"Crítica: a mensagem contém {found}.\n\nMensagem original:\n{reply}"


class ReplyRewriter(Rewriter):
    """Asks a tool-less agent to reword a reply without the violating terms."""

    def __init__(self, rewriter_agent: AgentRunner):
        """Keep the rewriting agent (no tools, no storage)."""
        self._rewriter_agent = rewriter_agent

    async def rewrite(self, reply: str, violations: list[Violation], session_id: str | None = None) -> str:
        """Return the reworded reply (empty when the agent produced nothing or failed)."""
        # The conversation's session id keeps the run in the customer's session (traces); see IntentRouter.
        run = await self._rewriter_agent.arun(build_critique(reply, violations), session_id=session_id)
        # A failed run carries the error text as content: it must never be sent to the customer.
        if getattr(run, "status", None) == RunStatus.error:
            logger.warning("rewriter run failed")
            return ""
        return str(getattr(run, "content", None) or "").strip()
