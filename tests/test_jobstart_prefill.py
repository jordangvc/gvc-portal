"""Job Start prefill — estimate sidecar + builder history ingest layers."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from subsystems.jobstart import ingest
from orchestrators import jobstart_flow


EXAMPLE = Path(__file__).resolve().parents[1] / "example_estimate.json"


class TestFromEstimate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_maps_scope_summary_and_client(self):
        out = ingest.from_estimate(self.sample)
        self.assertIn("Drywall and ACT", out["scope"])
        self.assertEqual(out["builder"], "Maxwell Construction")
        self.assertIn("Dave Sehlhorst", out["supervisor"])
        self.assertEqual(out["gc_pm"], "Dave Sehlhorst")
        self.assertEqual(out["gc_email"], "dsehlhorst@maxwellbuilds.com")

    def test_flattens_scope_details_when_summary_empty(self):
        data = {
            "client": {"name": "Acme"},
            "job": {},
            "estimate": {
                "line_items": [
                    {
                        "description": "Drywall - 5/8 Board",
                        "scope_detail": "5/8\" board on all partitions.\nLevel 5 finish.",
                    },
                    {"description": "ACT", "detail": "2x2 grid throughout."},
                ],
            },
        }
        out = ingest.from_estimate(data)
        self.assertIn("5/8", out["scope"])
        self.assertIn("2x2 grid", out["scope"])

    def test_special_notes_land_in_allowances(self):
        out = ingest.from_estimate(self.sample)
        self.assertIn("5/8", out.get("allowances", ""))
        self.assertNotIn("open_questions", out)

    def test_does_not_invent_exclusions(self):
        out = ingest.from_estimate(self.sample)
        self.assertNotIn("exclusions", out)


class TestFromHistory(unittest.TestCase):
    def test_only_soft_fields(self):
        prior = {
            "scope": "Full house hang",
            "exclusions": "Paint by others",
            "board_count": "400",
            "start_date": "2026-08-01",
            "ceiling_finish": "Knockdown",
            "lock_box": "4417 front door",
        }
        out = ingest.from_history(prior)
        self.assertEqual(out, {
            "ceiling_finish": "Knockdown",
            "lock_box": "4417 front door",
        })


class TestMergePrecedence(unittest.TestCase):
    def test_packet_beats_estimate(self):
        values, sources = ingest.merge(
            packet={"scope": "typed scope"},
            estimate={"scope": "estimate scope", "builder": "From Est"},
        )
        self.assertEqual(values["scope"], "typed scope")
        self.assertEqual(sources["scope"], ingest.SOURCE_PACKET)
        self.assertEqual(values["builder"], "From Est")
        self.assertEqual(sources["builder"], ingest.SOURCE_ESTIMATE)

    def test_estimate_beats_bid(self):
        values, sources = ingest.merge(
            estimate={"builder": "Maxwell Construction", "scope": "est scope"},
            bid={"builder": "Bid Builder", "scope": "bid scope"},
        )
        self.assertEqual(values["builder"], "Maxwell Construction")
        self.assertEqual(values["scope"], "est scope")
        self.assertEqual(sources["builder"], ingest.SOURCE_ESTIMATE)
        self.assertEqual(sources["scope"], ingest.SOURCE_ESTIMATE)

    def test_updates_beat_history(self):
        values, sources = ingest.merge(
            updates={"ceiling_finish": "Smooth"},
            history={"ceiling_finish": "Knockdown"},
        )
        self.assertEqual(values["ceiling_finish"], "Smooth")
        self.assertEqual(sources["ceiling_finish"], ingest.SOURCE_UPDATES)

    def test_history_does_not_fill_scope(self):
        history_layer = ingest.from_history({
            "scope": "old job scope",
            "garage_finish": "Tape only",
        })
        values, sources = ingest.merge(
            history=history_layer,
            bid={"scope": "bid scope"},
        )
        self.assertEqual(values["scope"], "bid scope")
        self.assertEqual(values["garage_finish"], "Tape only")
        self.assertEqual(sources["scope"], ingest.SOURCE_BID)
        self.assertEqual(sources["garage_finish"], ingest.SOURCE_HISTORY)


class TestBackfillBuilder(unittest.TestCase):
    def test_fills_from_naming_standard(self):
        values: dict = {}
        sources: dict = {}
        jobstart_flow.backfill_builder(
            values, sources,
            std={"builder": "Willow Creek", "name": "9195 Silva | Willow Creek"},
            customer="Customer Link",
        )
        self.assertEqual(values["builder"], "Willow Creek")
        self.assertEqual(sources["builder"], "naming")

    def test_falls_back_to_customer(self):
        values: dict = {}
        sources: dict = {}
        jobstart_flow.backfill_builder(
            values, sources, std={"builder": None}, customer="Greg Gavin",
        )
        self.assertEqual(values["builder"], "Greg Gavin")
        self.assertEqual(sources["builder"], ingest.SOURCE_BID)

    def test_does_not_overwrite_existing(self):
        values = {"builder": "Already set"}
        sources = {"builder": ingest.SOURCE_PACKET}
        jobstart_flow.backfill_builder(
            values, sources, std={"builder": "Nope"}, customer="Nope",
        )
        self.assertEqual(values["builder"], "Already set")


if __name__ == "__main__":
    unittest.main()
