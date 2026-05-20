"""Inbox page — connect Outlook and view live messages via the API."""

import httpx
import streamlit as st


def render(client: httpx.Client) -> None:
    st.header("Inbox")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Connect Outlook", use_container_width=True):
            r = client.get("/auth/login", follow_redirects=False)
            if r.status_code in (302, 307):
                st.link_button(
                    "Sign in with Microsoft", r.headers.get("location", "#")
                )
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
    st.caption(f"{data['count']} message(s) — account {data['user_id']}")

    for m in data["messages"]:
        sender = m.get("from_name") or m.get("from") or "(unknown sender)"
        clip = "📎 " if m.get("has_attachments") else ""
        with st.expander(f"{clip}{m['subject']} — {sender}"):
            st.write(f"**From:** {m.get('from', '')}")
            st.write(f"**Received:** {m.get('received_at', '')}")
            preview = m.get("preview", "")
            if preview:
                st.write(preview)
