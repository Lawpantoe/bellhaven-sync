import unittest

from matcher import PARENT_ACCOUNT_ID, build_proposals


def _parent() -> dict[str, str]:
    return {"account_id": PARENT_ACCOUNT_ID, "name": "Bellhaven"}


def _account(
    account_id: str,
    name: str,
    street: str,
    city: str,
    state: str,
    zip_code: str,
    parent_id: str = PARENT_ACCOUNT_ID,
) -> dict[str, str]:
    return {
        "account_id": account_id,
        "name": name,
        "billing_street": street,
        "billing_city": city,
        "billing_state": state,
        "billing_zip": zip_code,
        "parent_id": parent_id,
        "status": "Active",
        "lifetime_revenue": 0,
        "outstanding_ar": 0,
    }


def _location(
    name: str, street: str, city: str, state: str, zip_code: str, slug: str
) -> dict[str, object]:
    return {
        "name": name,
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "care_offerings": [],
        "url": f"https://example.test/communities/{slug}",
    }


class MatcherClassificationTests(unittest.TestCase):
    def test_same_street_and_city_with_different_zip_proposes_zip_fix(self) -> None:
        account = _account(
            "portsmouth", "Bellhaven of Portsmouth", "2222 Gallia St",
            "Portsmouth", "OH", "45626",
        )
        location = _location(
            "Bellhaven of Portsmouth", "2222 Gallia St",
            "Portsmouth", "OH", "45662", "portsmouth",
        )

        result = build_proposals([_parent(), account], [location])

        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["type"], "fix_address")
        self.assertEqual(proposal["proposed_field_changes"], {"billing_zip": "45662"})
        self.assertEqual(proposal["account_id"], "portsmouth")

    def test_matching_po_box_proposes_combined_billing_street(self) -> None:
        account = _account(
            "ashtabula", "Bellhaven of Ashtabula", "PO Box 517",
            "Ashtabula", "OH", "44004",
        )
        location = _location(
            "Bellhaven of Ashtabula", "3156 W Prospect Rd",
            "Ashtabula", "OH", "44004", "ashtabula",
        )

        result = build_proposals([_parent(), account], [location])

        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["type"], "fix_address")
        self.assertEqual(
            proposal["proposed_field_changes"],
            {"billing_street": "PO Box 517, 3156 W Prospect Rd"},
        )
        self.assertIn("retaining the PO Box", proposal["evidence"]["reason"])

    def test_weak_city_name_match_updates_crm_address_for_same_location(self) -> None:
        existing = _account(
            "union-square", "Union Square Senior Living", "240 Market St",
            "New Albany", "OH", "43054", parent_id="other-parent",
        )
        location = _location(
            "Bellhaven at Union Square", "118 Union Square Dr",
            "New Albany", "OH", "43054", "union-square",
        )

        result = build_proposals([_parent(), existing], [location])

        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["type"], "fix_address")
        self.assertEqual(
            proposal["proposed_field_changes"],
            {"billing_street": "118 Union Square Dr"},
        )

    def test_completely_different_street_creates_new_account(self) -> None:
        existing = _account(
            "other-campus", "Other Senior Living", "100 Main St",
            "Columbus", "OH", "43215", parent_id="other-parent",
        )
        location = _location(
            "Bellhaven at Union Square", "118 Union Square Dr",
            "New Albany", "OH", "43054", "union-square",
        )

        result = build_proposals([_parent(), existing], [location])

        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["type"], "create_account")
        self.assertIsNone(proposal["account_id"])
        self.assertEqual(
            proposal["proposed_field_changes"]["create_account"]["billing_street"],
            "118 Union Square Dr",
        )


if __name__ == "__main__":
    unittest.main()
