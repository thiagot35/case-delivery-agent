"""Interfaces of the semantic index used by the resolver (read) and by the Menu service (read + write)."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from menu_service.repository import MenuRepository


class SemanticIndex(Protocol):
    """Read side of the vector index: what `MenuResolver` and the search need."""

    def search(self, query: str, limit: int = 3) -> list[tuple[int, float]]:
        """Return `(pizza_id, cosine_distance)` pairs, closest first."""
        ...


class MenuIndex(SemanticIndex, Protocol):
    """Read + write side of the vector index: what `MenuService` needs to keep it in step with `menu.db`."""

    def sync(self, repository: "MenuRepository") -> None:
        """Make the index match the whole menu: upsert changed documents and delete orphans."""
        ...

    def refresh(self, repository: "MenuRepository", pizza_ids: set[int]) -> None:
        """Re-embed the given pizzas from `repository`, removing those that no longer exist or have no price."""
        ...

    @property
    def ready(self) -> bool:
        """Tell whether the index can answer searches."""
        ...
