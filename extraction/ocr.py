"""OCR for image attachments using Tesseract.

The Tesseract binary must be installed separately:
  Windows installer: https://github.com/UB-Mannheim/tesseract/wiki

The pytesseract Python package is a thin wrapper around the binary. If
the binary is not on PATH, set ``TESSERACT_CMD`` in .env.
"""

import io
import logging
from pathlib import Path
from typing import Any

from extraction.body import extract_from_body

log = logging.getLogger("po.ocr")

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"})
IMAGE_MIME_TYPES = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/gif",
    "image/webp", "image/bmp", "image/tiff",
})

try:
    import pytesseract
    from PIL import Image

    _OCR_LIBS_AVAILABLE = True
except ImportError:
    _OCR_LIBS_AVAILABLE = False
    log.warning("pytesseract / Pillow not installed — image OCR is disabled.")


class OcrError(RuntimeError):
    """Raised when OCR can't run (missing binary, bad image, etc.)."""


# --- Tesseract binary discovery ---------------------------------------

_BINARY_CONFIGURED = False


def _configure_tesseract() -> None:
    """Point pytesseract at a usable tesseract binary.

    Order: explicit ``settings.tesseract_cmd`` > old project's
    ``tessaret/`` bundle > standard Windows install paths > rely on PATH.
    """
    global _BINARY_CONFIGURED
    if _BINARY_CONFIGURED or not _OCR_LIBS_AVAILABLE:
        return

    from config.settings import settings

    candidates: list[str] = []
    if settings.tesseract_cmd:
        candidates.append(settings.tesseract_cmd)

    project_root = Path(__file__).resolve().parent.parent
    candidates.append(str(project_root / "tessaret" / "tesseract.exe"))
    candidates.append(str(project_root / "tesseract" / "tesseract.exe"))
    candidates.append(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    candidates.append(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            log.info("Tesseract found at %s", candidate)
            _BINARY_CONFIGURED = True
            return
    # Fall through — pytesseract will use PATH default.
    _BINARY_CONFIGURED = True


# --- Public API -------------------------------------------------------


def ocr_image_bytes(image_bytes: bytes) -> str:
    """Run OCR on raw image bytes; return the extracted text."""
    if not _OCR_LIBS_AVAILABLE:
        raise OcrError("OCR libraries missing — pip install pytesseract Pillow")
    _configure_tesseract()
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return pytesseract.image_to_string(img)
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError(
            "Tesseract binary not found. Install it from "
            "https://github.com/UB-Mannheim/tesseract/wiki and either add it "
            "to PATH or set TESSERACT_CMD in .env."
        ) from exc
    except Exception as exc:
        raise OcrError(f"OCR failed: {exc}") from exc


def extract_from_image_bytes(image_bytes: bytes) -> tuple[dict[str, Any], dict[str, str], str]:
    """OCR an image and run body regex on the result.

    Returns ``(fields, provenance, raw_text)`` so callers can show the
    extracted fields next to a preview of the OCR'd text.
    """
    text = ocr_image_bytes(image_bytes)
    fields, _ = extract_from_body(text)
    provenance = {key: "attachment:ocr" for key in fields}
    return fields, provenance, text


def extract_from_attachments(
    attachment_paths: list[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Path-based API used by :class:`ExtractionService` for stored files."""
    fields: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for path in attachment_paths:
        p = Path(path)
        if p.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            f, prov, _ = extract_from_image_bytes(p.read_bytes())
        except OcrError as exc:
            log.warning("OCR skipped for %s: %s", p, exc)
            continue
        for key, value in f.items():
            if key not in fields or not fields[key]:
                fields[key] = value
                provenance[key] = prov.get(key, "attachment:ocr")
    return fields, provenance
