"""The finding narrative must be derived from the sequence, not a fixed string.

The original implementation matched English keywords ("disk", "network") against
a pattern text that is a Drain template sequence of integers. No branch could
match, so every finding in every run received the same probable cause and the
same action plan.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambda" / "app"))

from adapters.graphrag import _action_plan, _probable_cause, sequence_profile  # noqa: E402


# Shapes taken from the indexed corpus: a run-dominated block, a tight cycle,
# and a varied multi-stage block.
RUN_DOMINATED = "0 2 3 0 2 3 0 2 3 1 1 1 6".split() + ["7"] * 40
CYCLIC = ["4", "5"] * 12
HIGH_VARIETY = [str(code) for code in range(12)]


class SequenceProfileTests(unittest.TestCase):
    def test_dominant_template_and_unbroken_run_are_measured(self) -> None:
        profile = sequence_profile(RUN_DOMINATED)
        self.assertEqual(profile["dominant_code"], "7")
        self.assertEqual(profile["dominant_count"], 40)
        self.assertEqual(profile["longest_run"], 40)
        self.assertEqual(profile["total_events"], len(RUN_DOMINATED))

    def test_a_head_cycle_is_detected_and_counted(self) -> None:
        self.assertEqual(sequence_profile(RUN_DOMINATED)["repeated_cycle"], "0->2->3")
        self.assertEqual(sequence_profile(RUN_DOMINATED)["repeated_cycle_count"], 3)
        self.assertEqual(sequence_profile(CYCLIC)["repeated_cycle"], "4->5")

    def test_an_empty_sequence_yields_no_claims(self) -> None:
        profile = sequence_profile([])
        self.assertEqual(profile["total_events"], 0)
        self.assertIsNone(profile["dominant_code"])


class ProbableCauseTests(unittest.TestCase):
    def test_distinct_sequences_produce_distinct_causes(self) -> None:
        """The defect: three different blocks returned one identical sentence."""
        causes = {
            _probable_cause({"event_codes": codes})
            for codes in (RUN_DOMINATED, CYCLIC, HIGH_VARIETY)
        }
        self.assertEqual(len(causes), 3)

    def test_the_cause_cites_measured_quantities_from_the_sequence(self) -> None:
        cause = _probable_cause({"event_codes": RUN_DOMINATED})
        self.assertIn("Template 7", cause)
        self.assertIn("40", cause)  # the unbroken run length
        self.assertIn("0->2->3", cause)  # the opening cycle

    def test_no_semantic_meaning_is_invented_for_a_template_id(self) -> None:
        """The dataset ships no template legend, so naming operations would be fabrication."""
        cause = _probable_cause({"event_codes": RUN_DOMINATED}).lower()
        for invented in ("packetresponder", "namenode", "datanode", "disk", "socket"):
            self.assertNotIn(invented, cause)
        self.assertIn("ships no legend", cause)

    def test_ranking_scores_are_reported_and_labels_disclaimed(self) -> None:
        cause = _probable_cause(
            {"event_codes": CYCLIC, "rarity_score": 0.91, "structural_score": 0.77}
        )
        self.assertIn("0.910", cause)
        self.assertIn("0.770", cause)
        self.assertIn("not on the dataset label", cause)

    def test_an_unindexed_sequence_says_so_instead_of_guessing(self) -> None:
        self.assertIn("no cause can be derived", _probable_cause({"event_codes": []}))


class ActionPlanTests(unittest.TestCase):
    def test_the_plan_names_the_dominant_template_and_cited_blocks(self) -> None:
        plan = _action_plan({"event_codes": RUN_DOMINATED, "affected_blocks": ["blk_-55657892"]})
        self.assertEqual(len(plan), 3)
        self.assertIn("template 7", plan[0].lower())
        self.assertIn("blk_-55657892", plan[0])

    def test_distinct_sequences_produce_distinct_plans(self) -> None:
        plans = {
            tuple(_action_plan({"event_codes": codes}))
            for codes in (RUN_DOMINATED, CYCLIC, HIGH_VARIETY)
        }
        self.assertEqual(len(plans), 3)

    def test_every_plan_still_routes_through_the_approval_workflow(self) -> None:
        for codes in (RUN_DOMINATED, CYCLIC, HIGH_VARIETY, []):
            plan = _action_plan({"event_codes": codes})
            self.assertIn("exact-plan approval", " ".join(plan))


if __name__ == "__main__":
    unittest.main()
