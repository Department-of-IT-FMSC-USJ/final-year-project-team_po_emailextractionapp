"""Classifier page — label emails, train the model, see results."""

import httpx
import streamlit as st

_LABEL_NAMES = {"po": "PO", "not_po": "Not-PO"}
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
    if not resp.is_success:
        st.error(f"Could not save label: {resp.text}")


def _render_inbox_for_labeling(
    client: httpx.Client, labeled: dict[str, str]
) -> None:
    try:
        resp = client.get("/inbox", params={"top": 25})
    except httpx.HTTPError:
        st.warning("Could not reach the API to load emails.")
        return

    if resp.status_code == 401:
        st.info("Not connected to Outlook. Sign in from the **Inbox** page first.")
        return
    if not resp.is_success:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        st.error(f"Could not load emails (HTTP {resp.status_code}): {detail}")
        return

    data = resp.json()
    if not data.get("messages"):
        st.info("Inbox is empty.")
        return

    for m in data["messages"]:
        eid = m.get("id", "")
        current = labeled.get(eid)
        sender = m.get("from_name") or m.get("from") or "(unknown sender)"
        pred = m.get("predicted_label")
        pred_badge = f"{_PRED_BADGE[pred]} · " if pred in _PRED_BADGE else ""
        label_tag = f" 🏷 {_LABEL_NAMES[current]}" if current in _LABEL_NAMES else ""

        with st.expander(f"{pred_badge}{m['subject']} — {sender}{label_tag}"):
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
            if current:
                st.caption(f"Currently labeled as **{_LABEL_NAMES[current]}**. Click to change.")

            b1, b2 = st.columns(2)
            with b1:
                po_label = "✅ PO" + (" ✓" if current == "po" else "")
                if st.button(po_label, key=f"po_{eid}", use_container_width=True):
                    _save_label(client, m, "po")
                    st.rerun()
            with b2:
                np_label = "❌ Not PO" + (" ✓" if current == "not_po" else "")
                if st.button(np_label, key=f"notpo_{eid}", use_container_width=True):
                    _save_label(client, m, "not_po")
                    st.rerun()


def render(client: httpx.Client) -> None:
    st.header("PO Classifier")
    st.caption("Label emails as PO / Not-PO below, then train the model.")

    try:
        status_resp = client.get("/classifier/status")
    except httpx.HTTPError:
        st.warning("Could not reach the API. Is it running on port 8000?")
        return
    if not status_resp.is_success:
        st.error(f"Could not load classifier status: {status_resp.text}")
        return

    status = status_resp.json()
    counts = status.get("labels", {})
    labeled = status.get("labeled_emails", {})
    n_po = counts.get("po", 0)
    n_not_po = counts.get("not_po", 0)

    c1, c2 = st.columns(2)
    c1.metric("PO labels", n_po)
    c2.metric("Non-PO labels", n_not_po)

    ready_to_train = n_po >= 3 and n_not_po >= 3
    if not ready_to_train:
        st.info("You need at least **3 PO** and **3 Non-PO** labels before training.")

    if st.button("🧠 Train model", type="primary", disabled=not ready_to_train):
        with st.spinner("Training the classifier..."):
            tr = client.post("/classifier/train")
        if tr.is_success:
            st.success("Model trained — predictions now show next to each email.")
            st.rerun()
        else:
            try:
                detail = tr.json().get("detail", tr.text)
            except ValueError:
                detail = tr.text
            st.error(detail)

    model = status.get("model")
    if model:
        with st.expander("Current model details"):
            acc = model.get("test_accuracy")
            m1, m2, m3 = st.columns(3)
            if acc is not None:
                m1.metric("Test accuracy", f"{acc * 100:.0f}%")
            m2.metric("Train samples", model.get("n_train", 0))
            m3.metric("Test samples", model.get("n_test", 0))
            st.write(f"**Trained at:** {model.get('trained_at', '')}")
            st.write(
                f"**Total labels:** {model.get('n_samples', 0)} "
                f"({model.get('n_po', 0)} PO / {model.get('n_not_po', 0)} non-PO) "
                f"· **split:** 80/20 stratified "
                f"· **algorithm:** {model.get('algorithm', '')} "
                f"· **train accuracy:** {model.get('train_accuracy', 0) * 100:.0f}%"
            )
    else:
        st.caption("No model trained yet.")

    st.divider()
    st.subheader("Label emails")
    _render_inbox_for_labeling(client, labeled)

    st.divider()
    with st.expander("🗑 Reset training data"):
        st.warning("Deleting labels or the trained model cannot be undone.")
        confirmed = st.checkbox("I understand", key="reset_confirm")
        rcols = st.columns(3)
        with rcols[0]:
            if st.button("Delete labels", disabled=not confirmed, use_container_width=True):
                resp = client.delete("/classifier/labels")
                if resp.is_success:
                    st.success("Labels deleted.")
                    st.rerun()
                else:
                    st.error(resp.text)
        with rcols[1]:
            if st.button("Delete model", disabled=not confirmed, use_container_width=True):
                resp = client.delete("/classifier/model")
                if resp.is_success:
                    st.success("Model deleted.")
                    st.rerun()
                else:
                    st.error(resp.text)
        with rcols[2]:
            if st.button(
                "Delete everything",
                type="primary",
                disabled=not confirmed,
                use_container_width=True,
            ):
                r1 = client.delete("/classifier/labels")
                r2 = client.delete("/classifier/model")
                if r1.is_success and r2.is_success:
                    st.success("Labels and model deleted — starting fresh.")
                    st.rerun()
                else:
                    st.error("Some deletes failed. See API log.")
