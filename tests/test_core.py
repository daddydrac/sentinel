import sys
import json
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "lambda" / "app"
sys.path.insert(0, str(APP_DIR))
from domain import core  # noqa: E402
from handlers import mcp_tool_handler  # noqa: E402
from handlers import graphrag_tool_handler  # noqa: E402


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.scenarios = core.load_scenarios()

    def test_three_demo_scenarios_exist(self):
        self.assertEqual(
            {"storage_path_regression", "firmware_drift_diagnosis", "verification_failure"},
            set(self.scenarios),
        )

    def test_tool_registry_matches_runtime_implementations(self):
        registry_path = APP_DIR.parents[1] / "contracts" / "tool-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(mcp_tool_handler.TOOL_ARGUMENTS) | set(graphrag_tool_handler.TOOL_ARGUMENTS),
            {tool["name"] for tool in registry["tools"]},
        )

    def test_hybrid_retrieval_contains_four_evidence_branches(self):
        evidence = core.retrieve(self.scenarios["storage_path_regression"])
        self.assertGreaterEqual(len(evidence["lexical"]), 1)
        self.assertGreaterEqual(len(evidence["vector"]), 1)
        self.assertGreaterEqual(len(evidence["graph"]), 1)
        self.assertGreaterEqual(len(evidence["live"]), 1)
        self.assertEqual(64, len(evidence["manifest_hash"]))

    def test_manifest_hash_is_stable_across_assembly_times(self):
        scenario = self.scenarios["storage_path_regression"]
        first = core.retrieve(scenario)
        second = core.retrieve(scenario)
        second["assembled_at"] = "later"
        self.assertEqual(first["manifest_hash"], second["manifest_hash"])

    def test_plan_hash_is_stable_and_bound_to_evidence(self):
        scenario = self.scenarios["storage_path_regression"]
        evidence = core.retrieve(scenario)
        first = core.build_plan(scenario, evidence)
        second = core.build_plan(scenario, evidence)
        self.assertEqual(first["plan_hash"], second["plan_hash"])
        mutated = dict(evidence)
        mutated["manifest_hash"] = "changed"
        third = core.build_plan(scenario, mutated)
        self.assertNotEqual(first["plan_hash"], third["plan_hash"])

    def test_policy_requires_approval_for_reversible_write(self):
        scenario = self.scenarios["storage_path_regression"]
        plan = core.build_plan(scenario, core.retrieve(scenario))
        self.assertEqual("REQUIRE_APPROVAL", core.decide_policy(plan)["decision"])

    def test_policy_allows_read_only_investigation(self):
        scenario = self.scenarios["firmware_drift_diagnosis"]
        plan = core.build_plan(scenario, core.retrieve(scenario))
        self.assertEqual("ALLOW", core.decide_policy(plan)["decision"])

    def test_failed_verification_has_compensation(self):
        scenario = self.scenarios["verification_failure"]
        evidence = core.retrieve(scenario)
        plan = core.build_plan(scenario, evidence)
        receipt = core.execute_simulated(scenario, plan, "wf-test")
        verification = core.verify_simulated(scenario, receipt)
        self.assertFalse(verification["passed"])
        compensation = core.compensate_simulated(scenario, plan, "wf-test")
        self.assertEqual("restore_previous_replica", compensation["action"])

    def test_mcp_read_tool_returns_only_requested_branch(self):
        result = mcp_tool_handler.lambda_handler(
            {"_tool": "search_exact_errors", "scenario_id": "storage_path_regression"}, None
        )
        self.assertEqual("lexical", result["retrieval_mode"])
        self.assertGreaterEqual(len(result["results"]), 1)

    def test_mcp_closed_schema_rejects_undeclared_argument(self):
        with self.assertRaises(ValueError):
            mcp_tool_handler.lambda_handler(
                {
                    "_tool": "search_exact_errors",
                    "scenario_id": "storage_path_regression",
                    "shell_command": "anything",
                },
                None,
            )

    def test_mcp_execute_revalidates_plan_hash_and_allowlist(self):
        scenario = self.scenarios["storage_path_regression"]
        plan = core.build_plan(scenario, core.retrieve(scenario))
        policy = core.decide_policy(plan)
        result = mcp_tool_handler.lambda_handler(
            {
                "_tool": "execute_remediation",
                "scenario_id": scenario["id"],
                "workflow_id": "wf-mcp-test",
                "plan_json": json.dumps(plan),
                "policy_json": json.dumps(policy),
                "approval_json": json.dumps(
                    {"approved": True, "plan_hash": plan["plan_hash"]}
                ),
            },
            None,
        )
        self.assertTrue(result["accepted"])
        self.assertEqual("drain_node", result["tool"])
        self.assertEqual(plan["plan_hash"], result["authority"]["approved_plan_hash"])

    def test_mcp_execute_rejects_tampered_plan(self):
        scenario = self.scenarios["storage_path_regression"]
        plan = core.build_plan(scenario, core.retrieve(scenario))
        plan["target"] = "unapproved-target"
        with self.assertRaises(ValueError):
            mcp_tool_handler.lambda_handler(
                {
                    "_tool": "execute_remediation",
                    "scenario_id": scenario["id"],
                    "workflow_id": "wf-mcp-test",
                    "plan_json": json.dumps(plan),
                    "policy_json": json.dumps(core.decide_policy(plan)),
                    "approval_json": json.dumps(
                        {"approved": True, "plan_hash": plan["plan_hash"]}
                    ),
                },
                None,
            )

    def test_mcp_execute_rejects_rehashed_argument_expansion(self):
        scenario = self.scenarios["storage_path_regression"]
        plan = core.build_plan(scenario, core.retrieve(scenario))
        plan["arguments"] = {"mode": "all", "max_workloads": 1000}
        plan.pop("plan_hash")
        plan["plan_hash"] = core.canonical_hash(plan)
        with self.assertRaises(ValueError):
            mcp_tool_handler.lambda_handler(
                {
                    "_tool": "execute_remediation",
                    "scenario_id": scenario["id"],
                    "workflow_id": "wf-mcp-test",
                    "plan_json": json.dumps(plan),
                    "policy_json": json.dumps(core.decide_policy(plan)),
                    "approval_json": json.dumps(
                        {"approved": True, "plan_hash": plan["plan_hash"]}
                    ),
                },
                None,
            )

    def test_mcp_execute_rejects_rehashed_extra_plan_field(self):
        scenario = self.scenarios["storage_path_regression"]
        plan = core.build_plan(scenario, core.retrieve(scenario))
        plan["shell_command"] = "anything"
        plan.pop("plan_hash")
        plan["plan_hash"] = core.canonical_hash(plan)
        with self.assertRaises(ValueError):
            mcp_tool_handler.lambda_handler(
                {
                    "_tool": "execute_remediation",
                    "scenario_id": scenario["id"],
                    "workflow_id": "wf-mcp-test",
                    "plan_json": json.dumps(plan),
                    "policy_json": json.dumps(core.decide_policy(plan)),
                    "approval_json": json.dumps(
                        {"approved": True, "plan_hash": plan["plan_hash"]}
                    ),
                },
                None,
            )

    def test_mcp_execute_requires_exact_plan_approval(self):
        scenario = self.scenarios["storage_path_regression"]
        plan = core.build_plan(scenario, core.retrieve(scenario))
        with self.assertRaises(ValueError):
            mcp_tool_handler.lambda_handler(
                {
                    "_tool": "execute_remediation",
                    "scenario_id": scenario["id"],
                    "workflow_id": "wf-mcp-test",
                    "plan_json": json.dumps(plan),
                    "policy_json": json.dumps(core.decide_policy(plan)),
                    "approval_json": json.dumps(
                        {"approved": True, "plan_hash": "tampered"}
                    ),
                },
                None,
            )

    def test_execution_retry_keeps_one_logical_action(self):
        scenario = self.scenarios["storage_path_regression"]
        plan = core.build_plan(scenario, core.retrieve(scenario))
        first = core.execute_simulated(scenario, plan, "wf-idempotent")
        second = core.execute_simulated(scenario, plan, "wf-idempotent")
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])

    def test_verifier_rejects_unreceipted_success_claim(self):
        with self.assertRaises(ValueError):
            mcp_tool_handler.lambda_handler(
                {
                    "_tool": "verify_remediation",
                    "scenario_id": "storage_path_regression",
                    "receipt_json": json.dumps({"accepted": True}),
                },
                None,
            )


if __name__ == "__main__":
    unittest.main()
