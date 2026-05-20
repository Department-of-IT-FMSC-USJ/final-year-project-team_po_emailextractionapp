"""Background tasks: sync inbox, classify, extract (separate steps)."""

from domain.pipeline import EmailPipeline


def sync_inbox(user_id: str) -> dict:
    """Fetch new messages from Graph and persist raw emails."""
    # TODO: GraphClient + DB upsert
    _ = user_id
    return {"synced": 0}


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
