"""Background tasks: sync inbox, classify, extract (separate steps)."""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from domain.pipeline import EmailPipeline
from integrations.auth import decrypt_token
from integrations.graph_client import GraphClient
from storage.db.repositories import get_user, insert_email, mark_user_synced
from storage.db.session import SessionLocal


def _parse_received(value: str | None) -> datetime:
    """Parse Graph's ISO-8601 ``receivedDateTime`` into a naive UTC datetime."""
    if not value:
        return datetime.utcnow()
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _message_to_fields(msg: dict[str, Any]) -> dict[str, Any]:
    """Map a raw Graph message into ``insert_email`` keyword arguments."""
    sender = msg.get("from", {}).get("emailAddress", {})
    return {
        "graph_message_id": msg["id"],
        "subject": msg.get("subject") or "",
        "from_address": sender.get("address", ""),
        "received_at": _parse_received(msg.get("receivedDateTime")),
        "has_attachments": bool(msg.get("hasAttachments", False)),
    }


def sync_inbox(user_id: str, max_messages: int = 100) -> dict:
    """Fetch new messages from Graph and persist raw emails.

    Synchronous entry point for RQ; drives the async Graph client internally.
    """
    return asyncio.run(_sync_inbox(user_id, max_messages))


async def _sync_inbox(user_id: str, max_messages: int) -> dict:
    db = SessionLocal()
    try:
        user = get_user(db, user_id)
        if user is None or not user.graph_refresh_token_encrypted:
            return {"synced": 0, "error": "user not connected to Outlook"}

        client = GraphClient()
        await client.refresh_access_token(decrypt_token(user.graph_refresh_token_encrypted))

        synced = 0
        skip_token: str | None = None
        while synced < max_messages:
            page = await client.list_messages(
                top=min(50, max_messages - synced), skip_token=skip_token
            )
            for msg in page.get("value", []):
                if insert_email(
                    db, email_id=str(uuid.uuid4()), user_id=user_id, **_message_to_fields(msg)
                ):
                    synced += 1
            skip_token = GraphClient.next_skip_token(page)
            if not skip_token:
                break

        mark_user_synced(db, user_id, datetime.utcnow())
        db.commit()
        return {"synced": synced}
    finally:
        db.close()


def classify_email(email_id: str, subject: str, body_text: str) -> dict:
    pipeline = EmailPipeline()
    result = pipeline.classify(email_id, subject, body_text)
    # TODO: save_classification via DB session
    return result.model_dump()


def extract_email(email_id: str, body_text: str, attachment_paths: list[str]) -> dict:
    pipeline = EmailPipeline()
    result = pipeline.extract(email_id, body_text, attachment_paths)
    # TODO: save_extraction via DB session
    return result.model_dump()


def process_email(email_id: str) -> dict:
    """Full pipeline: classify then extract (two distinct steps)."""
    # TODO: load email from DB, download attachments to blob paths
    _ = email_id
    pipeline = EmailPipeline()
    # classification, extraction = pipeline.process_full(...)
    return {"status": "pending"}
