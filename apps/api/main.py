from fastapi import FastAPI

from apps.api.routes import auth, emails, health, sync

app = FastAPI(title="PO Email Intelligence API", version="0.1.0")

app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(emails.router, prefix="/emails", tags=["emails"])
app.include_router(sync.router, prefix="/sync", tags=["sync"])
