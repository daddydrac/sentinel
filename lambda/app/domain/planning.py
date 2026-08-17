"""Deterministic, evidence-bound remediation planner."""

from __future__ import annotations

from typing import Any

from domain.model import canonical_hash


def build_plan(
    scenario: dict[str, Any], evidence: dict[str, Any], planner_mode: str = "deterministic-demo"
) -> dict[str, Any]:
    action = scenario["proposed_action"]
    plan = {
        "schema_version": "1.0",
        "planner_mode": planner_mode,
        "hypothesis": scenario["summary"],
        "tool": action["tool"],
        "target": action["target"],
        "arguments": action["arguments"],
        "risk": action["risk"],
        "expected_outcome": action["expected_outcome"],
        "rollback": action["rollback"],
        "verification": scenario["verification"],
        "evidence_ids": evidence["evidence_ids"],
        "evidence_manifest_hash": evidence["manifest_hash"],
        "assumptions": ["Demo actions are simulated", "Live-state values are fixture data"],
        "expires_in_seconds": 900,
    }
    plan["plan_hash"] = canonical_hash(plan)
    return plan
