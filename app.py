import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

import crm
from matcher import PARENT_ACCOUNT_ID
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


def _load_accounts() -> dict[str, dict[str, Any]]:
    path = Path("data") / "crm_accounts.json"
    if not path.is_file():
        return {}
    accounts = json.loads(path.read_text(encoding="utf-8"))
    return {str(account["account_id"]): account for account in accounts}


def _display_value(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else "(blank)"


def _money(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return _display_value(value)
    return f"${amount:,.0f}" if amount.is_integer() else f"${amount:,.2f}"


def _account_has_billing_history(account: dict[str, Any]) -> bool:
    try:
        revenue = float(account.get("lifetime_revenue") or 0)
        outstanding = float(account.get("outstanding_ar") or 0)
    except (TypeError, ValueError):
        return False
    return revenue > 0 or outstanding > 0


def _account_completeness(account: dict[str, Any]) -> int:
    fields = (
        "name",
        "billing_street",
        "billing_city",
        "billing_state",
        "billing_zip",
        "care_type",
        "status",
    )
    return sum(bool(account.get(field)) for field in fields)


def _proposal_reason(
    proposal: dict[str, Any],
    website: dict[str, Any],
    accounts: dict[str, dict[str, Any]],
) -> str:
    proposal_type = str(proposal.get("type") or "")
    evidence = proposal.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    current = proposal.get("current_crm_values")
    current = current if isinstance(current, dict) else {}
    changes = proposal.get("proposed_field_changes")
    changes = changes if isinstance(changes, dict) else {}
    website_address = _address(website, website=True) or "(not available)"
    crm_address = _address(current) or "(not available)"
    website_name = str(website.get("name") or "website location")
    crm_name = str(current.get("name") or "CRM account")

    if proposal_type == "needs_review":
        if evidence.get("weak_match"):
            score = evidence.get("name_similarity", "unknown")
            return (
                f"Weak match: same city/state and similar name (score {score}), "
                f"but the address differs: website {website_address} vs CRM "
                f"{crm_address}."
            )
        reason = evidence.get("reason") or "the available match evidence is inconclusive"
        return (
            f"Manual review is needed because {reason} Website address: "
            f"{website_address}; CRM address: {crm_address}."
        )

    if proposal_type == "not_on_website":
        return (
            "This account is under Bellhaven in the CRM, but no location on the "
            "Bellhaven website matches its address or name."
        )

    if proposal_type in {"reparent", "chow"}:
        target_parent_id = changes.get("parent_id")
        if proposal_type == "chow":
            create_fields = changes.get("create_account")
            if isinstance(create_fields, dict):
                target_parent_id = create_fields.get("parent_id")
        target_parent = accounts.get(str(target_parent_id), {})
        target_parent_name = (
            target_parent.get("name") or target_parent_id or "(not specified)"
        )
        current_parent = (
            current.get("parent_name")
            or current.get("parent_id")
            or "(none)"
        )
        revenue = evidence.get("lifetime_revenue", current.get("lifetime_revenue"))
        outstanding = evidence.get(
            "outstanding_ar", current.get("outstanding_ar")
        )
        result = (
            "create a new account under the target parent and link the existing "
            "account (CHOW); do not re-parent the existing account"
            if proposal_type == "chow"
            else "re-parent directly"
        )
        return (
            f"Current parent: {current_parent}; target parent: {target_parent_name}. "
            f"Revenue {_money(revenue)}, AR {_money(outstanding)} -> {result}."
        )

    if proposal_type == "duplicate":
        survivor_id = str(evidence.get("survivor_account_id") or "")
        survivor = accounts.get(survivor_id, {})
        survivor_name = str(survivor.get("name") or "CRM account")
        survivor_label = (
            f"{survivor_name} ({survivor_id})"
            if survivor_id
            else survivor_name
        )
        duplicate_location = website_address
        reasons: list[str] = []
        if survivor:
            bellhaven_parent = (
                str(survivor.get("parent_id") or "") == PARENT_ACCOUNT_ID
            )
            loser_in_bellhaven = (
                str(current.get("parent_id") or "") == PARENT_ACCOUNT_ID
            )
            if bellhaven_parent != loser_in_bellhaven:
                reasons.append(
                    "it is already under Bellhaven"
                    if bellhaven_parent
                    else "the other account is already under Bellhaven"
                )
            survivor_has_history = _account_has_billing_history(survivor)
            loser_has_history = bool(evidence.get("has_billing_history"))
            if survivor_has_history != loser_has_history:
                reasons.append(
                    "it has billing history"
                    if survivor_has_history
                    else "it has no billing history"
                )
            survivor_completeness = _account_completeness(survivor)
            loser_completeness = _account_completeness(current)
            if survivor_completeness != loser_completeness:
                reasons.append(
                    "its CRM record is more complete"
                    if survivor_completeness > loser_completeness
                    else "the other CRM record is more complete"
                )
            if reasons:
                reasons = [reasons[0]]
            else:
                reasons.append("its account ID wins the final tie-break")
        else:
            reasons.append(
                "the CRM survivor ranking preferred parent, billing history, "
                "record completeness, and account-ID order"
            )
        return (
            f"CRM accounts {crm_name} ({proposal.get('account_id') or 'no ID'}) and "
            f"{survivor_label} both match website address {duplicate_location}; "
            f"{survivor_label} was chosen as the survivor because "
            f"{'; '.join(reasons)}."
        )

    if proposal_type == "update_name":
        new_name = changes.get("name") or website_name
        return (
            f"The address matches the website location, but the CRM name "
            f"{crm_name!r} differs from the website name {new_name!r}."
        )

    if proposal_type == "fix_address":
        proposed = dict(current)
        proposed.update(changes)
        return (
            f"The CRM billing address {crm_address} differs from the website "
            f"address {website_address}; the proposed correction is "
            f"{_address(proposed)}."
        )

    if proposal_type == "create_account":
        return (
            f"No eligible CRM account matched {website_name}; create an account "
            f"at the website address {website_address}."
        )

    if proposal_type == "confident_match":
        reason = evidence.get("reason")
        if reason == "billing address is a PO Box":
            return (
                f"The CRM name, city, state, and ZIP match {website_name}; its "
                "billing address is a PO Box, so the website street is not used "
                "to change the CRM billing address."
            )
        score = evidence.get("name_similarity", "unknown")
        return (
            f"{crm_name} confidently matches {website_name} by address and name "
            f"(name similarity score {score}); no CRM change is needed."
        )

    if proposal_type == "fill_care_type":
        offering = evidence.get("website_care_offering", "website offering")
        mapped = changes.get("care_type", evidence.get("mapped_care_type"))
        return (
            f"The CRM care type is blank, while the website lists {offering!r}; "
            f"the offering maps to {mapped or 'no CRM care type'}."
        )

    reason = evidence.get("reason")
    if reason:
        return f"This {proposal_type.replace('_', ' ')} was proposed because {reason}"
    return (
        f"This {proposal_type.replace('_', ' ')} was proposed from the CRM and "
        "website comparison."
    )


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
    record: dict[str, Any],
    locations: dict[str, dict[str, Any]],
    accounts: dict[str, dict[str, Any]],
) -> None:
    proposal = record["payload"]
    key = record["proposal_key"]
    website = locations.get(str(proposal.get("website_url")), {})
    st.markdown(
        f"**Why this was proposed:** "
        f"{_proposal_reason(proposal, website, accounts)}"
    )
    st.markdown(f"**Proposal key:** `{key}`")
    st.markdown(f"**Account ID:** `{proposal.get('account_id') or 'New account'}`")

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
    all_records = list_proposals()
    confident_records = [
        record for record in all_records if record["type"] == "confident_match"
    ]
    locations = _load_locations()
    accounts = _load_accounts()

    pending_tab, decided_tab, confident_tab = st.tabs(
        [
            f"Pending ({len(pending_records)})",
            f"Decided ({len(decided_records)})",
            f"Confident matches ({len(confident_records)})",
        ]
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
                    _show_proposal(record, locations, accounts)

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
                website = locations.get(str(payload.get("website_url")), {})
                st.markdown(
                    f"**Why this was proposed:** "
                    f"{_proposal_reason(payload, website, accounts)}"
                )
                st.write(f"**Proposal key:** `{record['proposal_key']}`")
                st.write(f"**Result:** {record['result_message'] or '(none)'}")
                st.json(payload)

    with confident_tab:
        for record in confident_records:
            payload = record["payload"]
            website = locations.get(str(payload.get("website_url")), {})
            name = (
                payload.get("current_crm_values", {}).get("name")
                or website.get("name")
                or "Confident match"
            )
            with st.expander(f"{name} — {record['proposal_key']}"):
                st.markdown(
                    f"**Why this was proposed:** "
                    f"{_proposal_reason(payload, website, accounts)}"
                )
                st.json(payload.get("evidence", {}))


if __name__ == "__main__":
    main()
