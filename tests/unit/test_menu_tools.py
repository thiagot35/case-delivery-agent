import json
from pathlib import Path

from delivery_agent.tools.menu_tools import MenuTools
from menu_service.service import MenuService
from tests.conftest import FakeIndex, FakeMenuApi, make_run_context


def make_tools(menu_api: FakeMenuApi) -> MenuTools:
    """Menu tools over the in-process Menu service."""
    return MenuTools(menu_api)  # type: ignore[arg-type]


async def test_list_menu_is_a_summary(menu_api: FakeMenuApi) -> None:
    """list_menu returns flavor names and starting prices per size, never ingredients or the price table (007)."""
    raw = await make_tools(menu_api).list_menu()
    result = json.loads(raw)
    assert result["ok"] is True
    assert {f["flavor"] for f in result["flavors"]} >= {"Frango com Catupiry", "Calabresa"}
    assert result["from_price_by_size"] == [
        {"size": "Pequena", "from_price_display": "R$ 25,00"},
        {"size": "Média", "from_price_display": "R$ 35,00"},
        {"size": "Grande", "from_price_display": "R$ 45,00"},
    ]
    assert "mussarela" not in raw and "R$ 51,00" not in raw  # no ingredients, no full price table
    assert menu_api.calls == ["get_menu"]


async def test_get_pizza_details(menu_api: FakeMenuApi) -> None:
    """Details include ingredients and every valid price."""
    result = json.loads(await make_tools(menu_api).get_pizza_details("4 queijos"))
    assert result["flavor"] == "Quatro Queijos"
    assert "gorgonzola" in result["ingredients"]
    assert len(result["prices"]) == 7


async def test_get_price(menu_api: FakeMenuApi) -> None:
    """get_price returns exact prices and alternatives for unavailable combinations."""
    tools = make_tools(menu_api)
    ok = json.loads(await tools.get_price("calabresa", "grande", "borda de cheddar"))
    assert ok["price_display"] == "R$ 50,00"

    unavailable = json.loads(await tools.get_price("Doce de leite com coco", "Grande", "catupiry"))
    assert unavailable["error_code"] == "combination_unavailable"
    assert {a["crust"] for a in unavailable["alternatives"]} == {"Tradicional"}


async def test_search_menu_semantic(menu_api: FakeMenuApi) -> None:
    """search_menu returns the Menu service ranking with the price range."""
    result = json.loads(await make_tools(menu_api).search_menu("tem alguma sem carne?", limit=3))
    assert [r["flavor"] for r in result["results"]] == ["Margherita", "Quatro Queijos", "Doce de Leite com Coco"]
    assert result["results"][0]["from_price_display"] == "R$ 25,00"
    assert result["results"][0]["up_to_price_display"] == "R$ 49,00"


async def test_search_menu_keyword_fallback(menu_db: Path) -> None:
    """Without a vector index the Menu service falls back to keyword ranking."""
    menu_api = FakeMenuApi(MenuService(menu_db, None))
    result = json.loads(await make_tools(menu_api).search_menu("gorgonzola", limit=1))
    assert result["results"][0]["flavor"] == "Quatro Queijos"


async def test_menu_unavailable(menu_api: FakeMenuApi) -> None:
    """Every menu tool answers with menu_unavailable when the Menu service is down."""
    menu_api.unavailable = True
    tools = make_tools(menu_api)
    results = [
        await tools.list_menu(),
        await tools.get_pizza_details("calabresa"),
        await tools.get_price("calabresa", "grande"),
        await tools.search_menu("queijo"),
        await tools.suggest_pizzas(make_run_context()),
    ]
    assert all(json.loads(r)["error_code"] == "menu_unavailable" for r in results)


async def test_suggestions_popular(menu_api: FakeMenuApi) -> None:
    """Without history or preferences, two popular pizzas are suggested."""
    result = json.loads(await make_tools(menu_api).suggest_pizzas(make_run_context()))
    assert result["based_on"] == "popular"
    assert len(result["suggestions"]) == 2


async def test_suggestions_from_preferences(menu_api: FakeMenuApi) -> None:
    """Suggestions follow the customer's stated preferences."""
    result = json.loads(await make_tools(menu_api).suggest_pizzas(make_run_context(), preferences="queijo"))
    assert result["based_on"] == "preferences"
    assert [s["flavor"] for s in result["suggestions"]] == ["Quatro Queijos", "Margherita"]


async def test_suggestions_personalized_exclude_previous(menu_api: FakeMenuApi, fake_index: FakeIndex) -> None:
    """History-based suggestions exclude flavors already ordered."""
    rc = make_run_context({"previous_flavors": ["Calabresa"]})
    result = json.loads(await make_tools(menu_api).suggest_pizzas(rc))
    flavors = [s["flavor"] for s in result["suggestions"]]
    assert result["based_on"] == "order_history"
    assert "Calabresa" not in flavors
    assert flavors == ["Pepperoni", "Frango com Catupiry"]
    assert "Calabresa" in fake_index.queries[-1]


def test_agent_has_no_menu_change_tools(menu_api: FakeMenuApi, order_api) -> None:
    """The conversational agent only gets read and order tools: nothing that changes the menu."""
    from delivery_agent.tools.order_tools import OrderTools

    order_tools = OrderTools(order_api, menu_api)  # type: ignore[arg-type]
    names = set(make_tools(menu_api).get_async_functions()) | set(order_tools.get_async_functions())
    assert names == {
        "list_menu",
        "get_pizza_details",
        "get_price",
        "search_menu",
        "suggest_pizzas",
        "get_customer_history",
        "get_current_order",
        "add_pizza",
        "remove_item",
        "repeat_last_order",
        "get_delivery_address",
        "confirm_delivery_address",
        "set_delivery_address",
        "confirm_order",
        "cancel_order",
    }
