import httpx
import streamlit as st

from config.settings import settings


def render(client: httpx.Client) -> None:
    _ = client
    st.header("Settings")
    st.subheader("Classifier")
    st.text(f"Model path: {settings.classifier_model_path}")
    st.text(f"Model version: {settings.classifier_model_version}")

    st.subheader("Extraction")
    st.text(f"Rules path: {settings.extraction_rules_path}")
    st.text(f"Extractor version: {settings.extractor_version}")
    st.checkbox("OCR enabled", value=settings.ocr_enabled, disabled=True)

    st.subheader("API")
    st.text(f"API base URL: {settings.api_base_url}")
