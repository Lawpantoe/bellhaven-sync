import os
from typing import Any

import requests
from dotenv import load_dotenv

PAGE_SIZE = 100
REQUEST_TIMEOUT = 30
PARENT_ACCOUNT_ID = "0015QAPLGS3FVYEEEM"


def get_all_accounts() -> list[dict[str, Any]]:
    load_dotenv()
    base = os.getenv("CRM_BASE")
    token = os.getenv("CRM_TOKEN")
    if not base:
        raise RuntimeError("CRM_BASE environment variable is not set.")
    if not token:
        raise RuntimeError("CRM_TOKEN environment variable is not set.")

    accounts: list[dict[str, Any]] = []
    page = 1
    total: int | None = None

    with requests.Session() as session:
        while total is None or len(accounts) < total:
            response = session.get(
                f"{base.rstrip('/')}/accounts",
                headers={"Authorization": f"Bearer {token}"},
                params={"page": page, "page_size": PAGE_SIZE},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("CRM /accounts response must be a JSON object.")

            page_accounts = payload.get("data")
            response_total = payload.get("total")
            if not isinstance(page_accounts, list) or not all(
                isinstance(account, dict) for account in page_accounts
            ):
                raise RuntimeError("CRM /accounts response has invalid account data.")
            if (
                not isinstance(response_total, int)
                or isinstance(response_total, bool)
                or response_total < 0
            ):
                raise RuntimeError("CRM /accounts response has an invalid total.")

            total = response_total
            accounts.extend(page_accounts)
            if len(accounts) < total and not page_accounts:
                raise RuntimeError(
                    f"CRM returned an empty page before all {total} accounts were fetched."
                )
            page += 1

    return accounts[:total]


def _credentials() -> tuple[str, dict[str, str]]:
    load_dotenv()
    base = os.getenv("CRM_BASE")
    token = os.getenv("CRM_TOKEN")
    if not base:
        raise RuntimeError("CRM_BASE environment variable is not set.")
    if not token:
        raise RuntimeError("CRM_TOKEN environment variable is not set.")
    return base.rstrip("/"), {"Authorization": f"Bearer {token}"}


def get_account(account_id: str) -> dict[str, Any]:
    base, headers = _credentials()
    response = requests.get(
        f"{base}/accounts/{account_id}",
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    account = response.json()
    if not isinstance(account, dict) or account.get("account_id") != account_id:
        raise RuntimeError(f"CRM returned invalid account data for {account_id}.")
    return account


def patch_account(account_id: str, fields: dict[str, Any]) -> None:
    if not fields:
        raise ValueError("Refusing to PATCH an account with no proposed fields.")
    base, headers = _credentials()
    response = requests.patch(
        f"{base}/accounts/{account_id}",
        headers=headers,
        json=fields,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def create_account(fields: dict[str, Any]) -> str:
    base, headers = _credentials()
    response = requests.post(
        f"{base}/accounts",
        headers=headers,
        json=fields,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if not isinstance(payload, dict) or not payload.get("account_id"):
        raise RuntimeError("CRM account creation response did not include account_id.")
    return str(payload["account_id"])


class CRMChangedError(RuntimeError):
    pass


def _same_value(expected: Any, actual: Any) -> bool:
    if expected == actual:
        return True
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, (int, float)) and not isinstance(actual, bool):
            return float(expected) == float(actual)
    return False


def _check_proposal_snapshot(
    account_id: str, expected_values: dict[str, Any]
) -> dict[str, Any]:
    account = get_account(account_id)
    changed_fields = [
        field
        for field, expected in expected_values.items()
        if not _same_value(expected, account.get(field))
    ]
    if changed_fields:
        raise CRMChangedError(
            "CRM changed since proposal "
            f"(changed fields: {', '.join(sorted(changed_fields))})."
        )
    return account


def _verify_fields(account_id: str, fields: dict[str, Any]) -> None:
    account = get_account(account_id)
    mismatches = [
        field
        for field, expected in fields.items()
        if not _same_value(expected, account.get(field))
    ]
    if mismatches:
        raise RuntimeError(
            f"CRM did not confirm proposed fields: {', '.join(sorted(mismatches))}."
        )


def _account_fields(proposal: dict[str, Any]) -> dict[str, Any]:
    changes = proposal.get("proposed_field_changes", {})
    fields = changes.get("create_account")
    if not isinstance(fields, dict):
        raise ValueError("Proposal is missing create_account fields.")
    allowed_fields = {
        "name",
        "billing_street",
        "billing_city",
        "billing_state",
        "billing_zip",
        "care_type",
    }
    account_fields = {
        field: fields[field] for field in allowed_fields if field in fields
    }
    account_fields["parent_id"] = PARENT_ACCOUNT_ID
    return account_fields


def apply_approved_proposal(proposal: dict[str, Any]) -> str:
    proposal_type = proposal.get("type")
    changes = proposal.get("proposed_field_changes", {})
    account_id = proposal.get("account_id")

    if proposal_type in {"confident_match", "needs_review"}:
        return "Approved for manual handling; no automatic CRM write is defined."

    if proposal_type == "create_account":
        fields = _account_fields(proposal)
        new_account_id = create_account(fields)
        try:
            _verify_fields(new_account_id, fields)
        except Exception as exc:
            raise RuntimeError(
                f"Created CRM account {new_account_id}, but could not verify it: {exc}"
            ) from exc
        return f"Created and verified CRM account {new_account_id}."

    if not account_id:
        raise ValueError(f"{proposal_type} proposal is missing account_id.")
    expected_values = proposal.get("current_crm_values")
    if not isinstance(expected_values, dict):
        raise ValueError("Proposal is missing current CRM values.")
    _check_proposal_snapshot(str(account_id), expected_values)

    if proposal_type == "chow":
        fields = _account_fields(proposal)
        new_account_id = create_account(fields)
        try:
            _verify_fields(new_account_id, fields)
            _check_proposal_snapshot(str(account_id), expected_values)
            chow_field = {"chow_current_account": new_account_id}
            patch_account(str(account_id), chow_field)
            _verify_fields(str(account_id), chow_field)
        except Exception as exc:
            raise RuntimeError(
                f"Created and verified new account {new_account_id}, but could not "
                f"complete the CHOW link on {account_id}: {exc}"
            ) from exc
        return (
            f"Created and verified CRM account {new_account_id}; linked old "
            "account using chow_current_account only."
        )

    if proposal_type == "duplicate":
        fields = {
            field: changes[field]
            for field in ("duplicate_of_account", "status", "note")
            if field in changes
        }
    elif proposal_type in {"update_name", "reparent", "fill_care_type"}:
        fields = dict(changes)
    elif proposal_type == "not_on_website":
        fields = {
            field: changes[field]
            for field in ("status", "note")
            if field in changes
        }
    else:
        raise ValueError(f"Unsupported proposal type: {proposal_type}")

    patch_account(str(account_id), fields)
    _verify_fields(str(account_id), fields)
    return f"Updated and verified CRM account {account_id}."
