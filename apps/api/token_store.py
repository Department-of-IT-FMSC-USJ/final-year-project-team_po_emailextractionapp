"""In-memory Outlook token store.

No database — tokens live only in this process and are lost on restart.
Shared by the auth callback (writes) and the inbox route (reads).
Swap these functions for a repository to make sessions durable.
"""

from typing import Any

_tokens: dict[str, dict[str, Any]] = {}


def save_tokens(user_id: str, tokens: dict[str, Any]) -> None:
    _tokens[user_id] = tokens


def get_tokens(user_id: str) -> dict[str, Any] | None:
    return _tokens.get(user_id)


def latest_user_id() -> str | None:
    """The most recently connected user — a single-session dev convenience."""
    return next(reversed(_tokens), None) if _tokens else None
