from classifier.loader import load_classifier_metadata, load_classifier_model
from classifier.predict import predict_label
from config.settings import settings
from domain.schemas import ClassificationResult


class ClassifierService:
    """Standalone classification — no extraction logic.

    Loads the trained model once per instance; construct a fresh instance
    to pick up a newly trained model.
    """

    def __init__(self) -> None:
        self._model = load_classifier_model(settings.classifier_model_path)
        self._metadata = load_classifier_metadata(settings.classifier_model_path)
        self._version = (
            self._metadata.get("model_version", settings.classifier_model_version)
            if self._metadata
            else settings.classifier_model_version
        )

    @property
    def is_ready(self) -> bool:
        """True once a model has been trained and can be used for inference."""
        return self._model is not None

    def predict(self, email_id: str, subject: str, body_text: str) -> ClassificationResult:
        label, confidence = predict_label(self._model, subject=subject, body_text=body_text)
        return ClassificationResult(
            email_id=email_id,
            predicted_label=label,
            confidence=confidence,
            model_version=self._version,
        )
