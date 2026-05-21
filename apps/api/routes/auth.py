"""Outlook OAuth login (authorization-code flow).

The token is held in memory only (see ``apps.api.token_store``) — no
database persistence. It is lost on server restart.
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from apps.api.token_store import save_tokens
from config.settings import settings
from integrations.graph_client import GraphAuthError, GraphClient

router = APIRouter()
log = logging.getLogger("po.auth")


@router.get("/login")
def login(state: str = "outlook_po_reader"):
    client = GraphClient()
    url = client.get_authorization_url(state=state)
    log.info("GET /auth/login -> redirecting to Microsoft sign-in")
    return RedirectResponse(url)


@router.get("/callback")
async def callback(code: str = Query(...), state: str = Query(default="")):
    log.info("GET /auth/callback | code_len=%s state=%s", len(code), state)
    client = GraphClient()
    try:
        tokens = await client.exchange_code_for_tokens(code)
    except GraphAuthError as exc:
        log.error("token exchange FAILED: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    log.info(
        "token exchange OK | has_access=%s has_refresh=%s granted_scope=%r",
        "access_token" in tokens,
        "refresh_token" in tokens,
        tokens.get("scope"),
    )
    save_tokens(tokens)

    # Send the user straight to the Streamlit inbox view.
    return RedirectResponse(settings.frontend_base_url)
