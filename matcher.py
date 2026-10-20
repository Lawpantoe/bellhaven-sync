import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from rapidfuzz import fuzz

DATA_DIR = Path("data")
PARENT_ACCOUNT_ID = "0015QAPLGS3FVYEEEM"
CARE_TYPE_MAP = {
    "memory support": "Memory Care",
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "assisted living": "Assisted Living",
}
NAME_FILLER_WORDS = {"bellhaven", "the", "of", "at", "senior", "living"}
STREET_WORDS = {
    "street": "st",
    "st": "st",
    "avenue": "ave",
    "ave": "ave",
    "road": "rd",
    "rd": "rd",
    "drive": "dr",
    "dr": "dr",
    "boulevard": "blvd",
    "blvd": "blvd",
    "north": "n",
    "northeast": "ne",
    "northwest": "nw",
    "south": "s",
    "southeast": "se",
    "southwest": "sw",
    "east": "e",
    "west": "w",
    "lane": "ln",
    "ln": "ln",
    "court": "ct",
    "ct": "ct",
    "circle": "cir",
    "cir": "cir",
    "parkway": "pkwy",
    "pkwy": "pkwy",
    "terrace": "ter",
    "ter": "ter",
    "place": "pl",
    "pl": "pl",
    "highway": "hwy",
    "hwy": "hwy",
    "trail": "trl",
    "trl": "trl",
}
UNIT_RE = re.compile(r"\b(?:suite|ste|unit|apartment|apt)\b.*$|#.*$", re.I)
WORD_RE = re.compile(r"[a-z0-9]+")
STREET_NUMBER_RE = re.compile(r"^\s*(\d+[a-z]?)\b", re.I)
ZIP_RE = re.compile(r"\d{5}")
PO_BOX_RE = re.compile(r"^\s*p\.?\s*o\.?\s*box\b", re.I)


def _words(value: Any) -> list[str]:
    return WORD_RE.findall(str(value or "").lower())


def _normalize_name(value: Any) -> str:
    return " ".join(word for word in _words(value) if word not in NAME_FILLER_WORDS)


def _normalize_street(value: Any) -> tuple[str, str]:
    street = UNIT_RE.sub("", str(value or "").lower())
    words = [STREET_WORDS.get(word, word) for word in _words(street)]
    number = ""
    if words and STREET_NUMBER_RE.match(str(value or "")):
        number = words.pop(0)
    return number, " ".join(words)


def _normalize_zip(value: Any) -> str:
    match = ZIP_RE.search(str(value or ""))
    return match.group(0) if match else ""


def _name_score(location: dict[str, Any], account: dict[str, Any]) -> int:
    location_name = _normalize_name(location.get("name"))
    account_name = _normalize_name(account.get("name"))
    if not location_name or not account_name:
        return 0
    return round(fuzz.token_set_ratio(location_name, account_name))


def _street_score(location: dict[str, Any], account: dict[str, Any]) -> int:
    _, location_street = _normalize_street(location.get("street"))
    _, account_street = _normalize_street(account.get("billing_street"))
    if not location_street or not account_street:
        return 0
    return round(fuzz.ratio(location_street, account_street))


def _address_evidence(
    location: dict[str, Any], account: dict[str, Any]
) -> dict[str, Any]:
    location_number, location_street = _normalize_street(location.get("street"))
    account_number, account_street = _normalize_street(
        account.get("billing_street")
    )
    location_zip = _normalize_zip(location.get("zip"))
    account_zip = _normalize_zip(account.get("billing_zip"))
    location_city = " ".join(_words(location.get("city")))
    account_city = " ".join(_words(account.get("billing_city")))
    location_state = str(location.get("state") or "").strip().upper()
    account_state = str(account.get("billing_state") or "").strip().upper()
    street_score = _street_score(location, account)
    same_zip = bool(location_zip and location_zip == account_zip)
    same_number = bool(location_number and location_number == account_number)
    same_street = bool(
        location_street
        and account_street
        and location_number == account_number
        and location_street == account_street
    )
    same_city_state = bool(
        location_city
        and location_state
        and location_city == account_city
        and location_state == account_state
    )
    location_name = _normalize_name(location.get("name"))
    account_name = _normalize_name(account.get("name"))
    name_similarity = _name_score(location, account)
    zip_mismatch_match = bool(
        same_city_state
        and same_street
        and location_zip
        and account_zip
        and location_zip != account_zip
    )
    billing_address_is_po_box = bool(
        PO_BOX_RE.match(str(account.get("billing_street") or ""))
    )
    po_box_match = bool(
        billing_address_is_po_box
        and same_zip
        and same_city_state
        and location_name
        and location_name == account_name
    )
    street_completely_different = bool(
        location_street
        and account_street
        and not set(_words(location_street)).intersection(_words(account_street))
    )
    address_differs = (
        location_number != account_number
        or location_street != account_street
        or location_zip != account_zip
        or location_city != account_city
        or location_state != account_state
    )
    strong = (
        (same_zip and same_number and street_score >= 90)
        or zip_mismatch_match
        or po_box_match
    )
    weak = (
        same_city_state
        and name_similarity >= 85
        and address_differs
        and not street_completely_different
        and not zip_mismatch_match
        and not po_box_match
    )
    return {
        "same_zip": same_zip,
        "same_street": same_street,
        "same_street_number": same_number,
        "street_name_similarity": street_score,
        "same_city_state": same_city_state,
        "name_similarity": name_similarity,
        "zip_mismatch_match": zip_mismatch_match,
        "billing_address_is_po_box": billing_address_is_po_box,
        "po_box_match": po_box_match,
        "street_completely_different": street_completely_different,
        "strong_match": strong,
        "weak_match": weak,
        "normalized_location_street": location_street,
        "normalized_account_street": account_street,
    }


def _amount(value: Any) -> float | None:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_billing_history(account: dict[str, Any]) -> bool:
    revenue = _amount(account.get("lifetime_revenue"))
    outstanding = _amount(account.get("outstanding_ar"))
    return bool(
        (revenue is not None and revenue > 0)
        or (outstanding is not None and outstanding > 0)
    )


def _current_values(account: dict[str, Any] | None) -> dict[str, Any]:
    if account is None:
        return {}
    keys = (
        "name",
        "parent_id",
        "parent_name",
        "billing_street",
        "billing_city",
        "billing_state",
        "billing_zip",
        "care_type",
        "status",
        "lifetime_revenue",
        "outstanding_ar",
        "duplicate_of_account",
        "chow_current_account",
        "note",
    )
    return {key: account.get(key) for key in keys}


def _slug(location: dict[str, Any]) -> str:
    return urlsplit(str(location.get("url") or "")).path.rstrip("/").rsplit("/", 1)[-1]


def _proposal(
    proposal_type: str,
    account: dict[str, Any] | None,
    location: dict[str, Any] | None,
    changes: dict[str, Any],
    evidence: dict[str, Any],
    key_suffix: str = "",
) -> dict[str, Any]:
    account_id = account.get("account_id") if account else None
    slug = _slug(location) if location else "none"
    key_account = account_id or "new"
    suffix = f":{key_suffix}" if key_suffix else ""
    return {
        "proposal_key": f"{proposal_type}:{key_account}:{slug}{suffix}",
        "type": proposal_type,
        "account_id": account_id,
        "website_url": location.get("url") if location else None,
        "current_crm_values": _current_values(account),
        "proposed_field_changes": changes,
        "evidence": evidence,
    }


def _skipped_accounts(
    accounts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    skipped: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for account in accounts:
        if account.get("account_id") == PARENT_ACCOUNT_ID:
            continue
        reasons: list[str] = []
        if str(account.get("status") or "").strip().casefold() == "inactive":
            reasons.append("status is Inactive")
        if account.get("duplicate_of_account"):
            reasons.append("duplicate_of_account is already set")
        if account.get("chow_current_account"):
            reasons.append("chow_current_account is already set")
        if reasons:
            skipped.append(
                {
                    "account_id": account.get("account_id"),
                    "name": account.get("name"),
                    "reasons": reasons,
                }
            )
        else:
            eligible.append(account)
    return eligible, skipped


def _survivor_key(account: dict[str, Any]) -> tuple[int, int, int, str]:
    completeness_fields = (
        "name",
        "billing_street",
        "billing_city",
        "billing_state",
        "billing_zip",
        "care_type",
        "status",
    )
    completeness = sum(bool(account.get(field)) for field in completeness_fields)
    return (
        0 if account.get("parent_id") == PARENT_ACCOUNT_ID else 1,
        0 if _has_billing_history(account) else 1,
        -completeness,
        str(account.get("account_id") or ""),
    )


def _location_changes(
    account: dict[str, Any], location: dict[str, Any]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    changes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    if account.get("name") != location.get("name"):
        changes.append(("update_name", {"name": location.get("name")}, {}))
    if not str(account.get("care_type") or "").strip():
        offering = next(
            (
                str(value).strip()
                for value in location.get("care_offerings", [])
                if str(value).strip()
            ),
            "",
        )
        mapped = CARE_TYPE_MAP.get(offering.casefold())
        if mapped:
            changes.append(("fill_care_type", {"care_type": mapped}, {
                "website_care_offering": offering,
                "mapped_care_type": mapped,
            }))
    return changes


def _match_proposals(
    account: dict[str, Any],
    location: dict[str, Any],
    address_evidence: dict[str, Any],
    suffix: str = "",
) -> list[dict[str, Any]]:
    evidence = {
        **address_evidence,
        "lifetime_revenue": account.get("lifetime_revenue"),
        "outstanding_ar": account.get("outstanding_ar"),
        "has_billing_history": _has_billing_history(account),
    }

    if address_evidence["po_box_match"]:
        return [
            _proposal(
                "confident_match",
                account,
                location,
                {},
                {**evidence, "reason": "billing address is a PO Box"},
                suffix,
            )
        ]

    if address_evidence["zip_mismatch_match"]:
        return [
            _proposal(
                "fix_address",
                account,
                location,
                {"billing_zip": location.get("zip")},
                {
                    **evidence,
                    "reason": "Street and city/state match; billing ZIP differs.",
                },
                suffix,
            )
        ]

    if account.get("parent_id") != PARENT_ACCOUNT_ID:
        revenue = _amount(account.get("lifetime_revenue"))
        outstanding = _amount(account.get("outstanding_ar"))
        if revenue is None or outstanding is None:
            return [
                _proposal(
                    "needs_review",
                    account,
                    location,
                    {},
                    {**evidence, "reason": "Billing values are not numeric."},
                    suffix,
                )
            ]
        if revenue > 0 and outstanding > 0:
            return [
                _proposal(
                    "chow",
                    account,
                    location,
                    {
                        "create_account": {
                            "name": location.get("name"),
                            "billing_street": location.get("street"),
                            "billing_city": location.get("city"),
                            "billing_state": location.get("state"),
                            "billing_zip": location.get("zip"),
                            "care_type": (
                                CARE_TYPE_MAP.get(
                                    str(location.get("care_offerings", [""])[0]).casefold()
                                )
                            ),
                            "parent_id": PARENT_ACCOUNT_ID,
                        },
                        "set_existing_account": {
                            "chow_current_account": "<new_account_id>"
                        },
                    },
                    {**evidence, "old_account_untouched": True},
                    suffix,
                )
            ]

    proposals = []
    for proposal_type, changes, extra_evidence in _location_changes(account, location):
        proposals.append(
            _proposal(
                proposal_type,
                account,
                location,
                changes,
                {**evidence, **extra_evidence},
                suffix,
            )
        )

    if account.get("parent_id") != PARENT_ACCOUNT_ID:
        proposals.append(
            _proposal(
                "reparent",
                account,
                location,
                {"parent_id": PARENT_ACCOUNT_ID},
                evidence,
                suffix,
            )
        )
    if not proposals:
        proposals.append(
            _proposal("confident_match", account, location, {}, evidence, suffix)
        )
    return proposals


def build_proposals(
    accounts: list[dict[str, Any]], locations: list[dict[str, Any]]
) -> dict[str, Any]:
    parent = next(
        (account for account in accounts if account.get("account_id") == PARENT_ACCOUNT_ID),
        None,
    )
    if parent is None:
        raise RuntimeError(f"Bellhaven parent account {PARENT_ACCOUNT_ID} is missing.")

    eligible, skipped = _skipped_accounts(accounts)
    proposals: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    matched_accounts: set[str] = set()
    location_matches: dict[str, dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]] = {}

    for location in locations:
        strong_matches = []
        weak_matches = []
        for account in eligible:
            evidence = _address_evidence(location, account)
            if evidence["strong_match"]:
                strong_matches.append((account, evidence))
            elif evidence["weak_match"]:
                weak_matches.append((account, evidence))
        location_matches[str(location.get("url"))] = {
            "strong": strong_matches,
            "weak": weak_matches,
        }
        matched_accounts.update(
            str(account.get("account_id"))
            for account, _ in strong_matches + weak_matches
            if account.get("account_id")
        )

    strong_locations_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for location in locations:
        for account, _ in location_matches[str(location.get("url"))]["strong"]:
            strong_locations_by_account[str(account.get("account_id"))].append(location)

    conflicted_accounts = {
        account_id
        for account_id, matched_locations in strong_locations_by_account.items()
        if len({str(item.get("url")) for item in matched_locations}) > 1
    }

    for location in locations:
        matches = location_matches[str(location.get("url"))]
        strong_matches = matches["strong"]
        weak_matches = matches["weak"]

        if any(
            str(account.get("account_id")) in conflicted_accounts
            for account, _ in strong_matches
        ):
            affected = [
                (account, evidence)
                for account, evidence in strong_matches
                if str(account.get("account_id")) in conflicted_accounts
            ]
            for account, evidence in affected:
                proposals.append(
                    _proposal(
                        "needs_review",
                        account,
                        location,
                        {},
                        {
                            **evidence,
                            "reason": "One CRM account strongly matches multiple website locations.",
                            "conflicting_website_urls": sorted(
                                item.get("url")
                                for item in strong_locations_by_account[
                                    str(account.get("account_id"))
                                ]
                            ),
                        },
                    )
                )
                uncertain.append(
                    {
                        "kind": "account_location_conflict",
                        "account_id": account.get("account_id"),
                        "website_url": location.get("url"),
                        "reason": "A single CRM account strongly matches multiple website locations.",
                    }
                )
            continue

        if len(strong_matches) > 1:
            survivor, survivor_evidence = min(
                strong_matches, key=lambda item: _survivor_key(item[0])
            )
            proposals.extend(
                _match_proposals(
                    survivor, location, survivor_evidence, suffix="survivor"
                )
            )
            for loser, evidence in strong_matches:
                if loser is survivor:
                    continue
                old_note = str(loser.get("note") or "").strip()
                duplicate_note = (
                    f"Duplicate of {survivor.get('account_id')} for "
                    f"{location.get('name')} ({location.get('url')})."
                )
                note = f"{old_note}\n{duplicate_note}" if old_note else duplicate_note
                proposals.append(
                    _proposal(
                        "duplicate",
                        loser,
                        location,
                        {
                            "duplicate_of_account": survivor.get("account_id"),
                            "status": "Inactive",
                            "note": note,
                        },
                        {
                            **evidence,
                            "survivor_account_id": survivor.get("account_id"),
                            "has_billing_history": _has_billing_history(loser),
                            "billing_history_flag": (
                                "Loser has billing history; review before applying."
                                if _has_billing_history(loser)
                                else None
                            ),
                            "name_similarity": _name_score(location, loser),
                        },
                    )
                )
                if _has_billing_history(loser):
                    uncertain.append(
                        {
                            "kind": "duplicate_with_billing_history",
                            "account_id": loser.get("account_id"),
                            "website_url": location.get("url"),
                            "reason": "Duplicate loser has lifetime revenue or outstanding AR.",
                        }
                    )
            continue

        if len(strong_matches) == 1:
            account, evidence = strong_matches[0]
            proposals.extend(_match_proposals(account, location, evidence))
            if evidence["name_similarity"] < 85:
                uncertain.append(
                    {
                        "kind": "strong_address_name_mismatch",
                        "account_id": account.get("account_id"),
                        "website_url": location.get("url"),
                        "reason": (
                            "Address meets the strong-match rule, but the name "
                            f"similarity is only {evidence['name_similarity']}."
                        ),
                    }
                )
            if not _location_changes(account, location) and account.get(
                "parent_id"
            ) == PARENT_ACCOUNT_ID:
                mapped = [
                    CARE_TYPE_MAP.get(str(value).strip().casefold())
                    for value in location.get("care_offerings", [])
                ]
                current_type = str(account.get("care_type") or "").strip()
                if current_type and mapped and current_type not in mapped:
                    uncertain.append(
                        {
                            "kind": "care_type_difference",
                            "account_id": account.get("account_id"),
                            "website_url": location.get("url"),
                            "reason": (
                                f"CRM care_type is {current_type!r}, while the website "
                                f"offers map to {mapped!r}; only blank CRM care types "
                                "are filled automatically."
                            ),
                        }
                    )
            continue

        if weak_matches:
            for account, evidence in weak_matches:
                proposals.append(
                    _proposal(
                        "needs_review",
                        account,
                        location,
                        {},
                        {
                            **evidence,
                            "reason": "Weak name and city/state match; address differs.",
                        },
                    )
                )
                uncertain.append(
                    {
                        "kind": "weak_match",
                        "account_id": account.get("account_id"),
                        "website_url": location.get("url"),
                        "reason": "Name and city/state suggest a possible match, but the address differs.",
                    }
                )
            continue

        offering = next(
            (
                str(value).strip()
                for value in location.get("care_offerings", [])
                if str(value).strip()
            ),
            "",
        )
        mapped_care_type = CARE_TYPE_MAP.get(offering.casefold())
        proposed_account = {
            "name": location.get("name"),
            "billing_street": location.get("street"),
            "billing_city": location.get("city"),
            "billing_state": location.get("state"),
            "billing_zip": location.get("zip"),
            "care_type": mapped_care_type,
            "parent_id": PARENT_ACCOUNT_ID,
        }
        proposals.append(
            _proposal(
                "create_account",
                None,
                location,
                {"create_account": proposed_account},
                {
                    "reason": "No strong or weak eligible CRM account match.",
                    "mapped_website_care_offering": offering,
                    "mapped_care_type": mapped_care_type,
                },
            )
        )
        if offering and mapped_care_type is None:
            uncertain.append(
                {
                    "kind": "unmapped_care_offering",
                    "website_url": location.get("url"),
                    "reason": f"Website care offering {offering!r} has no CRM care_type mapping.",
                }
            )

    today = date.today().isoformat()
    for account in eligible:
        account_id = str(account.get("account_id") or "")
        if (
            account.get("parent_id") == PARENT_ACCOUNT_ID
            and str(account.get("status") or "").strip().casefold() == "active"
            and account_id not in matched_accounts
        ):
            old_note = str(account.get("note") or "").strip()
            note = (
                f"{old_note}\nNo longer listed on the Bellhaven website as of {today}."
                if old_note
                else f"No longer listed on the Bellhaven website as of {today}."
            )
            proposals.append(
                _proposal(
                    "not_on_website",
                    account,
                    None,
                    {"status": "Needs Review", "note": note},
                    {
                        "reason": "Active account under Bellhaven matched no website location.",
                        "checked_on": today,
                    },
                )
            )

    proposals.sort(
        key=lambda proposal: (
            proposal["type"],
            str(proposal["account_id"] or ""),
            str(proposal["website_url"] or ""),
            proposal["proposal_key"],
        )
    )
    skipped.sort(key=lambda item: str(item.get("account_id") or ""))
    uncertain.sort(
        key=lambda item: (
            item["kind"],
            str(item.get("account_id") or ""),
            str(item.get("website_url") or ""),
        )
    )
    keys = [proposal["proposal_key"] for proposal in proposals]
    if len(keys) != len(set(keys)):
        raise RuntimeError("Generated proposal keys are not unique.")

    return {
        "generated_on": today,
        "proposals": proposals,
        "skipped": skipped,
        "uncertain": uncertain,
    }


def main() -> None:
    accounts_path = DATA_DIR / "crm_accounts.json"
    locations_path = DATA_DIR / "website_locations.json"
    if not accounts_path.is_file() or not locations_path.is_file():
        raise FileNotFoundError(
            "Run collect.py first to create data/crm_accounts.json and "
            "data/website_locations.json."
        )

    accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
    locations = json.loads(locations_path.read_text(encoding="utf-8"))
    result = build_proposals(accounts, locations)
    (DATA_DIR / "proposals.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    counts: dict[str, int] = defaultdict(int)
    for proposal in result["proposals"]:
        counts[proposal["type"]] += 1
    print(f"Wrote {len(result['proposals'])} proposals to data/proposals.json")
    for proposal_type in sorted(counts):
        print(f"{proposal_type}: {counts[proposal_type]}")
    print(f"Skipped accounts: {len(result['skipped'])}")
    print(f"Uncertain classifications: {len(result['uncertain'])}")


if __name__ == "__main__":
    main()
