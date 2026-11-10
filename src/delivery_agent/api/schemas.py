"""HTTP contract (contracts/delivery-agent-api.md)."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from delivery_agent.domain.cpf import is_valid_cpf, normalize_cpf
from delivery_agent.domain.models import OrderSnapshot


class MessageRequest(BaseModel):
    client_name: str = Field(min_length=1, max_length=300)
    client_document: str
    message: str = Field(min_length=1, max_length=2000)
    # Optional customer city (specs/005-special-dates): adds its local holidays to the first agent reply.
    location: str | None = Field(default=None, max_length=100)

    @field_validator("client_name", "message", mode="before")
    @classmethod
    def _strip(cls, value: str) -> str:
        """Trim surrounding whitespace before the length checks."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("client_document")
    @classmethod
    def _validate_cpf(cls, value: str) -> str:
        """Reject invalid CPFs and normalize valid ones to 11 digits."""
        if not is_valid_cpf(value):
            raise ValueError("client_document must be a valid CPF")
        return normalize_cpf(value)

    @field_validator("location", mode="before")
    @classmethod
    def _blank_location(cls, value: str | None) -> str | None:
        """Trim the location; blank means not set."""
        if isinstance(value, str):
            value = value.strip()
        return value or None


Route = Literal["agent", "greeting", "not_delivery", "human_handoff", "blocked"]


class MessageResponse(BaseModel):
    reply: str
    session_id: str
    new_session: bool
    # Which branch of the guardrail pipeline answered (agent = the order agent).
    route: Route = "agent"
    order: OrderSnapshot | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    order_api: Literal["ok", "unreachable"]
    menu_service: Literal["ok", "unreachable"]
    menu_index: Literal["ready", "missing"]
