"""Uniform JSON results returned by tools to the model."""

import json
from typing import Any


def ok(**data: Any) -> str:
    """Build a successful tool result as JSON."""
    return json.dumps({"ok": True, **data}, ensure_ascii=False, default=str)


def fail(error_code: str, message: str, **data: Any) -> str:
    """Build a failed tool result as JSON with an error code and a message for the model."""
    return json.dumps(
        {"ok": False, "error_code": error_code, "message": message, **data}, ensure_ascii=False, default=str
    )
