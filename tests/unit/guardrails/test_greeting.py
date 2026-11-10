import random
from datetime import timedelta

from delivery_agent.domain.models import CustomerContext
from delivery_agent.guardrails.greeting import GreetingResponder
from tests.conftest import CPF, NAME, TODAY, FakeMenuApi, FakeOrderApi

CUSTOMER = CustomerContext(client_name=NAME, client_document=CPF)
CALABRESA = ("Pizza Calabresa - Grande - Borda Tradicional", 2, "47.00")
MARGUERITA = ("Pizza Marguerita - Média - Borda Recheada com Catupiry", 1, "45.00")


def responder(order_api: FakeOrderApi, menu_api: FakeMenuApi, seed: int = 7) -> GreetingResponder:
    """Greeting responder over the in-memory Order API and Menu service, with a seeded random generator."""
    return GreetingResponder(order_api, menu_api, random.Random(seed))  # type: ignore[arg-type]


async def test_new_customer_gets_three_menu_flavors(order_api: FakeOrderApi, menu_api: FakeMenuApi) -> None:
    """A customer without orders gets 3 distinct flavors that exist on the menu."""
    greeting = await responder(order_api, menu_api).greet(CUSTOMER, TODAY)
    assert greeting is not None and greeting.customer_type == "new"
    menu_flavors = {pizza.flavor for pizza in (await menu_api.get_menu()).pizzas}
    shown = [flavor for flavor in menu_flavors if flavor in greeting.reply]
    assert len(shown) == 3
    assert greeting.reply.startswith("Olá, Maria, tudo bem? Somos a Beauty Pizza e temos diversos sabores como ")
    assert "e muito mais! Você pode consultar e fazer sua escolha aqui pelo chat" in greeting.reply
    assert greeting.state_updates == {"greeted": True}


async def test_flavors_are_random(order_api: FakeOrderApi, menu_api: FakeMenuApi) -> None:
    """Different seeds pick different flavor sets."""
    replies = {(await responder(order_api, menu_api, seed).greet(CUSTOMER, TODAY)).reply for seed in range(8)}  # type: ignore[union-attr]
    assert len(replies) > 1


async def test_returning_customer_gets_last_order(order_api: FakeOrderApi, menu_api: FakeMenuApi) -> None:
    """A returning customer gets the last order's items and total, and the state to repeat it."""
    order_api.seed(TODAY - timedelta(days=9), [MARGUERITA])
    last = order_api.seed(TODAY - timedelta(days=2), [CALABRESA])
    greeting = await responder(order_api, menu_api).greet(CUSTOMER, TODAY)
    assert greeting is not None and greeting.customer_type == "returning"
    assert "Que bom ter você de volta à Beauty Pizza" in greeting.reply
    assert "- 2x Pizza Calabresa - Grande - Borda Tradicional" in greeting.reply
    assert "Total: R$ 94,00" in greeting.reply
    assert greeting.reply.endswith("Quer repetir esse pedido?")
    assert "Marguerita - Média" not in greeting.reply
    assert greeting.state_updates == {
        "greeted": True,
        "repeat_offered": True,
        "last_order_id": last.id,
        "previous_flavors": ["Calabresa", "Marguerita"],
    }
    assert menu_api.calls == []  # no menu call for a returning customer


async def test_order_today_skips_the_greeting(order_api: FakeOrderApi, menu_api: FakeMenuApi) -> None:
    """When the customer already has an order today, the order agent answers instead."""
    order_api.seed(TODAY - timedelta(days=2), [CALABRESA])
    order_api.seed(TODAY, [MARGUERITA])
    assert await responder(order_api, menu_api).greet(CUSTOMER, TODAY) is None


async def test_order_api_down_greets_as_new(order_api: FakeOrderApi, menu_api: FakeMenuApi) -> None:
    """An Order API outage falls back to the new-customer greeting."""
    order_api.seed(TODAY - timedelta(days=2), [CALABRESA])
    order_api.unavailable = True
    greeting = await responder(order_api, menu_api).greet(CUSTOMER, TODAY)
    assert greeting is not None and greeting.customer_type == "new"


async def test_menu_down_greets_without_flavors(order_api: FakeOrderApi, menu_api: FakeMenuApi) -> None:
    """A Menu service outage gives the greeting without the flavor list."""
    menu_api.unavailable = True
    greeting = await responder(order_api, menu_api).greet(CUSTOMER, TODAY)
    assert greeting is not None
    assert greeting.reply == (
        "Olá, Maria, tudo bem? Somos a Beauty Pizza e temos diversos sabores deliciosos! "
        "Você pode consultar e fazer sua escolha aqui pelo chat 🍕"
    )
