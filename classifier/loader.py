"""Load the trained classifier artifact (TF-IDF + LogisticRegression)."""

import json
from pathlib import Path
from typing import Any

import joblib

MODEL_FILENAME = "model.joblib"
METADATA_FILENAME = "metadata.json"


def load_classifier_model(model_path: Path) -> Any:
    """Load the trained pipeline, or None if no model has been trained yet."""
    model_file = model_path / MODEL_FILENAME
    if not model_file.exists():
        return None
    return joblib.load(model_file)


def load_classifier_metadata(model_path: Path) -> dict[str, Any] | None:
    """Load the trained model's metadata (accuracy, sample counts, ...)."""
    meta_file = model_path / METADATA_FILENAME
    if not meta_file.exists():
        return None
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
