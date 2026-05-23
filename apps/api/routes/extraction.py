"""Attachment-based PO extraction — OCR on image attachments."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from apps.api.token_store import get_tokens, save_tokens
from extraction.ocr import IMAGE_MIME_TYPES, OcrError, extract_from_image_bytes
from integrations.graph_client import GraphAuthError, GraphClient, GraphError

router = APIRouter()
log = logging.getLogger("po.extraction")


@router.post("/email/{message_id}/attachments")
async def extract_attachments(message_id: str):
    """Download image attachments for the email, OCR them, return fields.

    Returns ``{message_id, attachments: [...], fields, provenance}`` where
    ``attachments`` is a per-file breakdown (name, fields, text snippet, or
    error) and the top-level ``fields`` is the merged best-guess.
    """
    tokens = get_tokens()
    if not tokens:
        raise HTTPException(
            status_code=401, detail="No Outlook session — sign in at /auth/login first."
        )

    client = GraphClient(access_token=tokens.get("access_token"))

    try:
        attachments = await client.list_attachments(message_id)
    except GraphAuthError as exc:
        # Try to refresh once and retry.
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        refreshed = await client.refresh_access_token(refresh)
        save_tokens({**tokens, **refreshed})
        attachments = await client.list_attachments(message_id)
    except GraphError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    images = [a for a in attachments if str(a.get("contentType", "")).lower() in IMAGE_MIME_TYPES]
    log.info(
        "OCR request msg=%s | %d total attachments, %d images",
        message_id, len(attachments), len(images),
    )

    per_attachment: list[dict[str, Any]] = []
    merged_fields: dict[str, Any] = {}
    merged_prov: dict[str, str] = {}

    for att in images:
        name = att.get("name", "attachment")
        try:
            data = await client.download_attachment(message_id, att["id"])
            fields, prov, text = extract_from_image_bytes(data)
        except OcrError as exc:
            per_attachment.append({"name": name, "error": str(exc), "fields": {}})
            continue
        except (GraphAuthError, GraphError) as exc:
            per_attachment.append({"name": name, "error": str(exc), "fields": {}})
            continue

        per_attachment.append(
            {"name": name, "fields": fields, "text_snippet": text[:400]}
        )
        for key, value in fields.items():
            if key not in merged_fields or not merged_fields[key]:
                merged_fields[key] = value
                merged_prov[key] = prov.get(key, "attachment:ocr")

    return {
        "message_id": message_id,
        "image_count": len(images),
        "total_count": len(attachments),
        "attachments": per_attachment,
        "fields": merged_fields,
        "provenance": merged_prov,
    }
