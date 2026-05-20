from pathlib import Path
from typing import Any


def extract_from_body(
    body_text: str,
    rules_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Rule-based / regex extraction from email body."""
    _ = rules_path
    fields: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    # TODO: load rules from rules_path and apply
    _ = body_text
    return fields, provenance
