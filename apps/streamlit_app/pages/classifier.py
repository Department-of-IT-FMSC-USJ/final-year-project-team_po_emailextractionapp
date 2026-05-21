"""Classifier page — label stats and PO-model training."""

import httpx
import streamlit as st


def render(client: httpx.Client) -> None:
    st.header("PO Classifier")
    st.caption(
        "Label emails as PO / Not-PO on the **Inbox** page, then train the model here."
    )

    try:
        resp = client.get("/classifier/status")
    except httpx.HTTPError:
        st.warning("Could not reach the API. Is it running on port 8000?")
        return
    if not resp.is_success:
        st.error(f"Could not load classifier status: {resp.text}")
        return

    status = resp.json()
    labels = status.get("labels", {})
    n_po = labels.get("po", 0)
    n_not_po = labels.get("not_po", 0)

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
            st.success("Model trained — predictions now show on the Inbox page.")
        else:
            try:
                detail = tr.json().get("detail", tr.text)
            except ValueError:
                detail = tr.text
            st.error(detail)

    model = status.get("model")
    st.subheader("Current model")
    if not model:
        st.info("No model trained yet.")
        return

    acc = model.get("cv_accuracy")
    m1, m2 = st.columns(2)
    if acc is not None:
        m1.metric("Cross-validated accuracy", f"{acc * 100:.0f}%")
    m2.metric("Training samples", model.get("n_samples", 0))
    st.write(f"**Trained at:** {model.get('trained_at', '')}")
    st.write(
        f"**Samples:** {model.get('n_po', 0)} PO / {model.get('n_not_po', 0)} non-PO "
        f"· **algorithm:** {model.get('algorithm', '')} "
        f"· **train accuracy:** {model.get('train_accuracy', 0) * 100:.0f}%"
    )
