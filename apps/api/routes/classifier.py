"""PO email classifier — labeling, training, and status (no database)."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from apps.api.token_store import get_tokens, save_tokens
from classifier.dataset import VALID_LABELS, add_label, label_counts, labels_by_email
from classifier.loader import METADATA_FILENAME, MODEL_FILENAME, load_classifier_metadata
from classifier.service import ClassifierService
from classifier.train import NotEnoughData, attach_metadata_fields, train_classifier
from config.settings import settings
from integrations.graph_client import GraphAuthError, GraphClient, GraphError

router = APIRouter()
log = logging.getLogger("po.classifier")

# How many inbox emails to fetch for the post-train sanity check.
_UNSEEN_INBOX_TOP = 50
# Show up to this many lowest-confidence unseen emails in the metadata
# so the UI can highlight what to label next.
_UNSEEN_LOWEST_N = 5


class LabelIn(BaseModel):
    email_id: str
    subject: str = ""
    body_text: str = ""
    label: str  # "po" or "not_po"
    # ISO-8601 timestamp from Graph (`receivedDateTime`). Optional so old
    # callers keep working; used by training to hold out the newest emails.
    received_at: str | None = None
    # Sender address from Graph (`from.emailAddress.address`). Optional;
    # used by training as the *group* key for group-based CV so emails
    # from the same supplier never straddle a train/validation fold.
    from_addr: str | None = None


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
    add_label(
        payload.email_id,
        payload.subject,
        payload.body_text,
        payload.label,
        received_at=payload.received_at,
        from_addr=payload.from_addr,
    )
    counts = label_counts()
    log.info("label saved: %s -> %s | counts=%s", payload.email_id, payload.label, counts)
    return {"status": "saved", "counts": counts}


@router.get("/status")
def classifier_status():
    """Report label counts and the trained model's metadata (if any)."""
    metadata = load_classifier_metadata(settings.classifier_model_path)
    return {
        "trained": metadata is not None,
        "labels": label_counts(),
        "labeled_emails": labels_by_email(),
        "model": metadata,
    }


@router.post("/train")
async def train():
    """Train the classifier from the stored labels.

    After training, runs a *best-effort* sanity check on the live inbox:
    predicts every email that isn't already in the label set and
    attaches a summary (counts, PO split, confidence histogram,
    lowest-confidence candidates to label) to the metadata. Skipped
    silently when no Outlook session is available or Graph errors.
    """
    try:
        metadata = train_classifier()
    except NotEnoughData as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    unseen_check = await _unseen_inbox_check()
    if unseen_check is not None:
        metadata = attach_metadata_fields({"unseen_inbox_check": unseen_check})

    log.info("classifier trained: %s", metadata)
    return {"status": "trained", "model": metadata}


async def _unseen_inbox_check() -> dict[str, Any] | None:
    """Predict the trained model on inbox emails NOT yet in labels.jsonl.

    Returns a JSON-serializable summary, or ``None`` when the check
    cannot run (no Outlook session, Graph error, no trained model).
    """
    tokens = get_tokens()
    if not tokens:
        log.info("unseen-inbox check skipped: no Outlook session")
        return None

    page: dict[str, Any] | None = None
    async with GraphClient(access_token=tokens.get("access_token")) as client:
        try:
            page = await client.list_messages(top=_UNSEEN_INBOX_TOP)
        except GraphAuthError:
            # One refresh attempt — matches the pattern used by /inbox.
            refresh_token = tokens.get("refresh_token")
            if not refresh_token:
                log.info("unseen-inbox check skipped: token expired, no refresh")
                return None
            try:
                refreshed = await client.refresh_access_token(refresh_token)
                save_tokens({**tokens, **refreshed})
                page = await client.list_messages(top=_UNSEEN_INBOX_TOP)
            except (GraphAuthError, GraphError) as exc:
                log.warning("unseen-inbox check skipped: Graph refresh failed: %s", exc)
                return None
        except GraphError as exc:
            log.warning("unseen-inbox check skipped: Graph error: %s", exc)
            return None

    raw_messages = list((page or {}).get("value", []))
    if not raw_messages:
        return {
            "inbox_size": 0,
            "n_unseen": 0,
            "skipped_reason": "inbox is empty",
        }

    labeled_ids = set(labels_by_email().keys())
    unseen_messages = [m for m in raw_messages if m.get("id") not in labeled_ids]
    if not unseen_messages:
        return {
            "inbox_size": len(raw_messages),
            "n_unseen": 0,
            "skipped_reason": "every fetched inbox email is already labeled",
        }

    # Build a fresh service so it picks up the model we just wrote.
    service = ClassifierService()
    if not service.is_ready:
        log.warning("unseen-inbox check skipped: no model after train (unexpected)")
        return None

    predictions: list[dict[str, Any]] = []
    for msg in unseen_messages:
        eid = msg.get("id", "") or ""
        subject = msg.get("subject") or "(no subject)"
        preview = msg.get("bodyPreview", "") or ""
        result = service.predict(eid, subject, preview)
        sender = msg.get("from", {}).get("emailAddress", {})
        predictions.append({
            "email_id": eid,
            "subject": subject,
            "from": sender.get("address", ""),
            "received_at": msg.get("receivedDateTime"),
            "predicted_label": result.predicted_label,
            "confidence": round(float(result.confidence), 4),
        })

    confidences = [p["confidence"] for p in predictions]
    n_po = sum(1 for p in predictions if p["predicted_label"] == "po")
    n_not_po = len(predictions) - n_po

    # 5-bucket histogram across the binary-confidence range [0.5, 1.0].
    buckets: list[tuple[str, float, float]] = [
        ("50-60%", 0.5, 0.6),
        ("60-70%", 0.6, 0.7),
        ("70-80%", 0.7, 0.8),
        ("80-90%", 0.8, 0.9),
        ("90-100%", 0.9, 1.000001),  # upper-inclusive
    ]
    histogram = {
        bucket: sum(1 for c in confidences if lo <= c < hi)
        for bucket, lo, hi in buckets
    }

    # Lowest-confidence emails first — those are the highest-value labels
    # to add for the next training round (they sit closest to the
    # decision boundary).
    lowest = sorted(predictions, key=lambda p: p["confidence"])[:_UNSEEN_LOWEST_N]

    return {
        "inbox_size": len(raw_messages),
        "n_unseen": len(unseen_messages),
        "n_predicted_po": n_po,
        "n_predicted_not_po": n_not_po,
        "mean_confidence": float(sum(confidences) / len(confidences)),
        "min_confidence": float(min(confidences)),
        "max_confidence": float(max(confidences)),
        "confidence_histogram": histogram,
        "lowest_confidence": lowest,
    }


@router.delete("/labels")
def delete_labels():
    """Delete every stored label (the model file is left untouched)."""
    path = settings.classifier_labels_path
    if path.exists():
        path.unlink()
    log.info("all labels deleted")
    return {"status": "deleted", "labels": label_counts()}


@router.delete("/model")
def delete_model():
    """Delete the trained model artifact (labels are kept)."""
    removed = []
    for filename in (MODEL_FILENAME, METADATA_FILENAME):
        path = settings.classifier_model_path / filename
        if path.exists():
            path.unlink()
            removed.append(filename)
    log.info("model files deleted: %s", removed)
    return {"status": "deleted", "removed": removed}


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
