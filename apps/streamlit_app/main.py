"""Streamlit frontend — calls API only; no classifier/extraction logic here."""

import httpx
import streamlit as st

from apps.streamlit_app.pages import classifier as classifier_page
from apps.streamlit_app.pages import inbox
from apps.streamlit_app.pages import settings as settings_page
from config.settings import settings

st.set_page_config(page_title="PO Email Intelligence", layout="wide")

API_BASE = settings.api_base_url


def api_client() -> httpx.Client:
    return httpx.Client(base_url=API_BASE, timeout=30.0)


def main() -> None:
    st.sidebar.title("PO Email Intelligence")
    page = st.sidebar.radio("Navigate", ["Inbox", "Classifier", "Settings"])

    with api_client() as client:
        if page == "Inbox":
            inbox.render(client)
        elif page == "Classifier":
            classifier_page.render(client)
        else:
            settings_page.render(client)


if __name__ == "__main__":
    main()
