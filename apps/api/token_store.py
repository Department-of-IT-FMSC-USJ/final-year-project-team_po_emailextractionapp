"""In-memory Outlook token store — single session, no persistence.

The token lives only in this process and is lost on restart. Mirrors the
previous project's single-user session model. Swap for a repository to
make it durable and multi-user.
"""

from typing import Any

_current: dict[str, Any] | None = None


def save_tokens(tokens: dict[str, Any]) -> None:
    """Store the token result from a login or refresh."""
    global _current
    _current = tokens


def get_tokens() -> dict[str, Any] | None:
    """Return the current session's token result, or None if not signed in."""
    return _current


def clear_tokens() -> None:
    global _current
    _current = None
