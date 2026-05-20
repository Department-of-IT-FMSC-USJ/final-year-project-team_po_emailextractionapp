from pathlib import Path
from typing import Any


def load_classifier_model(model_path: Path) -> Any:
    """Load trained classifier artifact (joblib, onnx, etc.)."""
    if not model_path.exists():
        return None  # stub until model is deployed
    # TODO: joblib.load(model_path / "model.joblib") or onnxruntime session
    return None
