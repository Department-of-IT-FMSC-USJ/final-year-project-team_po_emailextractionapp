"""Extraction page — structured fields pulled from emails classified as PO.

The classifier must be trained for anything to appear here, because we
only extract from emails it flags as PO.
"""

import httpx
import streamlit as st


def _flatten(value) -> str:
    if isinstance(value, list):
        return ", ".join(value)
    return "—" if value in (None, "", []) else str(value)


def _render_ocr_panel(client: httpx.Client, msg: dict) -> None:
    """Per-email button to OCR image attachments. Results cached in session."""
    msg_id = msg.get("id", "")
    cache_key = f"ocr_{msg_id}"

    st.divider()
    if st.button("🖼 OCR image attachments", key=f"ocr_btn_{msg_id}"):
        with st.spinner("Downloading attachments and running Tesseract OCR..."):
            try:
                resp = client.post(f"/extraction/email/{msg_id}/attachments")
            except httpx.HTTPError as exc:
                st.session_state[cache_key] = {"error": str(exc)}
            else:
                if resp.is_success:
                    st.session_state[cache_key] = resp.json()
                else:
                    try:
                        detail = resp.json().get("detail", resp.text)
                    except ValueError:
                        detail = resp.text
                    st.session_state[cache_key] = {"error": detail}

    result = st.session_state.get(cache_key)
    if not result:
        return
    if "error" in result:
        st.error(result["error"])
        return
    if result.get("image_count", 0) == 0:
        st.info(
            f"{result.get('total_count', 0)} attachment(s), none are images. "
            "PDF extraction is not implemented yet."
        )
        return

    merged = result.get("fields") or {}
    if merged:
        st.success("📋 Fields from attachment OCR:")
        for key, value in merged.items():
            st.write(f"**{key.replace('_', ' ').title()}:** {_flatten(value)}")
    else:
        st.info("OCR ran but no PO fields were detected in the images.")

    for att in result.get("attachments", []):
        with st.expander(f"📎 {att.get('name', 'attachment')}"):
            if "error" in att:
                st.error(att["error"])
                continue
            if att.get("fields"):
                for key, value in att["fields"].items():
                    st.write(f"**{key.replace('_', ' ').title()}:** {_flatten(value)}")
            snippet = att.get("text_snippet", "")
            if snippet:
                st.caption("OCR text (first 400 chars):")
                st.code(snippet)


def render(client: httpx.Client) -> None:
    st.header("PO Extraction")
    st.caption("Structured fields extracted from emails classified as PO.")

    st.button("Refresh", use_container_width=False)

    try:
        with st.spinner("Fetching emails and parsing PO tables..."):
            resp = client.get("/inbox", params={"top": 50, "include_tables": True}, timeout=60.0)
    except httpx.HTTPError:
        st.warning("Could not reach the API. Is it running on port 8000?")
        return

    if resp.status_code == 401:
        st.info("Not connected to Outlook. Sign in from the **Inbox** page first.")
        return
    if not resp.is_success:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        st.error(f"Could not load inbox (HTTP {resp.status_code}): {detail}")
        return

    data = resp.json()
    if not data.get("classified"):
        st.warning(
            "Classifier is not trained yet — train it on the **Classifier** page first. "
            "Extraction only runs on emails predicted as PO."
        )
        return

    po_emails = [m for m in data["messages"] if m.get("predicted_label") == "po"]
    if not po_emails:
        st.info("No PO emails detected in the latest inbox fetch.")
        return

    st.success(f"{len(po_emails)} PO email(s) detected · {data['count']} scanned")

    # Top-level table view of extracted fields per PO email.
    rows = []
    for m in po_emails:
        fields = m.get("extracted_fields") or {}
        rows.append(
            {
                "From": m.get("from_name") or m.get("from", ""),
                "Subject": m.get("subject", ""),
                "PO Number": _flatten(fields.get("po_number")),
                "Supplier": _flatten(fields.get("supplier")),
                "Date": _flatten(fields.get("date")),
                "Amount": _flatten(fields.get("amount")),
                "Items": _flatten(fields.get("item_codes")),
                "Attachments": "📎" if m.get("has_attachments") else "",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Per-email details")
    for m in po_emails:
        sender = m.get("from_name") or m.get("from") or "(unknown sender)"
        with st.expander(f"{m.get('subject', '')} — {sender}"):
            st.write(f"**From:** {m.get('from', '')}")
            st.write(f"**Received:** {m.get('received_at', '')}")
            st.write(f"**Prediction confidence:** {m.get('confidence', 0) * 100:.0f}%")
            preview = m.get("preview", "")
            if preview:
                st.write(preview)

            fields = m.get("extracted_fields") or {}
            st.divider()
            st.caption("📋 Body extraction")
            if fields:
                for key, value in fields.items():
                    st.write(f"**{key.replace('_', ' ').title()}:** {_flatten(value)}")
            else:
                st.info("No fields detected in the email body.")

            table_rows = m.get("table_rows") or []
            if table_rows:
                st.divider()
                _render_table_rows(table_rows)

            if m.get("has_attachments"):
                _render_ocr_panel(client, m)


_SIZE_COLUMN_ORDER = (
    "5lb", "First Size", "Up To 1Mth", "Up To 3Mth",
    "3-6 Mths", "6-9 Mths", "9-12 Mths", "12-18 Mths",
    "1.5-2 Yrs", "Total",
)


def _render_table_rows(rows: list[dict]) -> None:
    """Group MASTER rows by Contract No and render one dataframe per contract.

    Each row includes the full MASTER schema: Type, Contract No, Item
    Category, every size column, and Total.
    """
    st.caption("📑 Parsed PO tables")
    by_contract: dict[str, list[dict]] = {}
    for row in rows:
        key = row.get("Contract No") or "(unknown contract)"
        by_contract.setdefault(key, []).append(row)

    for contract, contract_rows in by_contract.items():
        st.markdown(f"**Contract No: `{contract}`**")
        display = []
        for row in contract_rows:
            entry: dict = {
                "Type": row.get("Type", ""),
                "Contract No": row.get("Contract No", ""),
                "Item Category": row.get("Item Category", ""),
            }
            for size in _SIZE_COLUMN_ORDER:
                entry[size] = row.get(size)
            display.append(entry)
        st.dataframe(display, use_container_width=True, hide_index=True)
