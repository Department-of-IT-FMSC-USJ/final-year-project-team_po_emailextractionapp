"""Outlook OAuth login.

Tokens are held in memory only (see ``apps.api.token_store``) — no
database persistence. They are lost on server restart.
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
def login(state: str = "default"):
    client = GraphClient()
    url = client.get_authorization_url(state=state)
    log.info("GET /auth/login -> redirecting to Microsoft sign-in")
    return RedirectResponse(url)


@router.get("/callback")
async def callback(code: str = Query(...), state: str = Query(default="default")):
    log.info("GET /auth/callback | code_len=%s state=%s", len(code), state)
    client = GraphClient()
    try:
        tokens = await client.exchange_code_for_tokens(code)
    except GraphAuthError as exc:
        log.error("token exchange FAILED: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    claims = tokens.get("id_token_claims", {})
    user_id = claims.get("oid")
    user_email = claims.get("preferred_username", "")
    log.info(
        "token exchange OK | token_keys=%s | has_access=%s has_refresh=%s | "
        "granted_scope=%r | user_id=%s email=%s",
        sorted(tokens),
        "access_token" in tokens,
        "refresh_token" in tokens,
        tokens.get("scope"),
        user_id,
        user_email,
    )
    if not user_id:
        log.error("no 'oid' claim in id_token; claim_keys=%s", sorted(claims))
        raise HTTPException(
            status_code=400, detail="Outlook sign-in did not return a user identity"
        )

    save_tokens(user_id, tokens)
    log.info("token stored for %s; redirecting to %s", user_id, settings.frontend_base_url)

    _ = state, user_email
    return RedirectResponse(settings.frontend_base_url)
