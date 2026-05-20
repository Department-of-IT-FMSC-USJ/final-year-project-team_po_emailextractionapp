"""Microsoft Graph API — OAuth login and mail fetch.

OAuth (MSAL) calls are synchronous; they run in a thread so they do not
block the FastAPI event loop. Graph REST calls use httpx.AsyncClient.
"""

import asyncio
import base64
import binascii
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import msal

from config.settings import settings

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
AUTHORITY_BASE = "https://login.microsoftonline.com"

log = logging.getLogger("po.graph")

# MSAL manages these itself; passing them as scopes raises ValueError.
_RESERVED_SCOPES = {"openid", "profile", "offline_access"}


class GraphAuthError(RuntimeError):
    """Raised when an OAuth token is missing, expired, or rejected (HTTP 401)."""


class GraphError(RuntimeError):
    """Raised when a Graph request fails for a non-auth reason (4xx/5xx)."""


class GraphClient:
    """Thin wrapper over Azure AD OAuth and the Graph mail endpoints.

    Instances are cheap and request-scoped: construct with an access token
    to call mail endpoints, or without one to drive the login flow.
    """

    def __init__(self, access_token: str | None = None) -> None:
        self._access_token = access_token
        self._tenant_id = settings.azure_tenant_id or "common"
        self._client_id = settings.azure_client_id
        self._client_secret = settings.azure_client_secret
        self._redirect_uri = settings.graph_redirect_uri
        self._scopes = [s for s in settings.graph_scopes.split() if s.lower() not in _RESERVED_SCOPES]
        self._msal_app: msal.ConfidentialClientApplication | None = None

    # --- OAuth / login -------------------------------------------------

    def _app(self) -> msal.ConfidentialClientApplication:
        if self._msal_app is None:
            self._msal_app = msal.ConfidentialClientApplication(
                client_id=self._client_id,
                client_credential=self._client_secret,
                authority=f"{AUTHORITY_BASE}/{self._tenant_id}",
            )
        return self._msal_app

    def get_authorization_url(self, state: str) -> str:
        """Build the Microsoft sign-in URL to redirect the user to."""
        return self._app().get_authorization_request_url(
            scopes=self._scopes,
            state=state,
            redirect_uri=self._redirect_uri,
        )

    async def exchange_code_for_tokens(self, code: str) -> dict[str, Any]:
        """Exchange an OAuth authorization code for access + refresh tokens.

        Returns the raw MSAL result (``access_token``, ``refresh_token``,
        ``expires_in``, ...). The access token is also cached on this client.
        """
        result = await asyncio.to_thread(
            self._app().acquire_token_by_authorization_code,
            code,
            scopes=self._scopes,
            redirect_uri=self._redirect_uri,
        )
        self._access_token = self._token_or_raise(result, "Authorization code exchange failed")
        return result

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Mint a fresh access token from a stored (decrypted) refresh token."""
        result = await asyncio.to_thread(
            self._app().acquire_token_by_refresh_token,
            refresh_token,
            scopes=self._scopes,
        )
        self._access_token = self._token_or_raise(result, "Refresh token exchange failed")
        return result

    @staticmethod
    def _token_or_raise(result: dict[str, Any], context: str) -> str:
        token = result.get("access_token")
        if not token:
            detail = result.get("error_description") or result.get("error") or "unknown error"
            raise GraphAuthError(f"{context}: {detail}")
        return token

    # --- Mail fetch ----------------------------------------------------

    async def list_messages(self, top: int = 50, skip_token: str | None = None) -> dict[str, Any]:
        """Fetch one page of inbox messages, newest first.

        Pass ``skip_token`` from :meth:`next_skip_token` to page forward.
        Returns the raw Graph response (``value`` plus ``@odata.nextLink``).
        """
        params: dict[str, str] = {
            "$top": str(top),
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,receivedDateTime,hasAttachments,bodyPreview",
        }
        if skip_token:
            params["$skiptoken"] = skip_token
        return await self._graph_get("/me/mailFolders/inbox/messages", params)

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Fetch a single message including its full body."""
        params = {"$select": "id,subject,from,receivedDateTime,hasAttachments,body"}
        return await self._graph_get(f"/me/messages/{message_id}", params)

    @staticmethod
    def next_skip_token(response: dict[str, Any]) -> str | None:
        """Extract the paging token from a list response, or None when done."""
        next_link = response.get("@odata.nextLink")
        if not next_link:
            return None
        tokens = parse_qs(urlparse(next_link).query).get("$skiptoken")
        return tokens[0] if tokens else None

    @staticmethod
    def _decode_token_claims(token: str) -> dict[str, Any]:
        """Decode a JWT payload WITHOUT verifying it — diagnostics only.

        Returns ``{}`` for opaque (non-JWT) tokens, e.g. those issued for
        personal Microsoft accounts.
        """
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload))
        except (ValueError, IndexError, binascii.Error, json.JSONDecodeError):
            return {}

    async def _graph_get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self._access_token:
            raise GraphAuthError("GraphClient has no access token; sign in first.")
        claims = self._decode_token_claims(self._access_token)
        log.info(
            "Graph GET %s | token: jwt=%s aud=%s tid=%s scp=%r idtyp=%s upn=%s",
            path,
            bool(claims),
            claims.get("aud"),
            claims.get("tid"),
            claims.get("scp"),
            claims.get("idtyp"),
            claims.get("upn") or claims.get("unique_name") or claims.get("email"),
        )
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with httpx.AsyncClient(base_url=GRAPH_API_BASE, timeout=30.0) as client:
            resp = await client.get(path, params=params, headers=headers)
        if resp.status_code == 401:
            www_auth = resp.headers.get("www-authenticate", "")
            log.warning("Graph 401 on %s | www-authenticate=%s", path, www_auth)
            reason = resp.text or www_auth or (
                "no mailbox for the signed-in account — it is likely a guest "
                "user or a personal account without an Outlook/Exchange mailbox"
            )
            raise GraphAuthError(f"Graph rejected the access token (401): {reason}")
        if resp.status_code >= 400:
            raise GraphError(f"Graph request failed ({resp.status_code}): {resp.text}")
        return resp.json()

    # --- Attachments (handled by the extraction step) ------------------

    async def list_attachments(self, message_id: str) -> list[dict[str, Any]]:
        # TODO: implement with the extraction pipeline.
        _ = message_id
        return []

    async def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        # TODO: implement with the extraction pipeline.
        _ = message_id, attachment_id
        return b""
