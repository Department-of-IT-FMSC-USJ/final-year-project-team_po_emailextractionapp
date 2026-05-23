"""HTML table extraction for PO emails.

Ports the previous project's MEL-style table parsing into the new
architecture. Each parsed row follows the MASTER schema (Type,
Contract No, Item Category, size columns..., Total) so downstream code
(CSV export, dashboards) can rely on a stable shape.

Uses BeautifulSoup with the stdlib ``html.parser`` — no extra binary
dependencies.
"""

import re
from typing import Any

from bs4 import BeautifulSoup

# --- Schema ----------------------------------------------------------

SIZE_COLUMNS: tuple[str, ...] = (
    "5lb",
    "First Size",
    "Up To 1Mth",
    "Up To 3Mth",
    "3-6 Mths",
    "6-9 Mths",
    "9-12 Mths",
    "12-18 Mths",
    "1.5-2 Yrs",
    "Total",
)
TEXT_COLUMNS: tuple[str, ...] = ("Type", "Contract No", "Item Category")
MASTER_COLUMNS: tuple[str, ...] = TEXT_COLUMNS + SIZE_COLUMNS

# Aliases that should map to canonical size column names.
_SIZE_ALIASES: dict[str, frozenset[str]] = {
    "5lb": frozenset({"5lb", "5 lb"}),
    "First Size": frozenset({"first size", "firstsize"}),
    "Up To 1Mth": frozenset({
        "up to 1mth", "up to 1 mth", "upto 1mth", "up to 1 month", "up to 1month",
    }),
    "Up To 3Mth": frozenset({
        "up to 3mth", "up to 3 mth", "upto 3mth", "up to 3 month", "up to 3month",
    }),
    "3-6 Mths": frozenset({
        "3-6 mths", "3 6 mths", "3-6 months", "3 to 6 mths", "3-6mth", "3-6 mth",
    }),
    "6-9 Mths": frozenset({
        "6-9 mths", "6 9 mths", "6-9 months", "6 to 9 mths", "6-9mth", "6-9 mth",
    }),
    "9-12 Mths": frozenset({
        "9-12 mths", "9 12 mths", "9-12 months", "9 to 12 mths", "9-12mth", "9-12 mth",
    }),
    "12-18 Mths": frozenset({
        "12-18 mths", "12 18 mths", "12-18 months", "12 to 18 mths", "12-18mth",
    }),
    "1.5-2 Yrs": frozenset({
        "1.5-2 yrs", "15-2 yrs", "1.5 2 yrs", "1.5-2 years",
    }),
    "Total": frozenset({"total"}),
}

# Contract numbers in the previous project followed prefixes VA/VJ/VQ/VB
# (allow any 2-letter prefix to stay flexible).
_CONTRACT_NO_PATTERN = re.compile(r"\b[A-Z]{2}\d{6,}\b")

# Recognized row labels — these become "Item Category" values.
_ITEM_TYPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*7\s*%\s*$"), "7%"),
    (re.compile(r"\bprice\s*ticket\b", re.IGNORECASE), "PRICE TICKET"),
    (re.compile(r"\bcarto+n\s*sticker\b|\bcarton\s*stk\b", re.IGNORECASE), "CARTON STICKER"),
    (re.compile(r"\blaminat(?:ing|e)\b", re.IGNORECASE), "LAMINATING"),
    (re.compile(r"\bpos\b", re.IGNORECASE), "POS"),
    (re.compile(r"\bbase\s*(?:qty|quantity)?\b", re.IGNORECASE), "Base Qty"),
)


# --- Helpers ---------------------------------------------------------


def _canonical_size(raw: str) -> str | None:
    """Return the canonical SIZE_COLUMNS key for a header cell, or None."""
    if not raw:
        return None
    normalized = re.sub(r"\s+", " ", raw.strip().lower())
    normalized = normalized.replace(".", "").replace("_", " ")
    normalized = normalized.replace("months", "mths").replace("month", "mth")
    normalized = normalized.replace("years", "yrs").replace("year", "yrs")
    for canonical, aliases in _SIZE_ALIASES.items():
        if normalized in aliases:
            return canonical
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _identify_item_type(label: str) -> str | None:
    text = label.strip()
    if not text:
        return None
    for pattern, name in _ITEM_TYPE_PATTERNS:
        if pattern.search(text):
            return name
    return None


def _empty_master_row() -> dict[str, Any]:
    row: dict[str, Any] = {col: "" for col in TEXT_COLUMNS}
    row.update({col: None for col in SIZE_COLUMNS})
    return row


# --- Public API ------------------------------------------------------


def html_to_text(html: str) -> str:
    """Strip HTML to plain text (used by body regex extraction)."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator="\n", strip=True)


def parse_tables(html: str) -> list[dict[str, Any]]:
    """Parse every PO-shaped table in ``html`` into MASTER schema rows."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows.extend(_parse_one_table(table))
    return rows


# --- Per-table parsing -----------------------------------------------


def _extract_cells(tr) -> list[str]:
    cells: list[str] = []
    for cell in tr.find_all(["th", "td"]):
        text = cell.get_text(separator=" ", strip=True)
        cells.append(re.sub(r"\s+", " ", text))
    return cells


def _find_header_row(rows: list[list[str]]) -> int | None:
    """Pick the row with the most size-shaped header cells (first 8 only)."""
    best_idx: int | None = None
    best_score = 0
    for idx, row in enumerate(rows[:8]):
        score = sum(1 for cell in row if _canonical_size(cell) is not None)
        if score > best_score:
            best_score = score
            best_idx = idx
    # Require at least 3 recognizable size columns to consider this a PO table.
    return best_idx if best_score >= 3 else None


def _row_item_type(row: list[str], size_indices: set[int]) -> str | None:
    """Pick the first non-size cell whose text matches a known item type.

    Tables sometimes have multiple text columns per row (e.g.
    ``Online | VB9910047 | POS | …``); only ``POS`` here is the item
    label, the others are the Type and Contract No values.
    """
    for idx, cell in enumerate(row):
        if idx in size_indices:
            continue
        item_type = _identify_item_type(cell.strip())
        if item_type:
            return item_type
    return None


def _find_po_type(rows: list[list[str]]) -> str:
    """Detect Online/Retail anywhere in the table."""
    for row in rows:
        for cell in row:
            lower = cell.lower()
            if re.search(r"\bonline\b", lower):
                return "Online"
            if re.search(r"\bretail\b", lower):
                return "Retail"
    return ""


def _parse_one_table(table) -> list[dict[str, Any]]:
    raw_rows = [_extract_cells(tr) for tr in table.find_all("tr")]
    raw_rows = [r for r in raw_rows if any(c.strip() for c in r)]
    if len(raw_rows) < 2:
        return []

    header_idx = _find_header_row(raw_rows)
    if header_idx is None:
        return []
    headers = raw_rows[header_idx]

    # column index -> canonical size column name
    size_columns: dict[int, str] = {}
    for idx, cell in enumerate(headers):
        canonical = _canonical_size(cell)
        if canonical:
            size_columns[idx] = canonical

    # Many PO tables have a trailing "Total" column with no header (just an
    # empty <th>). If "Total" isn't already mapped, look for an unmapped
    # rightmost column that is mostly numeric and treat it as Total.
    if "Total" not in size_columns.values():
        data_rows = raw_rows[header_idx + 1 :]
        for idx in range(len(headers) - 1, -1, -1):
            if idx in size_columns:
                continue
            non_empty = 0
            numeric = 0
            for row in data_rows:
                if idx >= len(row):
                    continue
                cell = row[idx].strip()
                if cell:
                    non_empty += 1
                    if _to_float(cell) is not None:
                        numeric += 1
            if non_empty >= 2 and numeric / non_empty >= 0.6:
                size_columns[idx] = "Total"
                break

    size_indices = set(size_columns)

    # Contract number — search every cell in the table for the pattern.
    flat = " ".join(c for row in raw_rows for c in row)
    contract_match = _CONTRACT_NO_PATTERN.search(flat.upper())
    contract_no = contract_match.group(0) if contract_match else ""

    # Type — Online or Retail (if either word appears anywhere in the table).
    po_type = _find_po_type(raw_rows)

    extracted: list[dict[str, Any]] = []
    for row in raw_rows[header_idx + 1 :]:
        # Pad row to header length if cells are missing on the right.
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))

        item_type = _row_item_type(row, size_indices)
        if not item_type:
            continue

        master_row = _empty_master_row()
        master_row["Type"] = po_type
        master_row["Contract No"] = contract_no
        master_row["Item Category"] = item_type

        any_value = False
        for idx, canonical in size_columns.items():
            value = _to_float(row[idx]) if idx < len(row) else None
            if value is not None:
                master_row[canonical] = value
                any_value = True

        if any_value:
            extracted.append(master_row)

    return extracted
