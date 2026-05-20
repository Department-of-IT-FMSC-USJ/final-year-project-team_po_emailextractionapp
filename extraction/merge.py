from typing import Any


def merge_field_results(
    body_fields: dict[str, Any],
    body_prov: dict[str, str],
    attach_fields: dict[str, Any],
    attach_prov: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Prefer attachment OCR for conflicts when both sources have a field."""
    merged = {**body_fields}
    provenance = {**body_prov}
    for key, value in attach_fields.items():
        if key not in merged or merged[key] in (None, ""):
            merged[key] = value
            provenance[key] = attach_prov.get(key, "attachment")
        elif key in attach_fields:
            merged[key] = value
            provenance[key] = attach_prov.get(key, "attachment")
    return merged, provenance
