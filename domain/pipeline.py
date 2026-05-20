"""Orchestrates classifier and extraction as separate steps — never merged."""

from classifier.service import ClassifierService
from domain.schemas import ClassificationResult, ExtractionResult
from extraction.service import ExtractionService


class EmailPipeline:
    def __init__(
        self,
        classifier: ClassifierService | None = None,
        extractor: ExtractionService | None = None,
    ) -> None:
        self._classifier = classifier or ClassifierService()
        self._extractor = extractor or ExtractionService()

    def classify(self, email_id: str, subject: str, body_text: str) -> ClassificationResult:
        return self._classifier.predict(email_id=email_id, subject=subject, body_text=body_text)

    def extract(
        self,
        email_id: str,
        body_text: str,
        attachment_paths: list[str] | None = None,
    ) -> ExtractionResult:
        return self._extractor.extract(
            email_id=email_id,
            body_text=body_text,
            attachment_paths=attachment_paths or [],
        )

    def process_full(
        self,
        email_id: str,
        subject: str,
        body_text: str,
        attachment_paths: list[str] | None = None,
    ) -> tuple[ClassificationResult, ExtractionResult]:
        classification = self.classify(email_id, subject, body_text)
        extraction = self.extract(email_id, body_text, attachment_paths)
        return classification, extraction
