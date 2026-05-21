"""PO email classifier — labeling, training, and status (no database)."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from classifier.dataset import VALID_LABELS, add_label, label_counts
from classifier.loader import load_classifier_metadata
from classifier.service import ClassifierService
from classifier.train import NotEnoughData, train_classifier
from config.settings import settings

router = APIRouter()
log = logging.getLogger("po.classifier")


class LabelIn(BaseModel):
    email_id: str
    subject: str = ""
    body_text: str = ""
    label: str  # "po" or "not_po"


class PredictIn(BaseModel):
    subject: str = ""
    body_text: str = ""


@router.post("/labels")
def add_training_label(payload: LabelIn):
    """Save a PO / non-PO label for one email (overwrites a prior label)."""
    if payload.label not in VALID_LABELS:
        raise HTTPException(
            status_code=422, detail=f"label must be one of {sorted(VALID_LABELS)}"
        )
    add_label(payload.email_id, payload.subject, payload.body_text, payload.label)
    counts = label_counts()
    log.info("label saved: %s -> %s | counts=%s", payload.email_id, payload.label, counts)
    return {"status": "saved", "counts": counts}


@router.get("/status")
def classifier_status():
    """Report label counts and the trained model's metadata (if any)."""
    metadata = load_classifier_metadata(settings.classifier_model_path)
    return {"trained": metadata is not None, "labels": label_counts(), "model": metadata}


@router.post("/train")
def train():
    """Train the classifier from the stored labels."""
    try:
        metadata = train_classifier()
    except NotEnoughData as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info("classifier trained: %s", metadata)
    return {"status": "trained", "model": metadata}


@router.post("/predict")
def predict(payload: PredictIn):
    """Classify a single email's subject + body with the trained model."""
    service = ClassifierService()
    if not service.is_ready:
        raise HTTPException(
            status_code=409, detail="No trained model — add labels and train first."
        )
    result = service.predict("", payload.subject, payload.body_text)
    return result.model_dump()
