"""Pure contract tests for the replacement GraphRAG path."""

from __future__ import annotations

import importlib.util
import json
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("graphrag_build", ROOT / "hpc/graphrag_build.py")
assert SPEC and SPEC.loader
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)
APPROVAL_SPEC = importlib.util.spec_from_file_location(
    "tool_approval", ROOT / "lambda/app/domain/tool_approval.py"
)
assert APPROVAL_SPEC and APPROVAL_SPEC.loader
APPROVAL = importlib.util.module_from_spec(APPROVAL_SPEC)
APPROVAL_SPEC.loader.exec_module(APPROVAL)

CHAT_ID = "chat-12345678-1234-1234-1234-123456789abc"
QUERY = "Identify the three most important anomalous HDFS log behaviors and give me an action plan."


def return_control_input(function: str, parameters: dict[str, str]) -> dict:
    return {
        "functionInvocationInput": {
            "actionGroup": "GraphRAGReadTools",
            "function": function,
            "parameters": [
                {"name": name, "type": "string", "value": value}
                for name, value in parameters.items()
            ],
        }
    }


class GraphRagBuildContractTests(unittest.TestCase):
    def test_nova_vectors_are_unit_normalized(self) -> None:
        vector = BUILD.l2_normalize([3.0, 4.0], 2)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in vector)), 1.0, places=7)

    def test_invalid_embeddings_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            BUILD.l2_normalize([1.0], 2)
        with self.assertRaises(ValueError):
            BUILD.l2_normalize([0.0, 0.0], 2)
        with self.assertRaises(ValueError):
            BUILD.l2_normalize([float("nan"), 1.0], 2)

    def test_vector_mapping_is_faiss_hnsw_inner_product(self) -> None:
        mapping = BUILD.opensearch_mapping(1024, 24, 1)
        settings = mapping["settings"]["index"]
        vector = mapping["mappings"]["properties"]["embedding"]
        self.assertTrue(settings["knn"])
        self.assertTrue(settings["knn.remote_index_build.enabled"])
        self.assertEqual(vector["dimension"], 1024)
        self.assertEqual(vector["space_type"], "innerproduct")
        self.assertEqual(vector["method"]["engine"], "faiss")
        self.assertEqual(vector["method"]["name"], "hnsw")

    def test_labels_never_enter_online_mappings(self) -> None:
        serialized = json.dumps(
            {
                "patterns": BUILD.opensearch_mapping(1024, 24, 1),
                "records": BUILD.record_mapping(24, 1),
            }
        )
        self.assertNotIn("evaluation_label", serialized)


class GraphRagToolContractTests(unittest.TestCase):
    def test_contracts_match_registry_and_terraform(self) -> None:
        schema_dir = ROOT / "contracts/graphrag-tools"
        schemas = [json.loads(path.read_text()) for path in sorted(schema_dir.glob("*.schema.json"))]
        names = {schema["title"] for schema in schemas}
        expected = {
            "search_log_events",
            "query_hdfs_graph",
            "get_anomaly_evidence",
            "correlate_block_failures",
            "analyze_node_behavior",
            "rank_anomalies",
            "generate_remediation_plan",
            "validate_remediation",
        }
        self.assertEqual(names, expected)
        registry = json.loads((ROOT / "contracts/tool-registry.json").read_text())
        registered = {tool["name"] for tool in registry["tools"]}
        self.assertTrue(expected <= registered)
        terraform = (ROOT / "infra/modules/agentic/main.tf").read_text()
        for name in expected:
            self.assertGreaterEqual(terraform.count(f'name        = "{name}"'), 2)
        for schema in schemas:
            self.assertFalse(schema["additionalProperties"])

    def test_ui_enforces_requested_layout_and_public_trace(self) -> None:
        css = (ROOT / "ui/src/styles.css").read_text()
        jsx = (ROOT / "ui/src/main.tsx").read_text()
        self.assertIn("grid-template-columns: minmax(280px, 25%) minmax(0, 75%)", css)
        self.assertIn("Public reasoning trace", jsx)
        self.assertIn("No private chain-of-thought", jsx)
        self.assertIn("Top 3 anomaly findings", jsx)
        self.assertIn("Approve the Agent’s MCP tool plan?", jsx)
        self.assertIn("/tool-decision", jsx)
        self.assertIn("status === 'AWAITING_APPROVAL') return", jsx)

    def test_managed_acquisition_and_authenticated_streaming(self) -> None:
        analytics = (ROOT / "infra/modules/analytics/main.tf").read_text()
        stage = (ROOT / "scripts/stage_hf_data.sh").read_text()
        realtime = (ROOT / "infra/modules/realtime/main.tf").read_text()
        ui = (ROOT / "ui/src/main.tsx").read_text()
        self.assertIn('resource "aws_codebuild_project" "acquisition"', analytics)
        self.assertIn("codebuild start-build", stage)
        self.assertNotIn("hf download", stage)
        self.assertIn("AMAZON_COGNITO_USER_POOLS", realtime)
        self.assertNotIn("API_KEY", realtime)
        self.assertIn("fetchAuthSession", ui)
        self.assertIn("/sessions/${currentUser.userId}/${chatId}", ui)

    def test_graphrag_target_is_a_separate_read_only_lambda(self) -> None:
        terraform = (ROOT / "infra/modules/agentic/main.tf").read_text()
        legacy = (ROOT / "lambda/app/handlers/mcp_tool_handler.py").read_text()
        graph = (ROOT / "lambda/app/handlers/graphrag_tool_handler.py").read_text()
        self.assertIn('handler          = "handlers.graphrag_tool_handler.lambda_handler"', terraform)
        self.assertIn('resource "aws_iam_role_policy" "graphrag_tool_data"', terraform)
        self.assertNotIn('"rank_anomalies"', legacy)
        self.assertIn('"rank_anomalies"', graph)

    def test_graph_is_run_isolated_and_publish_is_manifest_gated(self) -> None:
        build = (ROOT / "hpc/graphrag_build.py").read_text()
        self.assertIn('run_prefix = f"run:{args.run_id}:"', build)
        self.assertIn('F.lit("SourceRecord")', build)
        self.assertIn('F.lit("Anomaly")', build)
        self.assertIn('F.lit("Host")', build)
        self.assertIn('F.lit("DataNode")', build)
        self.assertIn('F.lit("Process")', build)
        self.assertIn('"AFFECTS_HOST"', build)
        self.assertIn('"status": "PUBLISHED"', build)
        self.assertIn('manifests/published', build)

    def test_managed_ingestion_has_singleflight_and_fail_closed_gates(self) -> None:
        ingestion = (ROOT / "infra/modules/ingestion/main.tf").read_text()
        gate = (ROOT / "lambda/app/handlers/pipeline_gate_handler.py").read_text()
        runner = (ROOT / "scripts/run_100g_benchmark.sh").read_text()
        self.assertIn("AcquireLease", ingestion)
        self.assertIn("ValidateGeneratedCorpus", ingestion)
        self.assertIn("ValidatePublishedGraphRAG", ingestion)
        self.assertIn("UnlockAfterFailure", ingestion)
        self.assertIn('"workflow_id": "lock#graphrag-build"', gate)
        self.assertIn("MINIMUM_BYTES", gate)
        self.assertIn('len(smoke.get("findings", [])) != 3', gate)
        self.assertIn("stepfunctions start-execution", runner)

    def test_cognito_users_and_group_approval_are_deployment_managed(self) -> None:
        identity = (ROOT / "infra/modules/identity/main.tf").read_text()
        provision = (ROOT / "scripts/provision_cognito_user.sh").read_text()
        deploy = (ROOT / "scripts/deploy_e2e.sh").read_text()
        api = (ROOT / "lambda/app/handlers/api_handler.py").read_text()
        self.assertIn("admin_create_user_config", identity)
        self.assertIn('name         = "approver"', identity)
        self.assertIn('name         = "admin"', identity)
        self.assertIn("admin-create-user", provision)
        self.assertIn("admin-remove-user-from-group", provision)
        self.assertIn("INITIAL_OPERATOR_EMAIL", deploy)
        self.assertIn('{"approver", "admin"}', api)

    def test_destroy_command_has_account_region_and_confirmation_gates(self) -> None:
        script = (ROOT / "scripts/destroy_all.sh").read_text()
        self.assertIn("EXPECTED_AWS_ACCOUNT_ID", script)
        self.assertIn("EXPECTED_AWS_REGION", script)
        self.assertIn("CONFIRM_DESTROY", script)
        self.assertIn("terraform -chdir=\"$repo_dir/infra\" plan -destroy", script)
        self.assertIn("terraform -chdir=\"$repo_dir/infra\" state list", script)


class GraphRagHumanApprovalTests(unittest.TestCase):
    def test_return_control_plan_is_hash_bound_and_executable_only_before_expiry(self) -> None:
        plan = APPROVAL.build_tool_plan(
            chat_id=CHAT_ID,
            operator_query=QUERY,
            approval_round=1,
            invocation_id="invocation-1",
            invocation_inputs=[
                return_control_input("rank_anomalies", {"query": QUERY, "top_k": "3"})
            ],
            created_at=100,
        )
        self.assertEqual(plan["calls"][0]["authority"], "read-only")
        self.assertEqual(plan["calls"][0]["arguments"]["top_k"], "3")
        self.assertEqual(
            APPROVAL.validate_tool_plan(
                plan, chat_id=CHAT_ID, operator_query=QUERY, now_epoch=200
            )["plan_hash"],
            plan["plan_hash"],
        )
        with self.assertRaisesRegex(ValueError, "expired"):
            APPROVAL.validate_tool_plan(
                plan, chat_id=CHAT_ID, operator_query=QUERY, now_epoch=701
            )

    def test_plan_tampering_and_query_rewriting_fail_closed(self) -> None:
        plan = APPROVAL.build_tool_plan(
            chat_id=CHAT_ID,
            operator_query=QUERY,
            approval_round=1,
            invocation_id="invocation-2",
            invocation_inputs=[
                return_control_input("rank_anomalies", {"query": QUERY, "top_k": "3"})
            ],
            created_at=100,
        )
        plan["calls"][0]["arguments"]["top_k"] = "2"
        with self.assertRaisesRegex(ValueError, "hash"):
            APPROVAL.validate_tool_plan(plan, chat_id=CHAT_ID, operator_query=QUERY)
        with self.assertRaisesRegex(ValueError, "exactly match"):
            APPROVAL.build_tool_plan(
                chat_id=CHAT_ID,
                operator_query=QUERY,
                approval_round=1,
                invocation_id="invocation-3",
                invocation_inputs=[
                    return_control_input(
                        "rank_anomalies", {"query": "rewritten request", "top_k": "3"}
                    )
                ],
                created_at=100,
            )

    def test_first_round_requires_rank_and_rejects_non_graphrag_tools(self) -> None:
        with self.assertRaisesRegex(ValueError, "must include rank_anomalies"):
            APPROVAL.build_tool_plan(
                chat_id=CHAT_ID,
                operator_query=QUERY,
                approval_round=1,
                invocation_id="invocation-4",
                invocation_inputs=[
                    return_control_input("search_log_events", {"query": QUERY, "mode": "hybrid"})
                ],
                created_at=100,
            )
        with self.assertRaisesRegex(ValueError, "non-GraphRAG"):
            APPROVAL.build_tool_plan(
                chat_id=CHAT_ID,
                operator_query=QUERY,
                approval_round=1,
                invocation_id="invocation-5",
                invocation_inputs=[return_control_input("execute_remediation", {})],
                created_at=100,
            )

    def test_converse_tool_use_replaces_return_control_without_weakening_the_gate(self) -> None:
        """The mechanism changed; the guarantee did not.

        Bedrock Agents entered maintenance mode, so RETURN_CONTROL is gone. What
        must still hold is that the model only proposes, the proposal is
        hash-bound and validated, and MCP executes strictly after approval.
        """
        chat = (ROOT / "lambda/app/handlers/chat_handler.py").read_text()
        api = (ROOT / "infra/modules/api/main.tf").read_text()

        # The model proposes through Converse; the retired agent runtime is gone.
        self.assertIn("bedrock.converse(", chat)
        self.assertNotIn("invoke_agent", chat)
        self.assertNotIn("bedrock-agent-runtime", chat)

        # Approval strictly precedes any MCP call, and the exact-hash check remains.
        self.assertLess(chat.index("validate_tool_plan"), chat.index("mcp_client.call_tool"))
        self.assertIn("Exact MCP tool-plan approval is missing or does not match", chat)
        self.assertIn('"POST /api/chats/{id}/tool-decision"', api)

        # The transcript the approval was derived from cannot be swapped afterwards.
        self.assertIn("Stored transcript does not match the approved conversation", chat)

    def test_legacy_agent_path_is_optional_and_off_by_default(self) -> None:
        variables = (ROOT / "infra/modules/agentic/variables.tf").read_text()
        root = (ROOT / "infra/main.tf").read_text()
        self.assertIn('variable "enable_bedrock_agents"', variables)
        block = variables.split('variable "enable_bedrock_agents"', maxsplit=1)[1]
        self.assertRegex(block.split("}", maxsplit=1)[0], r"default\s*=\s*false")
        # Neither runtime path may depend on an agent that cannot be created.
        self.assertNotIn("module.agentic.graphrag_agent_id", root)
        self.assertNotIn("module.agentic.agent_alias_arn", root)

    def test_graphrag_tools_stay_out_of_the_write_capable_remediation_path(self) -> None:
        legacy = (ROOT / "lambda/app/handlers/mcp_tool_handler.py").read_text()
        for name in (
            "search_log_events",
            "query_hdfs_graph",
            "get_anomaly_evidence",
            "correlate_block_failures",
            "analyze_node_behavior",
            "rank_anomalies",
            "generate_remediation_plan",
            "validate_remediation",
        ):
            self.assertNotIn(f'"{name}"', legacy)


if __name__ == "__main__":
    unittest.main()
