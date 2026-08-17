"""Replaceable simulated execution, verification, and compensation adapters."""

from __future__ import annotations

from typing import Any

from domain.model import canonical_hash, utc_now


def execute_simulated(
    scenario: dict[str, Any], plan: dict[str, Any], workflow_id: str
) -> dict[str, Any]:
    return {
        "receipt_id": f"receipt-{workflow_id[-8:]}",
        "idempotency_key": canonical_hash({"workflow_id": workflow_id, "plan_hash": plan["plan_hash"]}),
        "tool": plan["tool"],
        "target": plan["target"],
        "arguments": plan["arguments"],
        "plan_hash": plan["plan_hash"],
        "accepted": True,
        "simulated": True,
        "executed_at": utc_now(),
    }


def verify_simulated(scenario: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    passed = bool(scenario["verification_should_pass"])
    return {
        "passed": passed,
        "authoritative_source": "simulated independent health adapter",
        "before": scenario["verification"]["before"],
        "after": scenario["verification"]["after"],
        "window": scenario["verification"]["window"],
        "receipt_id": receipt["receipt_id"],
        "verified_at": utc_now(),
    }


def compensate_simulated(
    scenario: dict[str, Any], plan: dict[str, Any], workflow_id: str
) -> dict[str, Any]:
    return {
        "compensation_id": f"comp-{workflow_id[-8:]}",
        "action": plan["rollback"],
        "target": plan["target"],
        "simulated": True,
        "result": "COMPENSATED_AND_ESCALATED",
        "completed_at": utc_now(),
    }
