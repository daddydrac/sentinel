"""AgentCore Gateway Lambda target implementing governed remediation tools."""

from __future__ import annotations

import json
from typing import Any

from domain import core


TOOL_ARGUMENTS = {
    "search_exact_errors": {"scenario_id"},
    "find_similar_incidents": {"scenario_id"},
    "inspect_dependency_graph": {"scenario_id"},
    "get_live_state": {"scenario_id"},
    "retrieve_hybrid_context": {"scenario_id"},
    "execute_remediation": {
        "scenario_id",
        "workflow_id",
        "plan_json",
        "policy_json",
        "approval_json",
    },
    "verify_remediation": {"scenario_id", "receipt_json"},
    "compensate_remediation": {
        "scenario_id",
        "workflow_id",
        "plan_json",
        "policy_json",
        "approval_json",
    },
}

PLAN_FIELDS = {
    "schema_version",
    "planner_mode",
    "hypothesis",
    "tool",
    "target",
    "arguments",
    "risk",
    "expected_outcome",
    "rollback",
    "verification",
    "evidence_ids",
    "evidence_manifest_hash",
    "assumptions",
    "expires_in_seconds",
    "agent_analysis",
    "agent_tool_calls",
    "plan_hash",
}
REQUIRED_PLAN_FIELDS = PLAN_FIELDS - {"agent_analysis", "agent_tool_calls"}


def _tool_name(context: Any, event: dict[str, Any]) -> str:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    visible = custom.get("bedrockAgentCoreToolName", event.get("_tool", ""))
    return visible.split("___", 1)[-1]


def _scenario(scenario_id: str) -> dict[str, Any]:
    scenarios = core.load_scenarios()
    if scenario_id not in scenarios:
        raise ValueError(f"Unknown scenario_id: {scenario_id}")
    return scenarios[scenario_id]


def _validated_plan(raw: str, scenario: dict[str, Any]) -> dict[str, Any]:
    plan = json.loads(raw)
    unexpected = set(plan) - PLAN_FIELDS
    missing = REQUIRED_PLAN_FIELDS - set(plan)
    if unexpected or missing:
        raise ValueError(
            f"Closed plan schema rejected unexpected={sorted(unexpected)} missing={sorted(missing)}"
        )
    supplied_hash = plan.pop("plan_hash", "")
    if supplied_hash != core.canonical_hash(plan):
        raise ValueError("Plan hash does not match the canonical MCP payload")
    plan["plan_hash"] = supplied_hash
    expected = scenario["proposed_action"]
    governed_fields = ("tool", "target", "arguments", "risk", "expected_outcome", "rollback")
    if any(plan.get(field) != expected[field] for field in governed_fields):
        raise ValueError("Plan differs from the governed scenario tool, target, arguments, risk, or rollback")
    evidence = core.retrieve(scenario)
    if plan.get("evidence_manifest_hash") != evidence["manifest_hash"]:
        raise ValueError("Plan is not bound to the current governed evidence manifest")
    return plan


def _validated_authority(
    plan: dict[str, Any], policy_raw: str, approval_raw: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = json.loads(policy_raw)
    approval = json.loads(approval_raw or "{}")
    expected_policy = core.decide_policy(plan)
    if policy.get("decision") != expected_policy["decision"]:
        raise ValueError("Supplied policy decision does not match deterministic policy")
    if policy.get("policy_version") != expected_policy["policy_version"]:
        raise ValueError("Unsupported policy version")
    if policy["decision"] == "DENY":
        raise ValueError("Policy denied this plan")
    if policy["decision"] == "REQUIRE_APPROVAL":
        if approval.get("approved") is not True or approval.get("plan_hash") != plan["plan_hash"]:
            raise ValueError("Exact-plan approval is missing or does not match the plan hash")
    return policy, approval


def _validated_receipt(raw: str, scenario: dict[str, Any]) -> dict[str, Any]:
    receipt = json.loads(raw)
    expected = scenario["proposed_action"]
    if (
        receipt.get("accepted") is not True
        or receipt.get("tool") != expected["tool"]
        or receipt.get("target") != expected["target"]
        or not receipt.get("plan_hash")
        or not receipt.get("idempotency_key")
    ):
        raise ValueError("Verification requires a valid, accepted receipt for the governed action")
    return receipt


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    tool = _tool_name(context, event)
    if tool not in TOOL_ARGUMENTS:
        raise ValueError(f"Unsupported MCP tool: {tool}")
    unexpected = set(event) - TOOL_ARGUMENTS[tool] - {"_tool"}
    if unexpected:
        raise ValueError(f"Closed MCP schema rejected arguments: {sorted(unexpected)}")
    if len(event.get("scenario_id", "")) > 80:
        raise ValueError("scenario_id exceeds the tool contract limit")
    for field in ("plan_json", "policy_json", "approval_json", "receipt_json"):
        if len(event.get(field, "")) > 32768:
            raise ValueError(f"{field} exceeds the tool contract limit")
    scenario = _scenario(event["scenario_id"])

    evidence = core.retrieve(scenario)
    branch_by_tool = {
        "search_exact_errors": "lexical",
        "find_similar_incidents": "vector",
        "inspect_dependency_graph": "graph",
        "get_live_state": "live",
    }
    if tool in branch_by_tool:
        branch = branch_by_tool[tool]
        return {
            "retrieval_mode": branch,
            "results": evidence[branch],
            "evidence_ids": [item["id"] for item in evidence[branch]],
            "manifest_hash": evidence["manifest_hash"],
        }

    if tool == "retrieve_hybrid_context":
        return evidence

    if tool == "execute_remediation":
        plan = _validated_plan(event["plan_json"], scenario)
        policy, approval = _validated_authority(
            plan, event["policy_json"], event.get("approval_json", "{}")
        )
        receipt = core.execute_simulated(scenario, plan, event["workflow_id"])
        receipt["authority"] = {
            "policy_version": policy["policy_version"],
            "decision": policy["decision"],
            "approved_plan_hash": approval.get("plan_hash"),
        }
        return receipt

    if tool == "verify_remediation":
        receipt = _validated_receipt(event["receipt_json"], scenario)
        return core.verify_simulated(scenario, receipt)

    if tool == "compensate_remediation":
        plan = _validated_plan(event["plan_json"], scenario)
        _validated_authority(plan, event["policy_json"], event.get("approval_json", "{}"))
        return core.compensate_simulated(scenario, plan, event["workflow_id"])

    raise AssertionError("Validated MCP tool has no implementation")
