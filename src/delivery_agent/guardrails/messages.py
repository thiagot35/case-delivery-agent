"""Static pt-BR replies sent by the guardrails without calling the order agent."""

from delivery_agent.guardrails.redaction import CARD_ENTITY, CPF_ENTITY


def not_delivery(first_name: str) -> str:
    """Reply to messages unrelated to the pizzeria."""
    return (
        f"Desculpe, {first_name}, só atendo pedidos da Beauty Pizza 🍕. "
        "Posso te mostrar o cardápio ou anotar o seu pedido?"
    )


def human_handoff(first_name: str) -> str:
    """Reply when the request for a human attendant is forwarded."""
    return (
        f"{first_name}, já pedi para um atendente humano continuar o seu atendimento. Ele vai falar com você por aqui "
        "em breve. Enquanto isso, se quiser, posso continuar ajudando com o seu pedido."
    )


def blocked(first_name: str) -> str:
    """Reply to messages blocked by the input filter (prompt injection, hard blocklist)."""
    return f"Desculpe, {first_name}, não posso ajudar com isso. Posso te ajudar com o cardápio ou com o seu pedido?"


def rephrase(first_name: str) -> str:
    """Reply when the agent's answer failed the output guardrails twice."""
    return f"Desculpe, {first_name}, não entendi bem. Pode reformular o que você deseja?"


def pii_notice(found: frozenset[str]) -> str:
    """Notice appended to the reply when sensitive data was removed from the customer's message."""
    labels = [label for entity, label in ((CARD_ENTITY, "cartão"), (CPF_ENTITY, "CPF")) if entity in found]
    what = " e ".join(labels) or "dados pessoais"
    return f"Por segurança, removi da sua mensagem os dados de {what}. Não é preciso enviar esses dados por aqui."


BLOCKED_PLACEHOLDER = "[mensagem bloqueada]"


def greeting_new(first_name: str, flavors: list[str]) -> str:
    """Greeting of a customer without orders: a few menu flavors and an invitation to order."""
    if flavors:
        listed = f"{', '.join(flavors[:-1])} e {flavors[-1]}" if len(flavors) > 1 else flavors[0]
        menu = f"temos diversos sabores como {listed} e muito mais"
    else:
        menu = "temos diversos sabores deliciosos"
    return (
        f"Olá, {first_name}, tudo bem? Somos a Beauty Pizza e {menu}! "
        "Você pode consultar e fazer sua escolha aqui pelo chat 🍕"
    )


def greeting_returning(first_name: str, items: list[str], total: str) -> str:
    """Greeting of a returning customer: the last order summary and the offer to repeat it."""
    lines = "\n".join(f"- {item}" for item in items)
    return (
        f"Olá, {first_name}, tudo bem? Que bom ter você de volta à Beauty Pizza 🍕\n"
        f"Seu último pedido foi:\n{lines}\nTotal: {total}\n"
        "Quer repetir esse pedido?"
    )
