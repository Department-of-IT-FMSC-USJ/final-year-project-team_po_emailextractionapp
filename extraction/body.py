"""Rule-based extraction from email body text.

Generic patterns — domain-specific overrides belong in
``extraction/rules/`` (loaded via ``rules_path`` once implemented).
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

# --- PO number --------------------------------------------------------

_PO_PATTERNS: list[re.Pattern[str]] = [
    # MEL-style and similar: 2-4 letter prefix + 4-digit year + "PO" + digits
    # e.g. MEL2025PO12345
    re.compile(r"\b[A-Z]{2,4}\d{4}PO\d+\b", re.IGNORECASE),
    # Labeled: "Purchase Order Number: XXX" / "Purchase Order #: XXX"
    re.compile(
        r"\bPurchase\s*Order\s*(?:Number|No\.?|#)?\s*[:\-]\s*([A-Z0-9\-]{3,})\b",
        re.IGNORECASE,
    ),
    # Labeled: "PO#: XXX" / "PO Number: XXX"
    re.compile(r"\bP[O0]\s*(?:Number|No\.?|#)?\s*[:\-]\s*([A-Z0-9\-]{3,})\b", re.IGNORECASE),
]


def _extract_po_number(text: str) -> str | None:
    for pat in _PO_PATTERNS:
        match = pat.search(text)
        if match:
            return (match.group(1) if match.groups() else match.group(0)).upper()
    return None


# --- Supplier ---------------------------------------------------------

_SUPPLIER_PATTERN = re.compile(
    r"(?im)^\s*(?:supplier|vendor|seller|manufacturer|company)(?:\s*name)?"
    r"\s*[:\-]\s*([^\n]{2,120})"
)
_COMPANY_SUFFIX = re.compile(
    r"\b(?:LTD|LIMITED|LLC|INC|CORP|CO\.?|PVT\.?\s*LTD)\b", re.IGNORECASE
)
_SIGNOFF_TAIL = re.compile(
    r"\b(?:thanks|thank you|regards|best regards|sincerely)\b.*$", re.IGNORECASE
)


def _clean_supplier(candidate: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", candidate).strip(" \t\r\n-:;,.")
    cleaned = _SIGNOFF_TAIL.sub("", cleaned).strip()
    if len(cleaned) < 3 or any(t in cleaned.lower() for t in ("@", "http://", "https://")):
        return None
    return cleaned[:120]


def _extract_supplier(text: str) -> str | None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    match = _SUPPLIER_PATTERN.search(normalized)
    if match:
        return _clean_supplier(match.group(1))
    # Fallback: signature lines ending with a company suffix.
    tail = [ln.strip() for ln in normalized.split("\n")[-8:] if ln.strip()]
    for line in tail:
        if _COMPANY_SUFFIX.search(line):
            cleaned = _clean_supplier(line)
            if cleaned:
                return cleaned
    return None


# --- Date -------------------------------------------------------------

_DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%m/%d/%Y", "%m-%d-%Y",
    "%Y/%m/%d", "%Y-%m-%d",
    "%d/%m/%y", "%d-%m-%y",
    "%d %b %Y", "%d %B %Y",
    "%b %d %Y", "%B %d %Y",
    "%b %d, %Y", "%B %d, %Y",
)
_DATE_PATTERN = re.compile(
    r"\b("
    r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"            # 12/05/2024
    r"|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"             # 2024-05-12
    r"|\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{2,4}"         # 12 May 2024
    r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}"         # May 12, 2024
    r")\b"
)
_DATE_LABEL_PATTERN = re.compile(
    r"(?im)\b(?:date|delivery\s*date|due\s*date|order\s*date|po\s*date|ship\s*date)"
    r"\s*[:\-]\s*([^\n,]{4,40})"
)


def _normalize_date(value: str) -> str | None:
    candidate = value.strip().rstrip(",").rstrip(".")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _extract_date(text: str) -> str | None:
    label_match = _DATE_LABEL_PATTERN.search(text)
    if label_match:
        candidate = label_match.group(1).strip()
        normalized = _normalize_date(candidate)
        if normalized:
            return normalized
        inner = _DATE_PATTERN.search(candidate)
        if inner:
            normalized = _normalize_date(inner.group(1))
            if normalized:
                return normalized
    match = _DATE_PATTERN.search(text)
    if match:
        return _normalize_date(match.group(1))
    return None


# --- Amount / total ---------------------------------------------------

_AMOUNT_PATTERN = re.compile(
    r"(?im)\b(?:grand\s*total|sub[\- ]?total|invoice\s*total|total|amount|due|balance)\b"
    r"[^\n\d\$£€]{0,15}"
    r"([\$£€]?\s*[\d,]+(?:\.\d{1,2})?)"
)


def _extract_amount(text: str) -> str | None:
    match = _AMOUNT_PATTERN.search(text)
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1))


# --- Item codes -------------------------------------------------------

_ITEM_CODE_PATTERN = re.compile(r"\b[A-Z]{2,4}\d+[A-Z]?\d*-[A-Z]?\d+\b")


def _extract_item_codes(text: str) -> list[str]:
    # Dedupe while preserving order; cap so a noisy email can't blow up the UI.
    seen: dict[str, None] = {}
    for code in _ITEM_CODE_PATTERN.findall(text):
        seen.setdefault(code.upper(), None)
        if len(seen) >= 20:
            break
    return list(seen)


# --- Public entry point -----------------------------------------------


def extract_from_body(
    body_text: str,
    rules_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Run all body regex extractors. Returns ``(fields, provenance)``.

    Each key in ``fields`` has a matching entry in ``provenance`` that
    identifies the source (e.g. ``"body:regex"``).
    """
    _ = rules_path  # reserved for loading custom rules from extraction/rules/
    text = body_text or ""
    fields: dict[str, Any] = {}
    provenance: dict[str, str] = {}

    if (po := _extract_po_number(text)) is not None:
        fields["po_number"] = po
        provenance["po_number"] = "body:regex"
    if (supplier := _extract_supplier(text)) is not None:
        fields["supplier"] = supplier
        provenance["supplier"] = "body:regex"
    if (date := _extract_date(text)) is not None:
        fields["date"] = date
        provenance["date"] = "body:regex"
    if (amount := _extract_amount(text)) is not None:
        fields["amount"] = amount
        provenance["amount"] = "body:regex"
    if codes := _extract_item_codes(text):
        fields["item_codes"] = codes
        provenance["item_codes"] = "body:regex"

    return fields, provenance
