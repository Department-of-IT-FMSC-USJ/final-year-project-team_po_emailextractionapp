import httpx
import streamlit as st


def render(client: httpx.Client) -> None:
    st.header("Inbox")

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Connect Outlook"):
            r = client.get("/auth/login", follow_redirects=False)
            if r.status_code in (302, 307):
                st.link_button("Sign in with Microsoft", r.headers.get("location", "#"))
    with col2:
        if st.button("Sync inbox"):
            resp = client.post("/sync")
            if resp.is_success:
                st.success(f"Sync started: {resp.json()}")
            else:
                st.error(resp.text)
    with col3:
        status_filter = st.selectbox(
            "Status",
            ["all", "pending", "processing", "completed", "failed"],
        )

    resp = client.get("/emails", params={"status": None if status_filter == "all" else status_filter})
    if not resp.is_success:
        st.warning("Could not load emails. Is the API running?")
        return

    emails = resp.json()
    if not emails:
        st.info("No emails yet. Connect Outlook and sync.")
        return

    for em in emails:
        with st.expander(f"{em.get('subject', '(no subject)')} — {em.get('from_address', '')}"):
            st.write(f"Status: **{em.get('processing_status', 'pending')}**")
            email_id = em.get("id")
            if email_id and st.button("Process", key=f"process_{email_id}"):
                proc = client.post(f"/emails/{email_id}/process")
                if proc.is_success:
                    st.json(proc.json())
