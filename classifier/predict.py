"""Inference for the PO classifier."""

from typing import Any

from classifier.features import build_feature_text

UNCLASSIFIED = "unclassified"


def predict_label(model: Any, subject: str, body_text: str) -> tuple[str, float]:
    """Run inference on email text.

    Returns ``(label, confidence)`` where label is ``"po"``, ``"not_po"``,
    or ``"unclassified"`` when no model is loaded.
    """
    if model is None:
        return UNCLASSIFIED, 0.0
    text = build_feature_text(subject, body_text)
    probabilities = model.predict_proba([text])[0]
    classes = list(model.classes_)
    best_idx = int(probabilities.argmax())
    return str(classes[best_idx]), float(probabilities[best_idx])
