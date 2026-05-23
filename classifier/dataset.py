"""Training-label storage for the classifier — a JSONL file, no database.

Each line is one labeled email::

    {"email_id", "subject", "body_text", "label", "labeled_at"}

``label`` is either ``"po"`` or ``"not_po"``. Re-labeling an email
overwrites its previous entry.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import settings

PO_LABEL = "po"
NOT_PO_LABEL = "not_po"
VALID_LABELS = frozenset({PO_LABEL, NOT_PO_LABEL})


def _labels_path() -> Path:
    return settings.classifier_labels_path


def load_labels() -> list[dict[str, Any]]:
    """Read every stored label record (skips any corrupt lines)."""
    path = _labels_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def add_label(email_id: str, subject: str, body_text: str, label: str) -> dict[str, Any]:
    """Store (or replace) the label for one email and return the record."""
    if label not in VALID_LABELS:
        raise ValueError(f"label must be one of {sorted(VALID_LABELS)}")

    record = {
        "email_id": email_id,
        "subject": subject,
        "body_text": body_text,
        "label": label,
        "labeled_at": datetime.now(timezone.utc).isoformat(),
    }
    # Drop any earlier label for the same email so re-labeling overwrites.
    kept = [r for r in load_labels() if r.get("email_id") != email_id]
    kept.append(record)

    path = _labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for entry in kept:
            handle.write(json.dumps(entry) + "\n")
    return record


def label_counts() -> dict[str, int]:
    """Return how many PO / non-PO labels are stored."""
    counts = {PO_LABEL: 0, NOT_PO_LABEL: 0}
    for record in load_labels():
        label = record.get("label")
        if label in counts:
            counts[label] += 1
    return counts


def labels_by_email() -> dict[str, str]:
    """Return ``{email_id: label}`` so the UI can mark already-labeled rows."""
    out: dict[str, str] = {}
    for record in load_labels():
        eid = record.get("email_id")
        label = record.get("label")
        if eid and label in VALID_LABELS:
            out[eid] = label
    return out
