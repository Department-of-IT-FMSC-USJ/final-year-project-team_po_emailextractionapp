"""Inbox page — connect Outlook, view messages, and label them for training."""

import httpx
import streamlit as st

_PRED_BADGE = {"po": "🟢 PO", "not_po": "⚪ Not-PO"}


def _save_label(client: httpx.Client, msg: dict, label: str) -> None:
    resp = client.post(
        "/classifier/labels",
        json={
            "email_id": msg.get("id", ""),
            "subject": msg.get("subject", ""),
            "body_text": msg.get("preview", ""),
            "label": label,
        },
    )
    if resp.is_success:
        st.toast(f"Labeled as {_PRED_BADGE.get(label, label)}")
    else:
        st.error(f"Could not save label: {resp.text}")


def render(client: httpx.Client) -> None:
    st.header("Inbox")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Connect Outlook", use_container_width=True):
            r = client.get("/auth/login", follow_redirects=False)
            if r.status_code in (302, 307):
                st.link_button("Sign in with Microsoft", r.headers.get("location", "#"))
            else:
                st.error("Could not start sign-in. Is the API running?")
    with col2:
        # Any button click reruns the script, which re-fetches the inbox below.
        st.button("Refresh", use_container_width=True)

    try:
        resp = client.get("/inbox", params={"top": 25})
    except httpx.HTTPError:
        st.warning("Could not reach the API. Is it running on port 8000?")
        return

    if not resp.is_success:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        if resp.status_code == 401:
            st.warning(f"Not connected — {detail}")
            st.caption("Click **Connect Outlook**, sign in, then **Refresh**.")
        else:
            st.error(f"Could not load inbox (HTTP {resp.status_code}): {detail}")
        return

    data = resp.json()
    if data.get("classified"):
        st.caption(f"{data['count']} message(s) — 🟢/⚪ shows the model's prediction")
    else:
        st.caption(f"{data['count']} message(s) — no model trained yet (see Classifier page)")

    for m in data["messages"]:
        sender = m.get("from_name") or m.get("from") or "(unknown sender)"
        clip = "📎 " if m.get("has_attachments") else ""
        dot = "" if m.get("is_read", True) else "🔵 "
        pred = m.get("predicted_label")
        badge = f"{_PRED_BADGE[pred]} · " if pred in _PRED_BADGE else ""
        with st.expander(f"{badge}{dot}{clip}{m['subject']} — {sender}"):
            st.write(f"**From:** {m.get('from', '')}")
            st.write(f"**Received:** {m.get('received_at', '')}")
            if pred in _PRED_BADGE:
                st.write(
                    f"**Prediction:** {_PRED_BADGE[pred]} "
                    f"({m.get('confidence', 0) * 100:.0f}% confidence)"
                )
            preview = m.get("preview", "")
            if preview:
                st.write(preview)

            st.divider()
            st.caption("Label this email to train the classifier:")
            b1, b2 = st.columns(2)
            eid = m.get("id", "")
            with b1:
                if st.button("✅ PO", key=f"po_{eid}", use_container_width=True):
                    _save_label(client, m, "po")
            with b2:
                if st.button("❌ Not PO", key=f"notpo_{eid}", use_container_width=True):
                    _save_label(client, m, "not_po")
