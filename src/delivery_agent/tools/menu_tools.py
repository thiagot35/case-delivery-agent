"""Menu tools (🟨 nodes "Puxar dados do cardápio"): menu, details, prices, semantic search and suggestions.

Every tool reads through the Menu service API: the agent never opens the menu database or the vector index, and it
has no tool to change the menu.
Contract: specs/001-pizza-ordering-agent/contracts/agent-tools.md
"""

import logging
from typing import Any

from agno.run import RunContext
from agno.tools import Toolkit

from delivery_agent.clients.errors import MenuServiceError
from delivery_agent.clients.menu_api_client import MenuApiClient
from delivery_agent.domain.formatting import format_brl, menu_summary
from delivery_agent.domain.models import FlavorMatch, MenuPrice, MenuQuote, MenuSearchHit
from delivery_agent.tools.results import fail, ok

logger = logging.getLogger(__name__)

DEFAULT_SUGGESTION_QUERY = "pizzas clássicas e tradicionais mais pedidas, salgadas, com queijo"
SIMILAR_QUERY_TEMPLATE = "pizzas parecidas com {flavors}"


def price_rows(prices: list[MenuPrice]) -> list[dict[str, str]]:
    """Format price rows for tool results."""
    return [{"size": p.size, "crust": p.crust, "price_display": format_brl(p.price)} for p in prices]


def menu_unavailable() -> str:
    """Tool error used when the Menu service cannot be reached."""
    return fail(
        "menu_unavailable",
        "O cardápio está indisponível no momento. Peça desculpas e sugira tentar em instantes.",
    )


def flavor_error(match: FlavorMatch) -> str | None:
    """Return a tool error for unresolved or ambiguous flavors, else None."""
    if match.pizza is None:
        return fail(
            "flavor_not_found",
            "Sabor não encontrado no cardápio. Mostre os sabores disponíveis ao cliente.",
            options=match.options,
        )
    if match.needs_confirmation:
        return fail(
            "flavor_ambiguous",
            f"Não tenho certeza do sabor. Pergunte ao cliente se ele quis dizer '{match.pizza.flavor}'.",
            suggestion=match.pizza.flavor,
        )
    return None


def quote_error(quote: MenuQuote) -> str | None:
    """Return a tool error when a quote could not be priced, else None."""
    if quote.status == "ok" and quote.pizza is not None and quote.price is not None:
        return None
    if quote.status == "flavor_not_found" or quote.pizza is None:
        return fail(
            "flavor_not_found",
            "Sabor não encontrado no cardápio. Mostre os sabores disponíveis ao cliente.",
            options=quote.options,
        )
    if quote.status == "flavor_ambiguous":
        return fail(
            "flavor_ambiguous",
            f"Não tenho certeza do sabor. Pergunte ao cliente se ele quis dizer '{quote.suggestion}'.",
            suggestion=quote.suggestion,
        )
    if quote.status == "unrecognized_option":
        return fail(
            "combination_unavailable",
            "Tamanho ou borda não reconhecidos. Pergunte ao cliente.",
            sizes=quote.sizes,
            crusts=quote.crusts,
            alternatives=price_rows(quote.alternatives),
        )
    return fail(
        "combination_unavailable",
        f"{quote.pizza.flavor} não está disponível em {quote.size} com borda {quote.crust}.",
        alternatives=price_rows(quote.alternatives),
    )


class MenuTools(Toolkit):
    """Read-only menu tools backed by the Menu service API."""

    def __init__(self, menu: MenuApiClient):
        """Keep the Menu service client and register the menu tools with Agno."""
        self._menu = menu
        super().__init__(
            name="menu_tools",
            tools=[self.list_menu, self.get_pizza_details, self.get_price, self.search_menu, self.suggest_pizzas],
        )

    async def list_menu(self) -> str:
        """Return a short summary of the Beauty Pizza menu: the flavor names with their starting price, the
        starting price of each size (from_price_by_size) and the crusts. Use it to present the menu in one sentence.
        For the ingredients and every price of one flavor, use get_pizza_details."""
        try:
            return ok(**menu_summary(await self._menu.get_menu()))
        except MenuServiceError:
            return menu_unavailable()

    async def get_pizza_details(self, flavor: str) -> str:
        """Get the description, ingredients and every valid size/crust/price combination of one flavor.

        Args:
            flavor: Flavor as written by the customer (typos are tolerated), e.g. "calabresa", "4 queijos".
        """
        try:
            match = await self._menu.resolve_flavor(flavor)
        except MenuServiceError:
            return menu_unavailable()
        if error := flavor_error(match):
            return error
        pizza = match.pizza
        assert pizza is not None
        return ok(
            flavor=pizza.flavor,
            description=pizza.description,
            ingredients=pizza.ingredients,
            prices=price_rows(pizza.prices),
        )

    async def get_price(self, flavor: str, size: str, crust: str = "Tradicional") -> str:
        """Get the exact price of a pizza combination. If the combination does not exist, returns the valid
        alternatives for the flavor.

        Args:
            flavor: Flavor, e.g. "Calabresa".
            size: Size, e.g. "Pequena", "Média" or "Grande".
            crust: Crust, e.g. "Tradicional", "Recheada com Cheddar" or "Recheada com Catupiry". Default Tradicional.
        """
        try:
            quote = await self._menu.quote(flavor, size, crust)
        except MenuServiceError:
            return menu_unavailable()
        if error := quote_error(quote):
            return error
        assert quote.pizza is not None and quote.price is not None
        return ok(
            available=True,
            flavor=quote.pizza.flavor,
            size=quote.size,
            crust=quote.crust,
            price=f"{quote.price:.2f}",
            price_display=format_brl(quote.price),
        )

    async def search_menu(self, query: str, limit: int = 3) -> str:
        """Semantic search over the menu for descriptive questions, e.g. "sem carne", "algo doce",
        "bastante queijo", "frango cremoso". Results always come from the menu database.

        Args:
            query: The customer's description in their own words.
            limit: Max number of results (1 to 6).
        """
        limit = max(1, min(int(limit), 6))
        try:
            hits = await self._menu.search(query, limit)
        except MenuServiceError:
            return menu_unavailable()
        return ok(query=query, results=[_pizza_card(hit) for hit in hits])

    async def suggest_pizzas(self, run_context: RunContext, preferences: str | None = None) -> str:
        """Pick exactly 2 relevant pizza suggestions (never random). Personalized with the customer's previous
        orders when available. Use it when the customer is undecided or right after greeting.

        Args:
            preferences: Optional customer preferences in their own words, e.g. "sem carne", "doce".
        """
        state = run_context.session_state or {}
        previous = [f for f in state.get("previous_flavors", []) if isinstance(f, str)]
        if preferences:
            query, exclude = preferences, set()
        elif previous:
            query, exclude = SIMILAR_QUERY_TEMPLATE.format(flavors=", ".join(previous)), set(previous)
        else:
            query, exclude = DEFAULT_SUGGESTION_QUERY, set()

        try:
            hits = await self._menu.search(query, limit=6)
        except MenuServiceError:
            return menu_unavailable()
        picks = [hit for hit in hits if hit.pizza.flavor not in exclude][:2]
        if len(picks) < 2:  # e.g. the customer already ordered almost every flavor
            seen = {hit.pizza.id for hit in picks}
            picks += [hit for hit in hits if hit.pizza.id not in seen][: 2 - len(picks)]
        return ok(
            based_on="preferences" if preferences else ("order_history" if previous else "popular"),
            suggestions=[_pizza_card(hit) for hit in picks],
        )


def _pizza_card(hit: MenuSearchHit) -> dict[str, Any]:
    """Summarize a pizza for search and suggestion results."""
    pizza = hit.pizza
    return {
        "flavor": pizza.flavor,
        "description": pizza.description,
        "ingredients": pizza.ingredients,
        "from_price_display": format_brl(pizza.min_price) if pizza.min_price is not None else None,
        "up_to_price_display": format_brl(pizza.max_price) if pizza.max_price is not None else None,
        "relevance": hit.relevance,
    }
