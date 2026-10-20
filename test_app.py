import unittest

from app import _proposal_reason
from matcher import PARENT_ACCOUNT_ID


class ProposalReasonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.website = {
            "name": "Bellhaven of Portsmouth",
            "street": "2222 Gallia St",
            "city": "Portsmouth",
            "state": "OH",
            "zip": "45662",
        }
        self.current = {
            "name": "Bellhaven of Portsmouth",
            "parent_id": PARENT_ACCOUNT_ID,
            "parent_name": "Bellhaven Senior Living",
            "billing_street": "2222 Gallia St",
            "billing_city": "Portsmouth",
            "billing_state": "OH",
            "billing_zip": "45626",
            "lifetime_revenue": 47000,
            "outstanding_ar": 0,
        }
        self.proposal = {
            "type": "needs_review",
            "account_id": "account-1",
            "current_crm_values": self.current,
            "proposed_field_changes": {},
            "evidence": {
                "weak_match": True,
                "name_similarity": 94,
                "reason": "Weak name and city/state match; address differs.",
            },
        }

    def test_weak_match_explains_score_and_compares_addresses(self) -> None:
        reason = _proposal_reason(self.proposal, self.website, {})

        self.assertIn("Weak match", reason)
        self.assertIn("score 94", reason)
        self.assertIn("website 2222 Gallia St, Portsmouth, OH 45662", reason)
        self.assertIn("CRM 2222 Gallia St, Portsmouth, OH 45626", reason)

    def test_parent_change_explains_parent_values_and_sop_result(self) -> None:
        proposal = {
            **self.proposal,
            "type": "reparent",
            "proposed_field_changes": {"parent_id": PARENT_ACCOUNT_ID},
        }

        reason = _proposal_reason(proposal, self.website, {})

        self.assertIn("Current parent: Bellhaven Senior Living", reason)
        self.assertIn("target parent: 0015QAPLGS3FVYEEEM", reason)
        self.assertIn("Revenue $47,000, AR $0 -> re-parent directly", reason)

    def test_chow_explains_new_account_result(self) -> None:
        proposal = {
            **self.proposal,
            "type": "chow",
            "evidence": {"lifetime_revenue": 47000, "outstanding_ar": 500},
            "proposed_field_changes": {
                "create_account": {"parent_id": PARENT_ACCOUNT_ID}
            },
        }

        reason = _proposal_reason(proposal, self.website, {})

        self.assertIn("Revenue $47,000, AR $500", reason)
        self.assertIn("create a new account", reason)
        self.assertIn("do not re-parent", reason)

    def test_duplicate_names_accounts_and_survivor_reason(self) -> None:
        proposal = {
            **self.proposal,
            "type": "duplicate",
            "account_id": "loser",
            "current_crm_values": {
                **self.current,
                "parent_id": "other-parent",
                "parent_name": "Other parent",
            },
            "evidence": {
                "survivor_account_id": "survivor",
                "has_billing_history": True,
            },
        }
        accounts = {
            "survivor": {
                "account_id": "survivor",
                "name": "Bellhaven Portsmouth",
                "parent_id": PARENT_ACCOUNT_ID,
                "billing_street": "2222 Gallia St",
                "billing_city": "Portsmouth",
                "billing_state": "OH",
                "billing_zip": "45662",
            }
        }

        reason = _proposal_reason(proposal, self.website, accounts)

        self.assertIn("Bellhaven of Portsmouth (loser)", reason)
        self.assertIn("Bellhaven Portsmouth (survivor)", reason)
        self.assertIn("website address 2222 Gallia St, Portsmouth, OH 45662", reason)
        self.assertIn("already under Bellhaven", reason)

    def test_all_database_proposal_types_have_a_renderable_reason(self) -> None:
        proposal_types = {
            "chow",
            "confident_match",
            "create_account",
            "duplicate",
            "fix_address",
            "needs_review",
            "not_on_website",
            "reparent",
            "update_name",
        }
        for proposal_type in proposal_types:
            with self.subTest(proposal_type=proposal_type):
                proposal = {
                    **self.proposal,
                    "type": proposal_type,
                    "evidence": {
                        "reason": "representative evidence",
                        "name_similarity": 90,
                        "survivor_account_id": "survivor",
                        "lifetime_revenue": 0,
                        "outstanding_ar": 0,
                    },
                    "proposed_field_changes": {
                        "parent_id": PARENT_ACCOUNT_ID,
                        "billing_zip": "45662",
                        "name": "Website name",
                        "create_account": {"parent_id": PARENT_ACCOUNT_ID},
                    },
                }
                reason = _proposal_reason(proposal, self.website, {})
                self.assertTrue(reason.strip())


if __name__ == "__main__":
    unittest.main()
