"""Table extraction from image attachments.

Uses Tesseract layout data (``pytesseract.image_to_data``) to recover
the row/column structure that flat OCR loses. Output matches the MASTER
schema produced by :func:`extraction.tables.parse_tables` so the CSV
export and dataframe view stay unchanged.

Pipeline per image:
    1. Decode + (optionally) invert dark-mode images for Tesseract.
    2. ``image_to_data`` → words with bounding boxes + line IDs.
    3. Group words into lines by Tesseract's ``line_num``; sort top-to-bottom.
    4. For every line, greedy-match adjacent words against the canonical
       size-column aliases. Lines with ≥3 matches are header rows.
    5. Each header anchors a table; subsequent lines up to the next
       header are data rows. Per-table contract # and Type are scanned
       from the lines between headers.
    6. For each data row, words left of the leftmost size column = label
       (matched against :data:`_ITEM_TYPE_PATTERNS`, falling back to
       "Base Qty" when empty); numeric words are binned into the nearest
       size column by x-center.
    7. An unlabeled rightmost-numeric column is promoted to "Total" if
       the header doesn't already contain one — mirrors the HTML parser.
"""

import difflib
import io
import logging
import re
from typing import Any

from extraction.tables import (
    _CONTRACT_NO_PATTERN,
    _SIZE_ALIASES,
    SIZE_COLUMNS,
    _canonical_size,
    _empty_master_row,
    _identify_item_type,
    _to_float,
)

log = logging.getLogger("po.image_tables")

try:
    import pytesseract
    from PIL import Image, ImageOps

    _OCR_LIBS = True
except ImportError:
    _OCR_LIBS = False

# Keep every Tesseract token that has *any* reported confidence; only
# drop -1 ("no confidence assigned", typically layout-only artifacts).
# Higher thresholds were silently dropping short numeric / symbol tokens
# (the "7" and "%" of a "7%" row label, single-cell digits like "12")
# that came back with conf in the low single digits.
_MIN_CONFIDENCE = 0
# Column binning tolerance as a fraction of image width. ~6% works well
# for typical 10-column PO tables; clamped to a minimum of 40 px.
_BIN_TOLERANCE_FRAC = 0.06
# When refining column centers from data rows, accept the new median
# only if ≥ this many data values support it.
_REFINE_MIN_SUPPORT = 2
# A data value is treated as a likely decimal-point-loss casualty when
# it exceeds the column's median by this factor (5× picked empirically
# — typical PO tables have intra-column ratios of <3×).
_DECIMAL_LOSS_RATIO = 5.0


def parse_tables_from_image_bytes(image_bytes: bytes) -> list[dict[str, Any]]:
    """Return MASTER-schema rows recovered from a PO table image.

    Returns ``[]`` when OCR libs are missing, the image can't be decoded,
    or no recognizable PO-shaped table is found.
    """
    log.info("image_table.start bytes=%d", len(image_bytes or b""))
    if not _OCR_LIBS:
        log.warning("image_table.skip reason=OCR_LIBS_MISSING (install pytesseract + Pillow)")
        return []
    if not image_bytes:
        log.warning("image_table.skip reason=EMPTY_BYTES")
        return []

    # Lazy import — extraction.ocr imports nothing from us, but keeping
    # the Tesseract config in one place avoids drift.
    from extraction.ocr import _configure_tesseract

    _configure_tesseract()

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            original_mean = _mean_brightness(img)
            img = _maybe_invert_for_dark_mode(img)
            img = _preprocess_for_ocr(img)
            img_width, img_height = img.width, img.height
            log.info(
                "image_table.decoded size=%dx%d mean_brightness=%.1f inverted=%s",
                img_width, img_height, original_mean, original_mean < 100,
            )
            data = pytesseract.image_to_data(
                img, output_type=pytesseract.Output.DICT
            )
    except Exception as exc:  # noqa: BLE001 — Tesseract / Pillow can raise many types
        log.warning("image_table.skip reason=OCR_FAILED error=%r", exc)
        return []

    word_count = sum(1 for t in data.get("text", []) if (t or "").strip())
    log.info("image_table.ocr_words raw=%d", word_count)
    if word_count == 0:
        log.warning("image_table.skip reason=NO_WORDS_FROM_TESSERACT")
        return []

    lines = _group_words_into_lines(data)
    log.info("image_table.lines count=%d kept_min_conf=%d", len(lines), _MIN_CONFIDENCE)
    if not lines:
        log.warning("image_table.skip reason=NO_LINES_AFTER_GROUPING")
        return []

    headers = _find_all_header_lines(lines)
    log.info(
        "image_table.headers count=%d (each line needs >=3 size columns to qualify)",
        len(headers),
    )
    if not headers:
        # Show a sample of detected lines so the user can see what Tesseract saw.
        sample_count = min(8, len(lines))
        for idx in range(sample_count):
            text = " ".join(w["text"] for w in lines[idx])
            log.info("image_table.line_sample[%d]: %s", idx, text[:200])
        log.warning(
            "image_table.skip reason=NO_HEADER_FOUND (no line matched >=3 of: %s)",
            list(SIZE_COLUMNS),
        )
        return []

    bin_tolerance = max(40, int(img_width * _BIN_TOLERANCE_FRAC))
    rows: list[dict[str, Any]] = []
    prev_end = 0
    for h_idx, (header_pos, columns) in enumerate(headers):
        # Per-table metadata: scan the preamble (lines above this header,
        # since the previous table ended) for contract no and Online/Retail.
        preamble = lines[prev_end:header_pos]
        contract_no = _scan_contract_no(preamble) or _scan_contract_no(lines)
        po_type = _scan_po_type(preamble) or _scan_po_type(lines)

        next_pos = headers[h_idx + 1][0] if h_idx + 1 < len(headers) else len(lines)
        data_lines = lines[header_pos + 1 : next_pos]

        if not any(c[0] == "Total" for c in columns):
            inferred = _infer_total_column(data_lines, columns, bin_tolerance)
            if inferred:
                columns = sorted(columns + [inferred], key=lambda c: c[1])

        # Refine each column's anchor using the actual data-row x-positions.
        # Header phrase centers can be off by ~half a column width on
        # narrow 10-column layouts; this catches those drifts before
        # we start binning data.
        columns = _refine_column_centers(data_lines, columns, bin_tolerance)

        log.info(
            "image_table.parse_table[%d] header_line=%d data_lines=%d "
            "columns=%s contract=%s type=%s tol=%d",
            h_idx, header_pos, len(data_lines),
            [c[0] for c in columns], contract_no or "?", po_type or "?", bin_tolerance,
        )

        table_rows = _parse_data_lines(
            data_lines, columns, contract_no, po_type, bin_tolerance
        )
        log.info(
            "image_table.parse_table[%d] rows_extracted=%d", h_idx, len(table_rows)
        )
        rows.extend(table_rows)
        prev_end = next_pos

    log.info("image_table.done total_rows=%d", len(rows))
    return rows


def _mean_brightness(img: "Image.Image") -> float:
    gray = img.convert("L")
    return sum(gray.getdata()) / (gray.width * gray.height)


# --- Image preprocessing -------------------------------------------------


def _maybe_invert_for_dark_mode(img: "Image.Image") -> "Image.Image":
    """Tesseract is trained on dark text / light background. Invert when
    the image is dominantly dark (mean brightness < ~100/255)."""
    gray = img.convert("L")
    pixels = gray.getdata()
    mean = sum(pixels) / (gray.width * gray.height)
    if mean < 100:
        return ImageOps.invert(gray).convert("RGB")
    return img


def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """Upscale small images and boost contrast to give Tesseract more to work with.

    Streamlit screenshots and Outlook inline images are often shrunk to
    ~700–900px wide — Tesseract's accuracy collapses at that scale. A
    2× upscale + autocontrast gets word recall back without changing
    layout coordinates the caller relies on.
    """
    if img.width < 1400:
        scale = 1400 / img.width
        new_size = (int(img.width * scale), int(img.height * scale))
        img = img.resize(new_size, Image.LANCZOS)
    # Convert to grayscale, stretch the histogram, then back to RGB for Tesseract.
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    return gray.convert("RGB")


# --- Word -> line grouping ----------------------------------------------


def _group_words_into_lines(data: dict) -> list[list[dict]]:
    lines_map: dict[tuple, list[dict]] = {}
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = int(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if 0 <= conf < _MIN_CONFIDENCE:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        left = data["left"][i]
        width = data["width"][i]
        top = data["top"][i]
        height = data["height"][i]
        lines_map.setdefault(key, []).append(
            {
                "text": text,
                "left": left,
                "top": top,
                "x_center": left + width / 2,
            }
        )

    for words in lines_map.values():
        words.sort(key=lambda w: w["left"])
    return sorted(
        lines_map.values(),
        key=lambda ws: sum(w["top"] for w in ws) / len(ws),
    )


# --- Header detection ----------------------------------------------------


def _find_all_header_lines(
    lines: list[list[dict]],
) -> list[tuple[int, list[tuple[str, float]]]]:
    """Every line with ≥3 size-shaped header phrases is a candidate header."""
    out: list[tuple[int, list[tuple[str, float]]]] = []
    for idx, line in enumerate(lines):
        cols = _detect_size_columns(line)
        if len(cols) >= 3:
            out.append((idx, cols))
    return out


def _detect_size_columns(line_words: list[dict]) -> list[tuple[str, float]]:
    """Greedy multi-word phrase matching for the canonical SIZE_COLUMNS.

    Returns ``[(canonical_size_name, x_center)]`` sorted left-to-right.

    Runs two passes so that *strict* matches (clean OCR, direct alias
    lookup) always win over *fuzzy* ones. Without this, a 2-word phrase
    like ``"Sib FistSize"`` fuzzy-matches "First Size" and eats both
    cells, losing the 5lb detection from the first word.
    """
    # Filter punctuation-only tokens like "|" that Tesseract emits between cells.
    words = [w for w in line_words if re.search(r"[A-Za-z0-9]", w["text"])]
    n = len(words)
    if n == 0:
        return []
    used = [False] * n
    matches: list[tuple[str, float]] = []

    for matcher in (_canonical_size_ocr_direct, _canonical_size_ocr_fuzzy):
        # Longer phrases first so "Up To 1Mth" beats partial matches on
        # "Up", "To", "1Mth" individually.
        for win in (4, 3, 2, 1):
            for i in range(n - win + 1):
                if any(used[i + k] for k in range(win)):
                    continue
                phrase = " ".join(words[i + k]["text"] for k in range(win))
                canonical = matcher(phrase)
                if canonical and not any(m[0] == canonical for m in matches):
                    x_center = sum(words[i + k]["x_center"] for k in range(win)) / win
                    matches.append((canonical, x_center))
                    for k in range(win):
                        used[i + k] = True

    matches.sort(key=lambda m: m[1])
    return matches


# --- OCR-tolerant size header matcher -----------------------------------


_OCR_ALIAS_INDEX: dict[str, str] | None = None


def _normalize_for_ocr_match(raw: str) -> str:
    """Aggressive normalization for fuzzy size-header matching.

    Strips whitespace (so "Up To 1Mth" and "UpToIMth" both become
    "uptoimth"), normalizes month/year suffix variants, and rewrites
    common Tesseract confusables in numeric contexts: ``I``/``l`` → ``1``
    between digits or before ``mth``/``yr``; leading ``S`` before ``lb``
    or ``ib`` → ``5`` (handles "5lb" misreads as "Sib").
    """
    if not raw:
        return ""
    s = raw.lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^\w\-]", "", s)  # drop punctuation but keep digits + hyphen
    s = s.replace("months", "mths").replace("month", "mth")
    s = s.replace("years", "yrs").replace("year", "yr")
    # Letter I/l surrounded by digits or before a known suffix is almost
    # always a misread "1".
    s = re.sub(r"(?<=\d)[il](?=\d)", "1", s)
    s = re.sub(r"[il](?=mth|yr)", "1", s)
    # OCR misreads leading "5" as "S" (5lb → Sib/Slb).
    s = re.sub(r"^s(?=[il]b|lb|ib)", "5", s)
    # If we end up with "5ib" via I/l→1 rewrite earlier, normalize to "5lb".
    if s.startswith("5") and "ib" in s and "1b" not in s:
        s = s.replace("ib", "lb")
    return s


def _build_ocr_alias_index() -> dict[str, str]:
    global _OCR_ALIAS_INDEX
    if _OCR_ALIAS_INDEX is None:
        idx: dict[str, str] = {}
        for canonical, aliases in _SIZE_ALIASES.items():
            idx[_normalize_for_ocr_match(canonical)] = canonical
            for alias in aliases:
                idx[_normalize_for_ocr_match(alias)] = canonical
        _OCR_ALIAS_INDEX = idx
    return _OCR_ALIAS_INDEX


def _canonical_size_ocr_direct(raw: str) -> str | None:
    """Strict + normalized direct lookup, *no* fuzzy fallback.

    Catches clean OCR ("3-6Mths") and confusable-rewritten phrases
    ("UpToIMth" → "upto1mth"), but never guesses. Used in the first
    pass of :func:`_detect_size_columns` so it can never block a
    correctly-typed shorter cell.
    """
    strict = _canonical_size(raw)
    if strict:
        return strict
    if not raw:
        return None
    normalized = _normalize_for_ocr_match(raw)
    if not normalized or len(normalized) < 3:
        return None
    return _build_ocr_alias_index().get(normalized)


def _canonical_size_ocr_fuzzy(raw: str) -> str | None:
    """Fuzzy alias lookup for OCR errors the direct pass missed.

    Cutoff 0.78 accepts character-swap errors ("FistSize" → "First Size",
    "12-48 Mths" → "12-18 Mths") while rejecting unrelated short keys.
    Only run on words the direct pass didn't claim, so it never causes
    a longer fuzzy match to swallow a perfectly clean adjacent cell.
    """
    if not raw:
        return None
    normalized = _normalize_for_ocr_match(raw)
    if not normalized or len(normalized) < 3:
        return None
    idx = _build_ocr_alias_index()
    if normalized in idx:
        return idx[normalized]
    matches = difflib.get_close_matches(normalized, list(idx.keys()), n=1, cutoff=0.78)
    if matches:
        return idx[matches[0]]
    return None


def _infer_total_column(
    data_lines: list[list[dict]],
    columns: list[tuple[str, float]],
    tolerance: int,
) -> tuple[str, float] | None:
    """Detect an unlabeled Total column to the right of the last size col."""
    if not columns:
        return None
    rightmost_x = columns[-1][1]
    candidates: list[float] = []
    for line in data_lines:
        for w in line:
            if _to_float(w["text"]) is None:
                continue
            if w["x_center"] <= rightmost_x + tolerance:
                continue
            candidates.append(w["x_center"])
    if len(candidates) < 2:
        return None
    candidates.sort()
    median_x = candidates[len(candidates) // 2]
    return ("Total", median_x)


def _refine_column_centers(
    data_lines: list[list[dict]],
    columns: list[tuple[str, float]],
    initial_tolerance: int,
) -> list[tuple[str, float]]:
    """Snap each column's x-center to the median of data values that bin to it.

    Header phrases like ``Up To 1Mth`` span multiple words; their average
    x-center can drift from the column's visual center. Data row values
    are well-aligned within a column, so re-deriving each column's anchor
    from the data's actual distribution catches cases where the header
    average is off by half a column's width — the root cause of silent
    drops in narrow / 10-column tables.

    The refinement pass uses a 2× tolerance for the initial nearest-column
    assignment so values that fell just outside the strict bin still get
    a vote. It only adopts the refined center when ≥ _REFINE_MIN_SUPPORT
    values support it AND the resulting shift stays within the original
    tolerance — preventing one outlier value from dragging a sparse
    column's anchor away from where the header placed it.
    """
    if not columns or not data_lines:
        return columns

    column_xs = [c[1] for c in columns]
    column_names = [c[0] for c in columns]
    refine_tolerance = initial_tolerance * 2
    cluster_xs: list[list[float]] = [[] for _ in columns]
    for line in data_lines:
        for w in line:
            if _to_float(w["text"]) is None:
                continue
            nearest = min(
                range(len(column_xs)),
                key=lambda i: abs(column_xs[i] - w["x_center"]),
            )
            if abs(column_xs[nearest] - w["x_center"]) <= refine_tolerance:
                cluster_xs[nearest].append(w["x_center"])

    refined: list[tuple[str, float]] = []
    for name, old_x, xs in zip(column_names, column_xs, cluster_xs, strict=True):
        if len(xs) >= _REFINE_MIN_SUPPORT:
            xs.sort()
            new_x = xs[len(xs) // 2]
            if abs(new_x - old_x) <= initial_tolerance:
                refined.append((name, new_x))
                continue
        refined.append((name, old_x))
    return refined


def _maybe_recover_decimal(value: float, column_median: float | None) -> float:
    """Try to recover a value where Tesseract dropped the decimal point.

    Targets the specific failure where ``1551.50`` is OCR'd as ``155150``.
    Conditions, all must hold (deliberately conservative — false fixes
    on a Total column would silently corrupt CSV exports):

    * Integer representation is ≥5 digits — guards against fixing
      legitimately-small numbers like ``6100`` (a real PO total).
    * Raw value is >5× the column median (it really does look like
      an outlier).
    * Candidate value (decimal re-inserted 2 places from the right)
      lands within a factor of 3 of the column median — i.e. it's in
      the column's normal distribution.

    The last check is what protects the Total column: a row that has
    Total=6100 in a column of Totals like ``[360, 360, 6100]`` triggers
    the magnitude test (6100 / median 360 = 17×), but the candidate
    ``61`` sits at 0.17× of the median, so the candidate-fit check
    rejects the fix.
    """
    if column_median is None or column_median <= 0 or value <= 0:
        return value
    if value / column_median < _DECIMAL_LOSS_RATIO:
        return value
    text = f"{value:.0f}"
    if len(text) < 5:
        return value
    candidate_str = text[:-2] + "." + text[-2:]
    try:
        candidate = float(candidate_str)
    except ValueError:
        return value
    # Candidate must land within a factor of 3 of the column median.
    fix_ratio = max(candidate, column_median) / max(min(candidate, column_median), 1e-9)
    if fix_ratio <= 3.0:
        return candidate
    return value


# --- Per-table scans -----------------------------------------------------


def _scan_contract_no(lines: list[list[dict]]) -> str:
    flat = " ".join(w["text"] for line in lines for w in line)
    match = _CONTRACT_NO_PATTERN.search(flat.upper())
    return match.group(0) if match else ""


def _scan_po_type(lines: list[list[dict]]) -> str:
    flat = " ".join(w["text"].lower() for line in lines for w in line)
    if re.search(r"\bonline\b", flat):
        return "Online"
    if re.search(r"\bretail\b", flat):
        return "Retail"
    return ""


# --- Data row parsing ----------------------------------------------------


def _parse_data_lines(
    data_lines: list[list[dict]],
    size_columns: list[tuple[str, float]],
    contract_no: str,
    po_type: str,
    tolerance: int,
) -> list[dict[str, Any]]:
    if not size_columns:
        return []
    leftmost_x = size_columns[0][1]
    column_xs = [c[1] for c in size_columns]
    column_names = [c[0] for c in size_columns]

    # First pass: gather per-column value distributions so the second
    # pass can sanity-check individual values against their column's
    # typical magnitude (catches decimal-point losses like 1551.50 ->
    # 155150 that Tesseract sometimes produces).
    column_values: list[list[float]] = [[] for _ in size_columns]
    for line in data_lines:
        for w in line:
            value = _to_float(w["text"])
            if value is None:
                continue
            nearest = min(
                range(len(column_xs)),
                key=lambda i: abs(column_xs[i] - w["x_center"]),
            )
            if abs(column_xs[nearest] - w["x_center"]) > tolerance:
                continue
            column_values[nearest].append(value)
    column_medians: list[float | None] = []
    for vals in column_values:
        if vals:
            sorted_vals = sorted(vals)
            column_medians.append(sorted_vals[len(sorted_vals) // 2])
        else:
            column_medians.append(None)

    rows: list[dict[str, Any]] = []
    for line in data_lines:
        label_words = [w for w in line if w["x_center"] < leftmost_x - tolerance]
        data_words = [w for w in line if w["x_center"] >= leftmost_x - tolerance]

        label = " ".join(w["text"] for w in label_words).strip()
        item_type = _identify_item_type(label) if label else None
        if not item_type:
            if label:
                # Non-empty label that didn't match a known item type — skip.
                continue
            item_type = "Base Qty"

        master_row = _empty_master_row()
        master_row["Type"] = po_type
        master_row["Contract No"] = contract_no
        master_row["Item Category"] = item_type

        any_value = False
        for w in data_words:
            value = _to_float(w["text"])
            if value is None:
                continue
            nearest_idx = min(
                range(len(column_xs)),
                key=lambda i: abs(column_xs[i] - w["x_center"]),
            )
            if abs(column_xs[nearest_idx] - w["x_center"]) > tolerance:
                continue
            value = _maybe_recover_decimal(value, column_medians[nearest_idx])
            master_row[column_names[nearest_idx]] = value
            any_value = True

        if any_value:
            rows.append(master_row)

    return rows


# Re-export so the module's public surface is obvious to readers.
__all__ = ["SIZE_COLUMNS", "parse_tables_from_image_bytes"]
