"""Custom CSS polish on top of the dark theme from ``.streamlit/config.toml``.

The base palette (background, text, accent) is set declaratively in
``config.toml`` so Streamlit can apply it before the first paint. Anything
that needs CSS-level control (borders, hover states, sidebar polish,
dataframe surfaces) is layered on here.
"""

import streamlit as st

_ACCENT = "#7C3AED"
_ACCENT_SOFT = "rgba(124, 58, 237, 0.15)"
_BORDER = "rgba(148, 163, 184, 0.15)"
_BORDER_STRONG = "rgba(148, 163, 184, 0.28)"
_SURFACE = "#1E293B"
_SURFACE_HOVER = "#273449"
_MUTED = "#94A3B8"

_CSS = f"""
<style>
  /* ---- Layout breathing room ------------------------------------- */
  .main .block-container {{
    padding-top: 2rem;
    padding-bottom: 4rem;
    max-width: 1280px;
  }}

  /* ---- Headings -------------------------------------------------- */
  h1, h2, h3 {{
    letter-spacing: -0.01em;
    font-weight: 600;
  }}
  h1 {{ font-size: 1.85rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.4rem; }}
  .stCaption, [data-testid="stCaptionContainer"] {{
    color: {_MUTED} !important;
  }}

  /* ---- Sidebar --------------------------------------------------- */
  [data-testid="stSidebar"] {{
    border-right: 1px solid {_BORDER};
    background: linear-gradient(180deg, #111827 0%, #0B1220 100%);
  }}
  [data-testid="stSidebar"] .stRadio > div {{
    gap: 0.25rem;
  }}
  [data-testid="stSidebar"] .stRadio label {{
    padding: 0.55rem 0.75rem;
    border-radius: 8px;
    transition: background 120ms ease;
    cursor: pointer;
  }}
  [data-testid="stSidebar"] .stRadio label:hover {{
    background: {_SURFACE_HOVER};
  }}
  [data-testid="stSidebar"] .stRadio label[data-checked="true"],
  [data-testid="stSidebar"] .stRadio input:checked + div {{
    background: {_ACCENT_SOFT};
  }}

  /* ---- Buttons --------------------------------------------------- */
  .stButton > button,
  .stDownloadButton > button,
  .stLinkButton > a {{
    border-radius: 8px;
    border: 1px solid {_BORDER_STRONG};
    font-weight: 500;
    transition: transform 80ms ease, border-color 120ms ease, background 120ms ease;
  }}
  .stButton > button:hover,
  .stDownloadButton > button:hover,
  .stLinkButton > a:hover {{
    border-color: {_ACCENT};
    transform: translateY(-1px);
  }}
  .stButton > button[kind="primary"],
  .stDownloadButton > button[kind="primary"] {{
    background: {_ACCENT};
    border-color: {_ACCENT};
  }}

  /* ---- Cards / Expanders / Containers ---------------------------- */
  [data-testid="stExpander"] {{
    border: 1px solid {_BORDER} !important;
    border-radius: 10px !important;
    background: {_SURFACE};
    transition: border-color 120ms ease;
  }}
  [data-testid="stExpander"]:hover {{
    border-color: {_BORDER_STRONG} !important;
  }}
  [data-testid="stExpander"] summary {{
    padding: 0.75rem 1rem !important;
    font-weight: 500;
  }}

  /* ---- Metrics --------------------------------------------------- */
  [data-testid="stMetric"] {{
    background: {_SURFACE};
    border: 1px solid {_BORDER};
    border-radius: 10px;
    padding: 0.85rem 1rem;
  }}

  /* ---- Alerts ---------------------------------------------------- */
  [data-testid="stAlert"] {{
    border-radius: 10px;
    border-width: 1px;
  }}

  /* ---- Dataframe surface ----------------------------------------- */
  [data-testid="stDataFrame"] {{
    border: 1px solid {_BORDER};
    border-radius: 10px;
    overflow: hidden;
  }}

  /* ---- Inputs ---------------------------------------------------- */
  .stTextInput input,
  .stSelectbox div[data-baseweb="select"] > div,
  .stNumberInput input {{
    border-radius: 8px !important;
  }}

  /* ---- Dividers -------------------------------------------------- */
  hr {{
    border-color: {_BORDER} !important;
    margin: 1.25rem 0 !important;
  }}

  /* ---- Streamlit chrome ------------------------------------------ */
  header[data-testid="stHeader"] {{
    background: transparent;
  }}
  #MainMenu, footer {{ visibility: hidden; }}
</style>
"""


def inject() -> None:
    """Inject the polish CSS once per page render."""
    st.markdown(_CSS, unsafe_allow_html=True)
