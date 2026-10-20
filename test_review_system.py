import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import crm
from review_db import insert_new_proposals, list_proposals


class ReviewDatabaseTests(unittest.TestCase):
    def test_existing_decisions_are_not_reproposed_or_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.db"
            proposal = {
                "proposal_key": "update_name:account:slug",
                "type": "update_name",
                "proposed_field_changes": {"name": "New name"},
            }

            first = insert_new_proposals([proposal], database)
            rows = list_proposals(database_path=database)
            self.assertEqual(first["new"], 1)
            self.assertEqual(rows[0]["status"], "pending")

            from review_db import decide_proposal

            decide_proposal(
                proposal["proposal_key"],
                "rejected",
                "Keep existing name.",
                database,
            )
            changed_payload = {
                **proposal,
                "proposed_field_changes": {"name": "Changed payload"},
            }
            second = insert_new_proposals([changed_payload], database)
            rows = list_proposals(database_path=database)

            self.assertEqual(second["new"], 0)
            self.assertEqual(second["already_decided"], 1)
            self.assertEqual(rows[0]["status"], "rejected")
            self.assertEqual(
                rows[0]["payload"]["proposed_field_changes"]["name"], "New name"
            )
            self.assertIsNotNone(rows[0]["decided_at"])


class CRMApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = {
            "type": "update_name",
            "account_id": "account-1",
            "current_crm_values": {
                "name": "Old name",
                "billing_city": "Columbus",
                "lifetime_revenue": 0,
            },
            "proposed_field_changes": {"name": "New name"},
        }

    @patch("crm.patch_account")
    @patch("crm.get_account")
    def test_changed_snapshot_prevents_any_write(
        self, get_account: unittest.mock.Mock, patch_account: unittest.mock.Mock
    ) -> None:
        get_account.return_value = {
            "account_id": "account-1",
            "name": "Someone else's edit",
            "billing_city": "Columbus",
            "lifetime_revenue": 0,
        }

        with self.assertRaisesRegex(crm.CRMChangedError, "CRM changed since proposal"):
            crm.apply_approved_proposal(self.proposal)

        patch_account.assert_not_called()

    @patch("crm.patch_account")
    @patch("crm.get_account")
    def test_changed_snapshot_ignores_unrelated_fields(
        self, get_account: unittest.mock.Mock, patch_account: unittest.mock.Mock
    ) -> None:
        proposal = {
            **self.proposal,
            "type": "reparent",
            "current_crm_values": {
                "name": "Old name",
                "parent_id": "other-parent",
                "lifetime_revenue": 0,
            },
            "proposed_field_changes": {"parent_id": crm.PARENT_ACCOUNT_ID},
        }
        get_account.side_effect = [
            {"account_id": "account-1", "name": "Updated name", "parent_id": "other-parent", "lifetime_revenue": 0},
            {"account_id": "account-1", "parent_id": crm.PARENT_ACCOUNT_ID},
        ]

        result = crm.apply_approved_proposal(proposal)

        patch_account.assert_called_once_with("account-1", {"parent_id": crm.PARENT_ACCOUNT_ID})
        self.assertIn("verified", result)

    @patch("crm.patch_account")
    @patch("crm.get_account")
    def test_update_patches_only_proposed_fields_and_verifies(
        self, get_account: unittest.mock.Mock, patch_account: unittest.mock.Mock
    ) -> None:
        get_account.side_effect = [
            {
                "account_id": "account-1",
                "name": "Old name",
                "billing_city": "Columbus",
                "lifetime_revenue": 0,
            },
            {"account_id": "account-1", "name": "New name"},
        ]

        result = crm.apply_approved_proposal(self.proposal)

        patch_account.assert_called_once_with("account-1", {"name": "New name"})
        self.assertIn("verified", result)

    @patch("crm.patch_account")
    @patch("crm.get_account")
    def test_fix_address_updates_only_billing_zip(
        self, get_account: unittest.mock.Mock, patch_account: unittest.mock.Mock
    ) -> None:
        proposal = {
            **self.proposal,
            "type": "fix_address",
            "current_crm_values": {
                "name": "Old name",
                "billing_street": "2222 Gallia St",
                "billing_city": "Portsmouth",
                "billing_state": "OH",
                "billing_zip": "45626",
                "lifetime_revenue": 0,
            },
            "proposed_field_changes": {"billing_zip": "45662"},
        }
        get_account.side_effect = [
            {
                "account_id": "account-1",
                "name": "Old name",
                "billing_street": "2222 Gallia St",
                "billing_city": "Portsmouth",
                "billing_state": "OH",
                "billing_zip": "45626",
                "lifetime_revenue": 0,
            },
            {"account_id": "account-1", "billing_zip": "45662"},
        ]

        result = crm.apply_approved_proposal(proposal)

        patch_account.assert_called_once_with("account-1", {"billing_zip": "45662"})
        self.assertIn("verified", result)

    @patch("crm.patch_account")
    @patch("crm.get_account")
    def test_fix_address_can_add_street_while_preserving_po_box(
        self, get_account: unittest.mock.Mock, patch_account: unittest.mock.Mock
    ) -> None:
        combined_street = "PO Box 517, 3156 W Prospect Rd"
        proposal = {
            **self.proposal,
            "type": "fix_address",
            "current_crm_values": {
                "name": "Bellhaven of Ashtabula",
                "billing_street": "PO Box 517",
                "billing_city": "Ashtabula",
                "billing_state": "OH",
                "billing_zip": "44004",
                "lifetime_revenue": 0,
            },
            "proposed_field_changes": {"billing_street": combined_street},
        }
        get_account.side_effect = [
            {
                "account_id": "account-1",
                "name": "Bellhaven of Ashtabula",
                "billing_street": "PO Box 517",
                "billing_city": "Ashtabula",
                "billing_state": "OH",
                "billing_zip": "44004",
                "lifetime_revenue": 0,
            },
            {"account_id": "account-1", "billing_street": combined_street},
        ]

        result = crm.apply_approved_proposal(proposal)

        patch_account.assert_called_once_with(
            "account-1", {"billing_street": combined_street}
        )
        self.assertIn("verified", result)

    @patch("crm.patch_account")
    @patch("crm.create_account")
    @patch("crm.get_account")
    def test_chow_changes_only_old_accounts_chow_link(
        self,
        get_account: unittest.mock.Mock,
        create_account: unittest.mock.Mock,
        patch_account: unittest.mock.Mock,
    ) -> None:
        proposal = {
            "type": "chow",
            "account_id": "old-account",
            "current_crm_values": {
                "name": "Old name",
                "parent_id": "other-parent",
            },
            "proposed_field_changes": {
                "create_account": {
                    "name": "New account",
                    "billing_street": "1 Main St",
                    "billing_city": "Columbus",
                    "billing_state": "OH",
                    "billing_zip": "43215",
                    "care_type": "Assisted Living",
                    "parent_id": "incorrect-parent",
                },
                "set_existing_account": {
                    "chow_current_account": "<new_account_id>"
                },
            },
        }
        create_account.return_value = "new-account"

        def account_by_id(account_id: str) -> dict[str, object]:
            if account_id == "new-account":
                return {
                    "account_id": "new-account",
                    "name": "New account",
                    "billing_street": "1 Main St",
                    "billing_city": "Columbus",
                    "billing_state": "OH",
                    "billing_zip": "43215",
                    "care_type": "Assisted Living",
                    "parent_id": crm.PARENT_ACCOUNT_ID,
                }
            if patch_account.called:
                return {
                    "account_id": "old-account",
                    "name": "Old name",
                    "parent_id": "other-parent",
                    "chow_current_account": "new-account",
                }
            return {
                "account_id": "old-account",
                "name": "Old name",
                "parent_id": "other-parent",
            }

        get_account.side_effect = account_by_id

        result = crm.apply_approved_proposal(proposal)

        self.assertEqual(create_account.call_args.args[0]["parent_id"], crm.PARENT_ACCOUNT_ID)
        patch_account.assert_called_once_with(
            "old-account", {"chow_current_account": "new-account"}
        )
        self.assertIn("linked old account", result)


if __name__ == "__main__":
    unittest.main()
