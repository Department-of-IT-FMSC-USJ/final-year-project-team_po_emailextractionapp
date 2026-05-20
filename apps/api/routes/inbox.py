"""Live Outlook inbox fetch.

Uses the in-memory OAuth token from the login flow to call Microsoft
Graph directly — no database. If the access token has expired it is
refreshed once and the request retried.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from apps.api.token_store import get_tokens, latest_user_id, save_tokens
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
        "preview": msg.get("bodyPreview", ""),
    }


@router.get("")
async def live_inbox(
    user_id: str | None = Query(default=None, description="Defaults to last connected user"),
    top: int = Query(default=25, ge=1, le=100),
):
    """Return the newest inbox messages for a connected Outlook account."""
    uid = user_id or latest_user_id()
    tokens = get_tokens(uid) if uid else None
    log.info("GET /inbox | user_id=%s resolved_uid=%s token_found=%s", user_id, uid, bool(tokens))
    if not tokens:
        log.warning("no token in store -> 401")
        raise HTTPException(
            status_code=401, detail="No Outlook session — sign in at /auth/login first."
        )

    client = GraphClient(access_token=tokens.get("access_token"))
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
            save_tokens(uid, {**tokens, **refreshed})
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
    log.info("inbox fetch OK | %s message(s)", len(messages))
    return {"user_id": uid, "count": len(messages), "messages": messages}
