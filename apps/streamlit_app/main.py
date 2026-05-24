"""Streamlit frontend — calls API only; no classifier/extraction logic here."""

import httpx
import streamlit as st

from apps.streamlit_app import theme
from apps.streamlit_app.pages import classifier as classifier_page
from apps.streamlit_app.pages import extraction as extraction_page
from apps.streamlit_app.pages import inbox
from apps.streamlit_app.pages import settings as settings_page
from config.settings import settings

st.set_page_config(
    page_title="PO Email Intelligence",
    page_icon="📬",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE = settings.api_base_url

_PAGES = {
    "Inbox": ("📥", inbox.render),
    "Classifier": ("🧠", classifier_page.render),
    "Extraction": ("📤", extraction_page.render),
    "Settings": ("⚙️", settings_page.render),
}


def api_client() -> httpx.Client:
    return httpx.Client(base_url=API_BASE, timeout=30.0)


def _sidebar() -> str:
    with st.sidebar:
        st.markdown(
            "<div style='padding: 0.5rem 0 1rem 0;'>"
            "<div style='font-size: 1.05rem; font-weight: 600; letter-spacing: -0.01em;'>"
            "📬 PO Email Intelligence</div>"
            "<div style='font-size: 0.8rem; color: #94A3B8; margin-top: 2px;'>"
            "Outlook inbox · classifier · extraction</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.divider()
        choice = st.radio(
            "Navigate",
            list(_PAGES.keys()),
            format_func=lambda name: f"{_PAGES[name][0]}  {name}",
            label_visibility="collapsed",
        )
    return choice


def main() -> None:
    theme.inject()
    page = _sidebar()
    render = _PAGES[page][1]
    with api_client() as client:
        render(client)


if __name__ == "__main__":
    main()
