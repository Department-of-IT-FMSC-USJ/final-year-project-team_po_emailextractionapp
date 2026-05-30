"""CSV export for PO emails.

Flattens a list of inbox messages (each already enriched by
``ExtractionService`` and ``parse_tables``) into the MASTER-schema CSV
shape: one row per parsed table item, with email metadata repeated on
each row. Emails with no parsed items still emit a single row so they
remain visible in the export.

Three per-row quality checks are appended to each row:

  1. **Has Sizes** — at least one of the 9 size columns
     (``5lb`` through ``1.5-2 Yrs``) has a value. ``Total`` is *not*
     part of this check because Base-Qty / 7% rows legitimately don't
     populate it.
  2. **No Duplicates In Email** — no other row in the same email shares
     this row's ``(Contract No, Item Category, Type)`` triple.
  3. **All Sizes Under 5000** — every numeric size cell in the 9 non-Total
     columns is ≤ 5000 (catches decimal-loss OCR bugs like ``1551.50``
     → ``155150``). ``Total`` is excluded — legit totals routinely
     exceed 5000.

Each check column stores a Python ``bool`` in the row dict. The CSV
export serializes those as the literal strings ``True`` / ``False``
(matching ``Has Attachments``); the Streamlit dataframe view passes the
rows through :func:`format_quality_checks_for_ui` first to display
``✅`` / ``❌``.
"""

import csv
import io
from typing import Any

from extraction.tables import SIZE_COLUMNS

_UI_PASS = "✅"
_UI_FAIL = "❌"
_MAX_SIZE_VALUE = 5000

# Size columns checked by "Has Sizes" — Total is excluded because rows
# like Base Qty / 7% don't carry a row-level Total by design.
_NON_TOTAL_SIZE_COLUMNS: tuple[str, ...] = tuple(
    c for c in SIZE_COLUMNS if c != "Total"
)

QUALITY_CHECK_COLUMNS: tuple[str, ...] = (
    "Has Sizes",
    "No Duplicates In Email",
    "All Sizes Under 5000",
)

EXPORT_COLUMNS: tuple[str, ...] = (
    "Subject", "From", "From Name", "Date",
    "Has Attachments", "Body Preview",
    "Item Name", "Type", "Contract No", "Item Category",
    *SIZE_COLUMNS,
    *QUALITY_CHECK_COLUMNS,
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


# --- Quality checks ---------------------------------------------------------


def _has_any_size(row: dict[str, Any]) -> bool:
    """True iff at least one non-Total size column carries a value.

    Total is excluded because rows like Base Qty / 7% don't populate a
    row-level Total by design — the check would otherwise misfire on
    every Base-Qty row in the corpus. Fails only when all 9 size cells
    are empty (typically an email with no parsed table at all).
    """
    for col in _NON_TOTAL_SIZE_COLUMNS:
        v = row.get(col)
        if v is not None and v != "":
            return True
    return False


def _all_sizes_under_threshold(row: dict[str, Any], threshold: float) -> bool:
    """True iff every numeric *non-Total* size cell is ≤ ``threshold``.

    Total is excluded for the same reason it's excluded from Has Sizes:
    legitimate row totals (e.g. PRICE TICKET ≈ 6100) routinely exceed
    the threshold, so flagging them would mask the real signal — a
    decimal-loss OCR bug in one of the bracket columns. Empty cells are
    ignored (they can't violate the cap); non-numeric junk in a size
    column counts as a violation so it gets surfaced.
    """
    for col in _NON_TOTAL_SIZE_COLUMNS:
        v = row.get(col)
        if v is None or v == "":
            continue
        try:
            if float(v) > threshold:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _identity_triple(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("Contract No", "")),
        str(row.get("Item Category", "")),
        str(row.get("Type", "")),
    )


def _apply_quality_checks(email_rows: list[dict[str, Any]]) -> None:
    """Annotate each row with the three quality-check columns in-place.

    Check 2 is computed per email-group so duplicate detection sees only
    the rows of one email at a time.
    """
    triple_counts: dict[tuple[str, str, str], int] = {}
    for row in email_rows:
        key = _identity_triple(row)
        triple_counts[key] = triple_counts.get(key, 0) + 1

    for row in email_rows:
        # "Has Sizes" is the gate — when the row has no size data at all,
        # the other two checks are vacuously true (no values to dupe, no
        # values to exceed the cap). That's misleading: a row with no
        # sizes is not a clean row. Cascade Has Sizes → all three.
        has_sizes = _has_any_size(row)
        if not has_sizes:
            row["Has Sizes"] = False
            row["No Duplicates In Email"] = False
            row["All Sizes Under 5000"] = False
            continue

        key = _identity_triple(row)
        row["Has Sizes"] = True
        row["No Duplicates In Email"] = triple_counts[key] <= 1
        row["All Sizes Under 5000"] = _all_sizes_under_threshold(
            row, _MAX_SIZE_VALUE
        )


# --- Row builder ------------------------------------------------------------


def build_export_rows(po_emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One dict per parsed item per PO email, in ``EXPORT_COLUMNS`` order.

    Per-row quality checks are computed *inside each email's group* so
    Check 2 (no duplicates) only sees that email's rows. Emails with no
    parsed items still emit a single row with blank item columns so
    they remain visible in downstream views.
    """
    rows: list[dict[str, Any]] = []
    for message in po_emails:
        base = _base_row(message)
        table_rows = message.get("table_rows") or []
        if not table_rows:
            email_rows = [{**base, **_blank_item_row()}]
        else:
            email_rows = [
                {**base, **_item_row_from_table_row(tr)} for tr in table_rows
            ]
        _apply_quality_checks(email_rows)
        rows.extend(email_rows)
    return rows


def build_export_csv(po_emails: list[dict[str, Any]]) -> bytes:
    """Return UTF-8 BOM CSV bytes — one row per parsed item per PO email.

    Quality-check columns serialize as the strings ``True`` / ``False``
    (Python's csv module calls ``str()`` on the bool values).
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(build_export_rows(po_emails))
    return buf.getvalue().encode("utf-8-sig")


def format_quality_checks_for_ui(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a copy of ``rows`` with quality-check booleans rendered as emoji.

    Source rows are not mutated — :func:`build_export_csv` keeps emitting
    ``True`` / ``False`` while the Streamlit dataframe view gets
    ``✅`` / ``❌`` glyphs.
    """
    formatted: list[dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        for col in QUALITY_CHECK_COLUMNS:
            val = new_row.get(col)
            if val is True:
                new_row[col] = _UI_PASS
            elif val is False:
                new_row[col] = _UI_FAIL
        formatted.append(new_row)
    return formatted
