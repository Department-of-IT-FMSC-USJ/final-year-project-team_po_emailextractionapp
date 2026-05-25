"""Microsoft Graph API — OAuth login and mail fetch.

OAuth uses the authorization-code flow directly against the Microsoft
identity platform token endpoint (no MSAL), mirroring the approach
proven in the previous project. All HTTP is async via httpx.
"""

import base64
import binascii
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from config.settings import settings

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
AUTHORITY_BASE = "https://login.microsoftonline.com"

log = logging.getLogger("po.graph")


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
        self._scopes = settings.graph_scopes.split()
        # Optional shared httpx client — set via ``async with GraphClient(...)``
        # to amortize the TCP+TLS handshake across many calls.
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GraphClient":
        # Connection pool tuned for the inbox fan-out (full-body fetches).
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        self._http = httpx.AsyncClient(
            base_url=GRAPH_API_BASE, timeout=30.0, limits=limits
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --- OAuth / login -------------------------------------------------

    @property
    def _authority(self) -> str:
        return f"{AUTHORITY_BASE}/{self._tenant_id}"

    def get_authorization_url(self, state: str) -> str:
        """Build the Microsoft sign-in URL to redirect the user to."""
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "response_mode": "query",
            "scope": " ".join(self._scopes),
            "state": state,
            # Always show the account chooser — never silently reuse a
            # cached session (which sends the request to the wrong tenant).
            "prompt": "select_account",
        }
        return f"{self._authority}/oauth2/v2.0/authorize?{urlencode(params)}"

    async def exchange_code_for_tokens(self, code: str) -> dict[str, Any]:
        """Exchange an OAuth authorization code for access + refresh tokens."""
        return await self._post_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(self._scopes),
            },
            context="authorization code exchange",
        )

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Mint a fresh access token from a stored refresh token."""
        return await self._post_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(self._scopes),
            },
            context="token refresh",
        )

    async def _post_token(self, data: dict[str, str], context: str) -> dict[str, Any]:
        endpoint = f"{self._authority}/oauth2/v2.0/token"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(endpoint, data=data)
        try:
            result = resp.json()
        except ValueError as exc:
            raise GraphAuthError(
                f"{context} failed: HTTP {resp.status_code} {resp.text[:200]}"
            ) from exc
        if "access_token" not in result:
            detail = result.get("error_description") or result.get("error") or "unknown error"
            raise GraphAuthError(f"{context} failed: {detail}")
        self._access_token = result["access_token"]
        return result

    # --- Mail fetch ----------------------------------------------------

    async def list_messages(self, top: int = 50, skip_token: str | None = None) -> dict[str, Any]:
        """Fetch one page of inbox messages, newest first.

        Pass ``skip_token`` from :meth:`next_skip_token` to page forward.
        Returns the raw Graph response (``value`` plus ``@odata.nextLink``).
        """
        params: dict[str, str] = {
            "$top": str(top),
            "$orderby": "receivedDateTime DESC",
            "$select": "id,subject,from,receivedDateTime,hasAttachments,isRead,bodyPreview",
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
        log.debug("Graph GET %s", path)
        headers = {"Authorization": f"Bearer {self._access_token}"}
        if self._http is not None:
            resp = await self._http.get(path, params=params, headers=headers)
        else:
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
        """Return metadata (id, name, contentType, size) for each attachment."""
        params = {"$select": "id,name,size,contentType"}
        result = await self._graph_get(f"/me/messages/{message_id}/attachments", params)
        return result.get("value", [])

    async def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download one attachment's raw bytes (base64-decoded from Graph).

        ``contentBytes`` lives on the derived ``microsoft.graph.fileAttachment``
        type, so it can't be named in ``$select`` on the base attachment
        endpoint (Graph returns HTTP 400). Fetch the full payload instead —
        the field is included automatically for file attachments.
        """
        data = await self._graph_get(
            f"/me/messages/{message_id}/attachments/{attachment_id}",
            params={},
        )
        content_b64 = data.get("contentBytes")
        if not content_b64:
            return b""
        return base64.b64decode(content_b64)
