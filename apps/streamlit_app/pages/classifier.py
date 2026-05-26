"""Classifier page — label emails, train the model, see results."""

import httpx
import streamlit as st

_LABEL_NAMES = {"po": "PO", "not_po": "Not-PO"}
_PRED_BADGE = {"po": "🟢 PO", "not_po": "⚪ Not-PO"}

_METRIC_HELP = {
    "Accuracy": "Overall correctness — fraction of all predictions that match the true label.",
    "Precision": "Of emails the model called PO, how many actually were PO.",
    "Recall": "Of emails that actually are PO, how many the model caught.",
    "F1-score": "Harmonic mean of precision and recall — penalizes lopsided trade-offs.",
}

_TIME_BASIS_LABEL = {
    "received_at": "`received_at` (when each email arrived)",
    "labeled_at": "`labeled_at` (when you clicked label — `received_at` missing on these records)",
    "mixed": "mixed sort key (`received_at` where available, `labeled_at` otherwise)",
}

_CV_STRATEGY_LABEL = {
    "stratified_kfold": "stratified k-fold (random shuffle)",
    "stratified_group_kfold_by_sender": "stratified **group** k-fold by sender (same-sender emails kept in one fold)",
}


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _render_per_class(per_class: dict) -> None:
    rows = []
    for label_key, label_display in _LABEL_NAMES.items():
        stats = per_class.get(label_key) or {}
        rows.append({
            "Class": label_display,
            "Precision": _pct(stats.get("precision")),
            "Recall": _pct(stats.get("recall")),
            "F1-score": _pct(stats.get("f1")),
            "Support": stats.get("support", 0),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_confusion(cm: list, cm_labels: list) -> None:
    header = ["True ↓ / Predicted →"] + [
        f"Pred: {_LABEL_NAMES.get(lbl, lbl)}" for lbl in cm_labels
    ]
    body = []
    for i, lbl in enumerate(cm_labels):
        body.append(
            {header[0]: f"True: {_LABEL_NAMES.get(lbl, lbl)}"}
            | {header[j + 1]: int(cm[i][j]) for j in range(len(cm_labels))}
        )
    st.dataframe(body, use_container_width=True, hide_index=True)


def _render_misclassified(rows: list[dict]) -> None:
    if not rows:
        st.success("No misclassifications on the held-out test set.")
        return
    table = []
    for row in rows:
        kind = row.get("kind", "")
        kind_label = {
            "false_positive": "🔴 FP (Non-PO → PO)",
            "false_negative": "🟠 FN (PO → Non-PO)",
        }.get(kind, kind)
        table.append({
            "Kind": kind_label,
            "True": _LABEL_NAMES.get(row.get("true_label", ""), row.get("true_label", "")),
            "Predicted": _LABEL_NAMES.get(row.get("predicted_label", ""), row.get("predicted_label", "")),
            "Confidence": _pct(row.get("confidence")),
            "Subject": row.get("subject", ""),
            "Sender": row.get("from_addr") or "—",
            "Received": row.get("received_at") or "—",
        })
    st.dataframe(table, use_container_width=True, hide_index=True)


def _render_unseen_inbox(check: dict) -> None:
    if not check:
        return
    skipped = check.get("skipped_reason")
    if skipped:
        st.caption(f"Unseen-inbox check skipped — {skipped}.")
        return

    n_unseen = check.get("n_unseen", 0)
    if not n_unseen:
        return

    n_po = check.get("n_predicted_po", 0)
    n_not_po = check.get("n_predicted_not_po", 0)
    mean_c = check.get("mean_confidence")
    min_c = check.get("min_confidence")

    u1, u2, u3, u4 = st.columns(4)
    u1.metric("Unseen emails", n_unseen)
    u2.metric("Predicted PO", n_po)
    u3.metric("Mean confidence", _pct(mean_c))
    u4.metric("Min confidence", _pct(min_c))
    st.caption(
        f"Live sanity check — the trained model predicted **{n_unseen} inbox email(s)** "
        f"that aren't in `labels.jsonl` (out of {check.get('inbox_size', n_unseen)} fetched). "
        f"No ground truth here, so no accuracy number — but the confidence "
        f"distribution tells you whether the model is confident or wobbly on fresh emails."
    )

    histogram = check.get("confidence_histogram") or {}
    if histogram:
        hist_rows = [{"Confidence": k, "Predictions": v} for k, v in histogram.items()]
        st.caption("Confidence histogram (bucketed):")
        st.dataframe(hist_rows, use_container_width=True, hide_index=True)

    lowest = check.get("lowest_confidence") or []
    if lowest:
        st.caption(
            "**Lowest-confidence unseen emails** — these sit closest to the "
            "decision boundary. Labeling them yields the most signal for the next training round."
        )
        st.dataframe([
            {
                "Predicted": _LABEL_NAMES.get(r.get("predicted_label", ""), r.get("predicted_label", "")),
                "Confidence": _pct(r.get("confidence")),
                "Subject": r.get("subject", ""),
                "From": r.get("from", ""),
                "Received": r.get("received_at", ""),
            }
            for r in lowest
        ], use_container_width=True, hide_index=True)


def _render_model_details(model: dict) -> None:
    """Test-set metrics (headline) + CV stability + misclassified + hygiene."""
    test = model.get("test") or {}
    headline_acc = test.get("accuracy", model.get("test_accuracy"))
    headline_p = test.get("macro_precision", model.get("macro_precision"))
    headline_r = test.get("macro_recall", model.get("macro_recall"))
    headline_f1 = test.get("macro_f1", model.get("macro_f1"))

    # --- Headline: test-set metrics ---
    st.markdown("##### Held-out test set (newest emails, never seen during training)")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Test accuracy", _pct(headline_acc), help=_METRIC_HELP["Accuracy"])
    m2.metric("Precision (macro)", _pct(headline_p), help=_METRIC_HELP["Precision"])
    m3.metric("Recall (macro)", _pct(headline_r), help=_METRIC_HELP["Recall"])
    m4.metric("F1-score (macro)", _pct(headline_f1), help=_METRIC_HELP["F1-score"])

    n_test = test.get("n", model.get("n_test_holdout", 0))
    n_train_pool = model.get("n_train_pool", 0)
    basis = model.get("time_basis", "labeled_at")
    st.caption(
        f"Trained on the **oldest {n_train_pool} labels**, scored on the "
        f"**newest {n_test}** (time-based split by {_TIME_BASIS_LABEL.get(basis, basis)}). "
        f"This split is **never** used during fitting or cross-validation."
    )

    per_class = test.get("per_class") or model.get("per_class") or {}
    if per_class:
        st.caption("Per-class precision / recall / F1 on the test set:")
        _render_per_class(per_class)

    cm = test.get("confusion_matrix") or model.get("confusion_matrix")
    cm_labels = (
        test.get("confusion_matrix_labels")
        or model.get("confusion_matrix_labels")
        or list(_LABEL_NAMES.keys())
    )
    if cm:
        st.caption(
            "Confusion matrix — rows are the **true** label, columns are the "
            "model's **prediction**. Off-diagonal = false positives / false negatives."
        )
        _render_confusion(cm, cm_labels)

    # --- Misclassified examples (FP / FN list) ---
    n_miss = model.get("n_misclassified_test")
    if n_miss is not None:
        st.markdown(f"##### Misclassified test emails ({n_miss})")
        _render_misclassified(model.get("misclassified_test") or [])

    # --- CV stability (secondary, inside the train pool only) ---
    cv_folds = model.get("cv_folds")
    if cv_folds:
        st.markdown("##### Cross-validation on the train pool (stability check)")
        cv_strategy = model.get("cv_strategy", "stratified_kfold")
        cv_strategy_label = _CV_STRATEGY_LABEL.get(cv_strategy, cv_strategy)
        acc_mean = model.get("cv_accuracy_mean")
        acc_std = model.get("cv_accuracy_std") or 0.0
        f1_mean = model.get("cv_f1_macro_mean")
        f1_std = model.get("cv_f1_macro_std") or 0.0
        st.caption(
            f"**{cv_folds}-fold {cv_strategy_label}**, run only on the {n_train_pool} "
            f"training labels (the test set is *not* in any fold). "
            f"Accuracy {_pct(acc_mean)} ± {_pct(acc_std)} · "
            f"macro F1 {_pct(f1_mean)} ± {_pct(f1_std)}. "
            f"Used for model-selection signal — **the headline accuracy above is the test number**."
        )
        per_fold = model.get("cv_accuracy_per_fold") or []
        if per_fold:
            st.caption(
                "Per-fold accuracy: "
                + ", ".join(f"{i + 1}: {_pct(s)}" for i, s in enumerate(per_fold))
            )
        if cv_strategy != "stratified_group_kfold_by_sender":
            distinct = model.get("cv_distinct_groups", 0)
            coverage = model.get("cv_group_coverage", 0)
            st.caption(
                f"💡 Group-based CV requires sender info — currently {coverage} "
                f"train-pool labels carry `from_addr` ({distinct} distinct senders). "
                f"Relabel emails through the UI to upgrade — group-based CV catches "
                f"same-supplier leakage that random folds can't."
            )

    # --- Unseen-inbox sanity check ---
    unseen = model.get("unseen_inbox_check")
    if unseen:
        st.markdown("##### Unseen-inbox sanity check (live, no ground truth)")
        _render_unseen_inbox(unseen)

    # --- Hard-example hint ---
    if headline_acc is not None and headline_acc >= 0.99 and (n_test or 0) <= 20:
        st.info(
            "🎯 The test set is small **and** the model scores ≥99%. That's not "
            "necessarily wrong — but it usually means the dataset is too clean. "
            "Add **hard examples** to make this number trustworthy:\n"
            "- Non-PO emails that contain words like *PO*, *order*, *invoice*, "
            "*quotation*, *delivery* (e.g. marketing newsletters).\n"
            "- PO emails that **don't** say \"purchase order\" outright (informal "
            "supplier orders, internal forwards, short confirmations)."
        )

    # --- Dataset hygiene ---
    st.markdown("##### Dataset hygiene")
    raw = model.get("n_labels_raw", model.get("n_samples", 0))
    n_samples = model.get("n_samples", 0)
    n_empty = model.get("n_empty_dropped", 0)
    n_dup = model.get("n_duplicates_dropped", 0)
    n_recv = model.get("n_with_received_at", 0)
    n_from = model.get("n_with_from_addr", 0)
    st.caption(
        f"**Pipeline:** {raw} raw → dropped {n_empty} empty → dropped {n_dup} duplicate "
        f"→ **{n_samples} unique** "
        f"({model.get('n_po', 0)} PO / {model.get('n_not_po', 0)} Not-PO)."
    )
    st.caption(
        f"**Coverage:** {n_recv}/{n_samples} carry `received_at`, "
        f"{n_from}/{n_samples} carry `from_addr`. Relabel emails to upgrade — "
        f"more coverage means the time split and group-based CV use real metadata "
        f"instead of fallbacks."
    )

    # --- Summary line ---
    st.divider()
    st.write(f"**Trained at:** {model.get('trained_at', '')}")
    st.write(
        f"**Algorithm:** {model.get('algorithm', '')} · "
        f"**train pool:** {n_train_pool} · "
        f"**test holdout:** {n_test} · "
        f"**train-pool fit accuracy:** {_pct(model.get('train_accuracy'))} "
        f"(in-sample sanity — separate from the test number above)"
    )

    if not per_class and not cm:
        st.info(
            "Per-class precision/recall/F1 and the confusion matrix are only "
            "available for models trained after the train/val/test rebuild. "
            "Click **Train model** to recompute them."
        )


def _save_label(client: httpx.Client, msg: dict, label: str) -> None:
    resp = client.post(
        "/classifier/labels",
        json={
            "email_id": msg.get("id", ""),
            "subject": msg.get("subject", ""),
            "body_text": msg.get("preview", ""),
            "label": label,
            # Email's actual receive time from Graph — lets training's
            # time-based holdout use the *newest emails*, not the
            # most-recently-clicked rows.
            "received_at": msg.get("received_at"),
            # Sender address — used as the group key in CV so emails
            # from the same supplier never straddle a train/val fold.
            "from_addr": msg.get("from"),
        },
    )
    if not resp.is_success:
        st.error(f"Could not save label: {resp.text}")


def _render_inbox_for_labeling(
    client: httpx.Client, labeled: dict[str, str]
) -> None:
    try:
        resp = client.get("/inbox", params={"top": 100})
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
            _render_model_details(model)
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
