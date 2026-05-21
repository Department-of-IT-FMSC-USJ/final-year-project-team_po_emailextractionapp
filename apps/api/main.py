import logging

from fastapi import FastAPI

from apps.api.routes import auth, classifier, emails, health, inbox, sync

# Route debug logs (po.auth, po.inbox) to the console alongside uvicorn's.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="PO Email Intelligence API", version="0.1.0")

app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(inbox.router, prefix="/inbox", tags=["inbox"])
app.include_router(classifier.router, prefix="/classifier", tags=["classifier"])
app.include_router(emails.router, prefix="/emails", tags=["emails"])
app.include_router(sync.router, prefix="/sync", tags=["sync"])
