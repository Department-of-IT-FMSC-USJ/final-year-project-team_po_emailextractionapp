from typing import Any


def predict_label(model: Any, subject: str, body_text: str) -> tuple[str, float]:
    """Run inference on email text features."""
    if model is None:
        return "unclassified", 0.0
    text = f"{subject}\n{body_text}"
    # TODO: vectorize + model.predict_proba
    _ = text
    return "unclassified", 0.0
