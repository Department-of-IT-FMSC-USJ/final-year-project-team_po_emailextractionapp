from classifier.loader import load_classifier_model
from classifier.predict import predict_label
from config.settings import settings
from domain.schemas import ClassificationResult


class ClassifierService:
    """Standalone classification — no extraction logic."""

    def __init__(self) -> None:
        self._model = load_classifier_model(settings.classifier_model_path)
        self._version = settings.classifier_model_version

    def predict(self, email_id: str, subject: str, body_text: str) -> ClassificationResult:
        label, confidence = predict_label(self._model, subject=subject, body_text=body_text)
        return ClassificationResult(
            email_id=email_id,
            predicted_label=label,
            confidence=confidence,
            model_version=self._version,
        )
