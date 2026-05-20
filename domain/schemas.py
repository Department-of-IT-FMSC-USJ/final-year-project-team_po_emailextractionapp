from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class EmailSummary(BaseModel):
    id: str
    graph_message_id: str
    subject: str
    from_address: str
    received_at: datetime
    has_attachments: bool = False
    processing_status: ProcessingStatus = ProcessingStatus.PENDING


class ClassificationResult(BaseModel):
    """Output from the classifier package only."""

    email_id: str
    predicted_label: str
    confidence: float
    model_version: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ExtractionResult(BaseModel):
    """Output from the extraction package only."""

    email_id: str
    fields: dict[str, Any]
    extractor_version: str
    field_provenance: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EmailDetail(EmailSummary):
    body_text: str | None = None
    classification: ClassificationResult | None = None
    extraction: ExtractionResult | None = None
