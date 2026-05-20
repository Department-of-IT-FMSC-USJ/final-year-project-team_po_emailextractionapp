from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from integrations.graph_client import GraphClient

router = APIRouter()


@router.get("/login")
def login(state: str = "default"):
    client = GraphClient()
    url = client.get_authorization_url(state=state)
    return RedirectResponse(url)


@router.get("/callback")
async def callback(code: str = Query(...), state: str = Query(default="default")):
    client = GraphClient()
    tokens = await client.exchange_code_for_tokens(code)
    # TODO: persist encrypted refresh token for user
    _ = state, tokens
    return {"message": "Connected to Outlook"}
