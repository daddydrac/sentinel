"""Fail-closed authorization policy for bounded tools."""

from __future__ import annotations

from typing import Any

from domain.model import utc_now


REVERSIBLE_TOOLS = {"drain_node", "restart_workload", "rollback_deployment"}
READ_ONLY_TOOLS = {"open_investigation", "search_logs", "inspect_topology"}


def decide_policy(plan: dict[str, Any]) -> dict[str, Any]:
    tool = plan["tool"]
    if tool in READ_ONLY_TOOLS:
        decision = "ALLOW"
        obligations = ["record receipt", "verify investigation exists"]
    elif tool in REVERSIBLE_TOOLS and plan["risk"] in {"low", "medium"}:
        decision = "REQUIRE_APPROVAL"
        obligations = [
            "plan-hash approval",
            "single target",
            "canary",
            "independent verification",
            "compensation",
        ]
    else:
        decision = "DENY"
        obligations = ["escalate to operator"]
    return {
        "decision": decision,
        "policy_version": "demo-cedar-equivalent-v1",
        "obligations": obligations,
        "evaluated_at": utc_now(),
    }
