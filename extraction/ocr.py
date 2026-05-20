from pathlib import Path
from typing import Any


def extract_from_attachments(
    attachment_paths: list[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """OCR / document parsing from image attachments."""
    fields: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for path in attachment_paths:
        p = Path(path)
        if not p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}:
            continue
        # TODO: run OCR (tesseract, azure doc intelligence, etc.)
        _ = p
    return fields, provenance
