from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    graph_refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    graph_message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    subject: Mapped[str] = mapped_column(String(1024), default="")
    from_address: Mapped[str] = mapped_column(String(255), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_status: Mapped[str] = mapped_column(String(32), default="pending")

    attachments: Mapped[list["EmailAttachment"]] = relationship(back_populates="email")
    classification: Mapped["ClassificationRecord | None"] = relationship(
        back_populates="email", uselist=False
    )
    extraction: Mapped["ExtractionRecord | None"] = relationship(
        back_populates="email", uselist=False
    )


class EmailAttachment(Base):
    __tablename__ = "email_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    storage_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64), default="")

    email: Mapped["Email"] = relationship(back_populates="attachments")


class ClassificationRecord(Base):
    """Persisted output from classifier package."""

    __tablename__ = "classifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), unique=True, index=True)
    predicted_label: Mapped[str] = mapped_column(String(128))
    confidence: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    email: Mapped["Email"] = relationship(back_populates="classification")


class ExtractionRecord(Base):
    """Persisted output from extraction package."""

    __tablename__ = "extractions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), unique=True, index=True)
    fields_json: Mapped[str] = mapped_column(Text, default="{}")
    field_provenance_json: Mapped[str] = mapped_column(Text, default="{}")
    extractor_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    email: Mapped["Email"] = relationship(back_populates="extraction")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
