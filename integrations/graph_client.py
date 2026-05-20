"""Microsoft Graph API — OAuth and mail fetch."""

from typing import Any

from config.settings import settings


class GraphClient:
    def __init__(self, access_token: str | None = None) -> None:
        self._access_token = access_token
        self._tenant_id = settings.azure_tenant_id
        self._client_id = settings.azure_client_id
        self._scopes = settings.graph_scopes.split()

    def get_authorization_url(self, state: str) -> str:
        # TODO: msal ConfidentialClientApplication / PublicClientApplication
        _ = state
        return f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/authorize"

    async def exchange_code_for_tokens(self, code: str) -> dict[str, Any]:
        _ = code
        return {"access_token": "", "refresh_token": ""}

    async def list_messages(self, top: int = 50, skip_token: str | None = None) -> dict[str, Any]:
        _ = top, skip_token
        return {"value": []}

    async def get_message(self, message_id: str) -> dict[str, Any]:
        _ = message_id
        return {}

    async def list_attachments(self, message_id: str) -> list[dict[str, Any]]:
        _ = message_id
        return []

    async def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        _ = message_id, attachment_id
        return b""
