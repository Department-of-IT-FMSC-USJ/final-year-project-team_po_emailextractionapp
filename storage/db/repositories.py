"""Data access — keeps classifier/extraction persistence separate."""

import json
from datetime import datetime

from sqlalchemy.orm import Session

from domain.schemas import ClassificationResult, ExtractionResult
from storage.db.models import ClassificationRecord, ExtractionRecord


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
