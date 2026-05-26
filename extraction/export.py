"""CSV export for PO emails.

Flattens a list of inbox messages (each already enriched by
``ExtractionService`` and ``parse_tables``) into the MASTER-schema CSV
shape: one row per parsed table item, with email metadata repeated on
each row. Emails with no parsed items still emit a single row so they
remain visible in the export.
"""

import csv
import io
from typing import Any

from extraction.tables import SIZE_COLUMNS

EXPORT_COLUMNS: tuple[str, ...] = (
    "Subject", "From", "From Name", "Date",
    "Has Attachments", "Body Preview",
    "Item Name", "Type", "Contract No", "Item Category",
    *SIZE_COLUMNS,
)

_ITEM_COLUMNS: tuple[str, ...] = (
    "Item Name", "Type", "Contract No", "Item Category",
    *SIZE_COLUMNS,
)


def _base_row(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "Subject": message.get("subject", ""),
        "From": message.get("from", ""),
        "From Name": message.get("from_name", ""),
        "Date": message.get("received_at", ""),
        "Has Attachments": message.get("has_attachments", False),
        "Body Preview": (message.get("preview") or "")[:200],
    }


def _item_row_from_table_row(table_row: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "Item Name": table_row.get("Item Category", ""),
        "Type": table_row.get("Type", ""),
        "Contract No": table_row.get("Contract No", ""),
        "Item Category": table_row.get("Item Category", ""),
    }
    for size in SIZE_COLUMNS:
        value = table_row.get(size)
        row[size] = "" if value is None else value
    return row


def _blank_item_row() -> dict[str, Any]:
    return {col: "" for col in _ITEM_COLUMNS}


def build_export_rows(po_emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One dict per parsed item per PO email, in ``EXPORT_COLUMNS`` order.

    Emails with no parsed items still emit a single row with blank item
    columns so they remain visible in downstream views.
    """
    rows: list[dict[str, Any]] = []
    for message in po_emails:
        base = _base_row(message)
        table_rows = message.get("table_rows") or []
        if not table_rows:
            rows.append({**base, **_blank_item_row()})
            continue
        for table_row in table_rows:
            rows.append({**base, **_item_row_from_table_row(table_row)})
    return rows


def build_export_csv(po_emails: list[dict[str, Any]]) -> bytes:
    """Return UTF-8 BOM CSV bytes — one row per parsed item per PO email."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(build_export_rows(po_emails))
    return buf.getvalue().encode("utf-8-sig")
