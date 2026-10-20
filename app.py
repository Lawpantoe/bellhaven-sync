import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

import crm
from review_db import decide_proposal, list_proposals


def _load_locations() -> dict[str, dict[str, Any]]:
    path = Path("data") / "website_locations.json"
    if not path.is_file():
        return {}
    locations = json.loads(path.read_text(encoding="utf-8"))
    return {str(item["url"]): item for item in locations}


def _address(fields: dict[str, Any], website: bool = False) -> str:
    if website:
        values = (
            fields.get("street"),
            fields.get("city"),
            fields.get("state"),
            fields.get("zip"),
        )
    else:
        values = (
            fields.get("billing_street"),
            fields.get("billing_city"),
            fields.get("billing_state"),
            fields.get("billing_zip"),
        )
    street, city, state, postal = (str(value or "") for value in values)
    locality = ", ".join(value for value in (city, state) if value)
    if postal:
        locality = f"{locality} {postal}".strip()
    return ", ".join(value for value in (street, locality) if value)


def _crm_account_url() -> str:
    load_dotenv()
    base = os.getenv("SITE_BASE")
    token = os.getenv("CRM_TOKEN")
    if not base:
        raise RuntimeError("SITE_BASE environment variable is not set.")
    if not token:
        raise RuntimeError("CRM_TOKEN environment variable is not set.")
    return f"{base.rstrip('/')}/crm/{token}/accounts"


def _approve(record: dict[str, Any]) -> None:
    proposal = record["payload"]
    if proposal["type"] in {"confident_match"}:
        st.error("Confident matches are not actionable.")
        return
    try:
        if proposal["type"] == "needs_review":
            message = (
                "Approved for manual handling; no automatic CRM write is defined."
            )
            decide_proposal(record["proposal_key"], "approved", message)
            st.info(
                message
            )
        else:
            decide_proposal(
                record["proposal_key"], "approved", "Approved in review app."
            )
            result = crm.apply_approved_proposal(proposal)
            decide_proposal(record["proposal_key"], "applied", result)
        st.success("Approval recorded.")
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        try:
            decide_proposal(record["proposal_key"], "failed", message)
        except RuntimeError:
            st.exception(exc)
            return
        st.error(f"Proposal failed: {message}")
    st.rerun()


def _reject(record: dict[str, Any], reason: str) -> None:
    message = reason.strip() or "Rejected in review app."
    decide_proposal(record["proposal_key"], "rejected", message)
    st.rerun()


def _show_proposal(
    record: dict[str, Any], locations: dict[str, dict[str, Any]]
) -> None:
    proposal = record["payload"]
    key = record["proposal_key"]
    st.markdown(f"**Proposal key:** `{key}`")
    st.markdown(f"**Account ID:** `{proposal.get('account_id') or 'New account'}`")

    website = locations.get(str(proposal.get("website_url")), {})
    website_column, crm_column = st.columns(2)
    with website_column:
        st.markdown("#### Website")
        if website:
            st.write(f"**Name:** {website.get('name', '')}")
            st.write(f"**Address:** {_address(website, website=True)}")
            st.write(
                "**Care offerings:** "
                + ", ".join(website.get("care_offerings", []))
            )
        else:
            st.write("No website location attached.")
        if proposal.get("website_url"):
            st.link_button("Open website page", proposal["website_url"])
    with crm_column:
        st.markdown("#### Current CRM data")
        current = proposal.get("current_crm_values", {})
        if current:
            st.write(f"**Name:** {current.get('name', '')}")
            st.write(f"**Address:** {_address(current)}")
            st.write(f"**Parent:** {current.get('parent_name') or current.get('parent_id') or '(none)'}")
            st.write(f"**Care type:** {current.get('care_type') or '(blank)'}")
            st.write(f"**Status:** {current.get('status') or '(blank)'}")
            st.write(f"**Lifetime revenue:** {current.get('lifetime_revenue')}")
            st.write(f"**Outstanding AR:** {current.get('outstanding_ar')}")
            with st.expander("Full CRM proposal snapshot"):
                st.json(current)
            st.link_button("Open CRM accounts", _crm_account_url())
        else:
            st.write("No current account (new account proposal).")

    st.markdown("#### Proposed changes")
    st.warning("Review the proposed changes before approving.")
    st.json(proposal.get("proposed_field_changes") or {})
    st.markdown("#### Evidence")
    st.json(proposal.get("evidence", {}))
    if record["result_message"]:
        st.info(record["result_message"])

    if proposal["type"] in {"confident_match"}:
        st.caption("Confident matches are stored for audit but are not reviewable.")
    elif proposal["type"] == "needs_review":
        st.caption(
            "Approval records a manual-review decision only; no CRM write is defined for this proposal."
        )
    approve_col, reject_col = st.columns(2)
    with approve_col:
        if st.button(
            "Approve",
            key=f"approve-{key}",
            disabled=proposal["type"] == "confident_match",
            type="primary",
        ):
            _approve(record)
    with reject_col:
        with st.form(f"reject-form-{key}"):
            reason = st.text_input("Rejection reason (optional)", key=f"reason-{key}")
            if st.form_submit_button("Reject"):
                _reject(record, reason)


def main() -> None:
    st.set_page_config(page_title="Bellhaven Proposal Review", layout="wide")
    st.title("Bellhaven Proposal Review")
    pending_records = [
        record
        for record in list_proposals(status="pending")
        if record["type"] != "confident_match"
    ]
    decided_records = [
        record
        for record in list_proposals()
        if record["status"] != "pending"
    ]
    locations = _load_locations()

    pending_tab, decided_tab = st.tabs(
        [f"Pending ({len(pending_records)})", f"Decided ({len(decided_records)})"]
    )
    with pending_tab:
        types = sorted({record["type"] for record in pending_records})
        selected_types = st.multiselect(
            "Filter by proposal type", types, default=types
        )
        visible = [
            record
            for record in pending_records
            if record["type"] in selected_types
        ]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in visible:
            grouped[record["type"]].append(record)
        if not visible:
            st.info("No pending proposals match the selected filters.")
        for proposal_type in sorted(grouped):
            st.subheader(f"{proposal_type} ({len(grouped[proposal_type])})")
            for record in grouped[proposal_type]:
                payload = record["payload"]
                name = (
                    payload.get("current_crm_values", {}).get("name")
                    or locations.get(str(payload.get("website_url")), {}).get("name")
                    or "New account"
                )
                with st.expander(f"{name} — {record['proposal_key']}"):
                    _show_proposal(record, locations)

    with decided_tab:
        if not decided_records:
            st.info("No decided proposals yet.")
        for record in decided_records:
            payload = record["payload"]
            decided_at = record["decided_at"] or "Decision time unavailable"
            with st.expander(
                f"{record['status']} — {record['type']} — "
                f"{payload.get('account_id') or 'new'} — {decided_at}"
            ):
                st.write(f"**Proposal key:** `{record['proposal_key']}`")
                st.write(f"**Result:** {record['result_message'] or '(none)'}")
                st.json(payload)


if __name__ == "__main__":
    main()
