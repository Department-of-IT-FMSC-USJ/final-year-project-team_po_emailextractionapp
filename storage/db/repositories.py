"""Data access — keeps classifier/extraction persistence separate.

Repository functions stage changes on the session; the caller commits.
"""

import json
from datetime import datetime

from sqlalchemy.orm import Session

from domain.schemas import ClassificationResult, ExtractionResult
from storage.db.models import ClassificationRecord, Email, ExtractionRecord, User


# --- Users / Outlook connection ---------------------------------------


def get_user(db: Session, user_id: str) -> User | None:
    return db.get(User, user_id)


def upsert_user(db: Session, user_id: str, email: str, refresh_token_encrypted: str) -> User:
    """Create or update a user, refreshing the stored Outlook token.

    ``last_sync_at`` is preserved across re-logins.
    """
    user = db.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            email=email,
            graph_refresh_token_encrypted=refresh_token_encrypted,
        )
        db.add(user)
    else:
        user.email = email
        user.graph_refresh_token_encrypted = refresh_token_encrypted
    return user


def mark_user_synced(db: Session, user_id: str, when: datetime) -> None:
    user = db.get(User, user_id)
    if user is not None:
        user.last_sync_at = when


# --- Emails -----------------------------------------------------------


def email_exists(db: Session, graph_message_id: str) -> bool:
    return (
        db.query(Email.id).filter(Email.graph_message_id == graph_message_id).first() is not None
    )


def insert_email(
    db: Session,
    *,
    email_id: str,
    user_id: str,
    graph_message_id: str,
    subject: str,
    from_address: str,
    received_at: datetime,
    has_attachments: bool,
    body_text: str | None = None,
) -> bool:
    """Insert a synced email; skip (return False) if already stored."""
    if email_exists(db, graph_message_id):
        return False
    db.add(
        Email(
            id=email_id,
            user_id=user_id,
            graph_message_id=graph_message_id,
            subject=subject,
            from_address=from_address,
            received_at=received_at,
            has_attachments=has_attachments,
            body_text=body_text,
            processing_status="pending",
        )
    )
    return True


# --- Classifier / extraction outputs ----------------------------------


def save_classification(db: Session, record_id: str, result: ClassificationResult) -> None:
    row = ClassificationRecord(
        id=record_id,
        email_id=result.email_id,
        predicted_label=result.predicted_label,
        confidence=result.confidence,
        model_version=result.model_version,
        created_at=result.created_at or datetime.utcnow(),
    )
    db.merge(row)


def save_extraction(db: Session, record_id: str, result: ExtractionResult) -> None:
    row = ExtractionRecord(
        id=record_id,
        email_id=result.email_id,
        fields_json=json.dumps(result.fields),
        field_provenance_json=json.dumps(result.field_provenance),
        extractor_version=result.extractor_version,
        created_at=result.created_at or datetime.utcnow(),
    )
    db.merge(row)
