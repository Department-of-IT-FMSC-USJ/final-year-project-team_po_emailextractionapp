from config.settings import settings
from domain.schemas import ExtractionResult
from extraction.body import extract_from_body
from extraction.merge import merge_field_results
from extraction.ocr import extract_from_attachments


class ExtractionService:
    """Standalone extraction — no classification logic."""

    def __init__(self) -> None:
        self._version = settings.extractor_version
        self._ocr_enabled = settings.ocr_enabled
        self._rules_path = settings.extraction_rules_path

    def extract(
        self,
        email_id: str,
        body_text: str,
        attachment_paths: list[str],
    ) -> ExtractionResult:
        body_fields, body_prov = extract_from_body(body_text, rules_path=self._rules_path)
        attach_fields, attach_prov = (
            extract_from_attachments(attachment_paths)
            if self._ocr_enabled and attachment_paths
            else ({}, {})
        )
        fields, provenance = merge_field_results(
            body_fields,
            body_prov,
            attach_fields,
            attach_prov,
        )
        return ExtractionResult(
            email_id=email_id,
            fields=fields,
            extractor_version=self._version,
            field_provenance=provenance,
        )
