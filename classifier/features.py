"""Text feature preparation for the classifier (independent of extraction)."""


def build_feature_text(subject: str, body_text: str, max_chars: int = 8000) -> str:
    combined = f"{subject.strip()}\n{body_text.strip()}"
    return combined[:max_chars]
