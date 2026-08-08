"""Job Start detail: lite (no Drive) first paint + parallel Drive when full."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrators import jobstart_flow  # noqa: E402


class TestHandoffDetailLite(unittest.TestCase):
    def test_lite_skips_drive_and_flags_pending(self):
        bid = {
            "item_id": 42,
            "name": "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek",
            "url": "https://monday.test/42",
            "group_title": "Won",
            "stage": "Accepted",
            "stage_state": "accepted",
            "prefill": {"builder": "Willow Creek", "scope": "Hang only"},
            "context": {"customer": "Willow Creek", "location": "Cincinnati"},
            "existing_project_ids": [],
            "existing_ops_ids": [],
            "copy": {},
        }

        with patch("adapters.monday.client.MondayClient") as MC, \
             patch("adapters.monday.jobstart.get_bid_detail", return_value=bid), \
             patch("adapters.monday.jobstart.get_field_labels", return_value={}), \
             patch("subsystems.jobstart.drafts.get_draft", return_value=None), \
             patch.object(jobstart_flow, "_scope_review_values") as scope_fn, \
             patch.object(jobstart_flow, "_estimate_values") as est_fn, \
             patch.object(jobstart_flow, "_update_values", return_value={}), \
             patch.object(jobstart_flow, "_history_values", return_value={}):
            MC.return_value = MagicMock()
            out = jobstart_flow.get_handoff_detail(42, "a@x.com", include_drive=False)

        self.assertTrue(out["ok"])
        self.assertTrue(out["drive_pending"])
        self.assertEqual(out["values"].get("builder"), "Willow Creek")
        scope_fn.assert_not_called()
        est_fn.assert_not_called()

    def test_full_calls_drive_helpers(self):
        bid = {
            "item_id": 42,
            "name": "9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek",
            "url": "https://monday.test/42",
            "group_title": "Won",
            "stage": "Accepted",
            "stage_state": "accepted",
            "prefill": {"builder": "Willow Creek"},
            "context": {"customer": "Willow Creek", "location": "Cincinnati"},
            "existing_project_ids": [],
            "existing_ops_ids": [],
            "copy": {},
        }

        with patch("adapters.monday.client.MondayClient") as MC, \
             patch("adapters.monday.jobstart.get_bid_detail", return_value=bid), \
             patch("adapters.monday.jobstart.get_field_labels", return_value={}), \
             patch("subsystems.jobstart.drafts.get_draft", return_value=None), \
             patch.object(
                 jobstart_flow, "_scope_review_values",
                 return_value=({"exclusions": "Paint by others"},
                               {"found": True, "name": "Scope Review"}),
             ) as scope_fn, \
             patch.object(
                 jobstart_flow, "_estimate_values",
                 return_value=({}, {"found": False}),
             ) as est_fn, \
             patch.object(jobstart_flow, "_update_values", return_value={}), \
             patch.object(jobstart_flow, "_history_values", return_value={}):
            MC.return_value = MagicMock()
            out = jobstart_flow.get_handoff_detail(42, "a@x.com", include_drive=True)

        self.assertFalse(out["drive_pending"])
        self.assertEqual(out["values"].get("exclusions"), "Paint by others")
        self.assertTrue(out["scope_review"].get("found"))
        scope_fn.assert_called_once()
        est_fn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
