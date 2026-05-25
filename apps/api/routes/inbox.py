"""Live Outlook inbox fetch.

Uses the in-memory OAuth token from the login flow to call Microsoft
Graph directly — no database. If the access token has expired it is
refreshed once and the request retried.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from apps.api.token_store import get_tokens, save_tokens
from classifier.service import ClassifierService
from extraction.image_tables import parse_tables_from_image_bytes
from extraction.ocr import IMAGE_MIME_TYPES
from extraction.service import ExtractionService
from extraction.tables import html_to_text, parse_tables
from integrations.graph_client import GraphAuthError, GraphClient, GraphError

router = APIRouter()
log = logging.getLogger("po.inbox")


def _summarize(msg: dict[str, Any]) -> dict[str, Any]:
    """Flatten a raw Graph message into the shape the frontend renders."""
    sender = msg.get("from", {}).get("emailAddress", {})
    return {
        "id": msg.get("id"),
        "subject": msg.get("subject") or "(no subject)",
        "from": sender.get("address", ""),
        "from_name": sender.get("name", ""),
        "received_at": msg.get("receivedDateTime"),
        "has_attachments": bool(msg.get("hasAttachments")),
        "is_read": bool(msg.get("isRead", True)),
        "preview": msg.get("bodyPreview", ""),
    }


@router.get("")
async def live_inbox(
    top: int = Query(default=25, ge=1, le=100),
    include_tables: bool = Query(
        default=False,
        description="When true, fetch the full HTML body for PO-predicted emails "
        "and parse PO tables (slower; used by the Extraction page).",
    ),
):
    """Return the newest inbox messages for the connected Outlook account."""
    tokens = get_tokens()
    log.info("GET /inbox | token_found=%s include_tables=%s", bool(tokens), include_tables)
    if not tokens:
        raise HTTPException(
            status_code=401, detail="No Outlook session — sign in at /auth/login first."
        )

    async with GraphClient(access_token=tokens.get("access_token")) as client:
        try:
            log.info("calling Graph list_messages(top=%s)", top)
            page = await client.list_messages(top=top)
        except GraphAuthError as first_err:
            log.warning("first Graph call rejected token: %s", first_err)
            refresh_token = tokens.get("refresh_token")
            if not refresh_token:
                raise HTTPException(
                    status_code=401, detail="Session expired — sign in again."
                ) from None
            try:
                log.info("refreshing access token and retrying...")
                refreshed = await client.refresh_access_token(refresh_token)
                save_tokens({**tokens, **refreshed})
                page = await client.list_messages(top=top)
            except GraphAuthError as exc:
                log.error("Graph still rejected token after refresh: %s", exc)
                raise HTTPException(
                    status_code=401, detail=f"Outlook rejected the token: {exc}"
                ) from exc
            except GraphError as exc:
                log.error("Graph error after refresh: %s", exc)
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        except GraphError as exc:
            log.error("Graph error on first call: %s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        messages = [_summarize(m) for m in page.get("value", [])]

        # Attach PO predictions when a trained classifier is available.
        classifier = ClassifierService()
        if classifier.is_ready:
            for msg in messages:
                prediction = classifier.predict(
                    msg["id"] or "", msg["subject"], msg["preview"]
                )
                msg["predicted_label"] = prediction.predicted_label
                msg["confidence"] = round(prediction.confidence, 3)

        # Body extraction + table parsing run only for PO-predicted emails.
        # Skipped entirely when no classifier has been trained.
        if classifier.is_ready:
            extractor = ExtractionService()
            po_messages = [m for m in messages if m.get("predicted_label") == "po"]

            # Fan out full-body fetches concurrently when the Extraction page
            # asks for them. Sequential per-email fetches were the dominant
            # cost; gather collapses N round-trips into ~1 round-trip wall time.
            bodies: list[dict[str, Any] | None]
            if include_tables and po_messages:
                bodies = await _gather_bodies(client, [m["id"] for m in po_messages])
            else:
                bodies = [None] * len(po_messages)

            # Concurrently OCR image attachments for every PO email so any
            # tables embedded as PNG/JPG land in the same MASTER schema as
            # body tables. Skipped when include_tables is False (Inbox page).
            attachment_rows: list[list[dict[str, Any]]]
            if include_tables and po_messages:
                attachment_rows = await _gather_attachment_table_rows(
                    client, po_messages
                )
            else:
                attachment_rows = [[] for _ in po_messages]

            for msg, full, att_rows in zip(
                po_messages, bodies, attachment_rows, strict=True
            ):
                body_text = f"{msg['subject']}\n{msg['preview']}"
                rows: list[dict[str, Any]] = []
                if full is not None:
                    body = full.get("body") or {}
                    content = body.get("content", "")
                    if body.get("contentType", "").lower() == "html":
                        body_text = f"{msg['subject']}\n{html_to_text(content)}"
                        rows = parse_tables(content)
                    else:
                        body_text = f"{msg['subject']}\n{content}"

                # Body + image-attachment rows share the MASTER schema, so
                # we just concatenate. The Streamlit view already groups by
                # Contract No, which keeps the per-contract sections clean.
                combined = rows + att_rows
                if combined:
                    msg["table_rows"] = combined

                result = extractor.extract(
                    msg["id"] or "", body_text, attachment_paths=[]
                )
                if result.fields:
                    msg["extracted_fields"] = result.fields
                    msg["field_provenance"] = result.field_provenance

    log.info(
        "inbox fetch OK | %s message(s) | classified=%s | tables=%s",
        len(messages), classifier.is_ready, include_tables,
    )
    return {
        "count": len(messages),
        "messages": messages,
        "classified": classifier.is_ready,
    }


async def _gather_bodies(
    client: GraphClient, message_ids: list[str]
) -> list[dict[str, Any] | None]:
    """Concurrently fetch full message bodies; ``None`` for any that failed."""
    results = await asyncio.gather(
        *(client.get_message(mid) for mid in message_ids),
        return_exceptions=True,
    )
    bodies: list[dict[str, Any] | None] = []
    for mid, res in zip(message_ids, results, strict=True):
        if isinstance(res, (GraphAuthError, GraphError)):
            log.warning("full-body fetch failed for %s: %s", mid, res)
            bodies.append(None)
        elif isinstance(res, BaseException):
            log.warning("full-body fetch raised for %s: %r", mid, res)
            bodies.append(None)
        else:
            bodies.append(res)
    return bodies


async def _gather_attachment_table_rows(
    client: GraphClient, messages: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    """For each message, OCR every image attachment into MASTER-schema rows.

    Per-message tasks run concurrently across messages. Inside each
    message, attachment downloads also run concurrently. Tesseract is
    blocking, so each OCR call is dispatched to a thread via
    :func:`asyncio.to_thread` to keep the event loop responsive.
    """
    log.info("attach.fanout.start po_emails=%d", len(messages))
    tasks = [_attachment_rows_for_message(client, m) for m in messages]
    results = await asyncio.gather(*tasks)
    total = sum(len(r) for r in results)
    with_rows = sum(1 for r in results if r)
    log.info(
        "attach.fanout.done po_emails=%d emails_with_rows=%d total_rows=%d",
        len(messages), with_rows, total,
    )
    return results


async def _attachment_rows_for_message(
    client: GraphClient, msg: dict[str, Any]
) -> list[dict[str, Any]]:
    message_id = msg["id"]
    subject = (msg.get("subject") or "")[:80]
    if not msg.get("has_attachments"):
        log.info("attach.skip msg=%s subject=%r reason=NO_ATTACHMENTS_FLAG", message_id, subject)
        return []

    try:
        attachments = await client.list_attachments(message_id)
    except (GraphAuthError, GraphError) as exc:
        log.warning("attach.list_failed msg=%s subject=%r error=%s", message_id, subject, exc)
        return []

    log.info(
        "attach.listed msg=%s subject=%r total=%d names=%s",
        message_id, subject, len(attachments),
        [a.get("name", "?") for a in attachments],
    )

    images = [
        a
        for a in attachments
        if str(a.get("contentType", "")).lower() in IMAGE_MIME_TYPES
    ]
    if not images:
        # Show what content types were seen so the user can extend IMAGE_MIME_TYPES
        # if Outlook sent something unexpected (e.g. application/octet-stream).
        ct_seen = sorted({str(a.get("contentType", "?")).lower() for a in attachments})
        log.info(
            "attach.skip msg=%s reason=NO_IMAGES content_types_seen=%s "
            "(image_table OCR only runs on: %s)",
            message_id, ct_seen, sorted(IMAGE_MIME_TYPES),
        )
        return []

    log.info(
        "attach.images msg=%s count=%d names=%s",
        message_id, len(images), [a.get("name", "?") for a in images],
    )

    downloads = await asyncio.gather(
        *(client.download_attachment(message_id, a["id"]) for a in images),
        return_exceptions=True,
    )

    rows: list[dict[str, Any]] = []
    for att, blob in zip(images, downloads, strict=True):
        name = att.get("name", "?")
        if isinstance(blob, BaseException):
            log.warning("attach.download_failed msg=%s att=%s error=%r", message_id, name, blob)
            continue
        if not blob:
            log.warning("attach.download_empty msg=%s att=%s", message_id, name)
            continue

        log.info("attach.parse_start msg=%s att=%s bytes=%d", message_id, name, len(blob))
        try:
            parsed = await asyncio.to_thread(parse_tables_from_image_bytes, blob)
        except Exception as exc:  # noqa: BLE001 — Tesseract may raise anything
            log.warning("attach.parse_failed msg=%s att=%s error=%r", message_id, name, exc)
            continue

        log.info(
            "attach.parse_done msg=%s att=%s rows_extracted=%d",
            message_id, name, len(parsed),
        )
        rows.extend(parsed)
    return rows
