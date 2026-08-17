"""Closed, deterministic approval contract for GraphRAG MCP tool selection.

Bedrock Agent selects functions through a RETURN_CONTROL action group. This
module turns the returned invocation inputs into a canonical, hash-bound plan
that a human can approve before the application invokes AgentCore MCP.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


MAX_APPROVAL_ROUNDS = 6
TOOL_APPROVAL_TTL_SECONDS = 600
ACTION_GROUP_NAME = "GraphRAGReadTools"
CHAT_ID_PATTERN = re.compile(
    r"chat-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

TOOL_REASONS = {
    "search_log_events": "Search exact HDFS event text and semantically similar patterns.",
    "query_hdfs_graph": "Connect approved pattern candidates to blocks and constituent events.",
    "get_anomaly_evidence": "Resolve approved pattern IDs to rank features, graph paths, and citations.",
    "correlate_block_failures": "Check whether failures repeat on the approved HDFS blocks.",
    "analyze_node_behavior": "Compare approved blocks against their available corpus baseline.",
    "rank_anomalies": "Apply the approved hybrid policy and select exactly three findings.",
    "generate_remediation_plan": "Translate approved evidence into human actions without executing writes.",
    "validate_remediation": "Read follow-up evidence without claiming recovery from a static snapshot.",
}

TOOL_ARGUMENTS = {
    "search_log_events": {"query", "mode", "top_k"},
    "query_hdfs_graph": {"pattern_ids_json", "max_hops"},
    "get_anomaly_evidence": {"pattern_ids_json"},
    "correlate_block_failures": {"block_ids_json", "top_k"},
    "analyze_node_behavior": {"block_ids_json"},
    "rank_anomalies": {"query", "top_k"},
    "generate_remediation_plan": {"pattern_ids_json"},
    "validate_remediation": {"pattern_ids_json", "original_evidence_hash"},
}

_REQUIRED_ARGUMENTS = {
    "search_log_events": {"query"},
    "query_hdfs_graph": {"pattern_ids_json"},
    "get_anomaly_evidence": {"pattern_ids_json"},
    "correlate_block_failures": {"block_ids_json"},
    "analyze_node_behavior": {"block_ids_json"},
    "rank_anomalies": {"query"},
    "generate_remediation_plan": {"pattern_ids_json"},
    "validate_remediation": {"pattern_ids_json", "original_evidence_hash"},
}


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalized_query(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Tool query must be a string")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 4_000:
        raise ValueError("Tool query must contain 1 through 4000 characters")
    return normalized


def _bounded_integer(value: Any, field: str, minimum: int, maximum: int) -> str:
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if integer < minimum or integer > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return str(integer)


def _json_array(value: Any, field: str, sha256_only: bool) -> str:
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError(f"{field} must be a bounded JSON string")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must contain valid JSON") from exc
    if not isinstance(decoded, list) or not 1 <= len(decoded) <= 20:
        raise ValueError(f"{field} must contain between 1 and 20 identifiers")
    cleaned: list[str] = []
    for item in decoded:
        if not isinstance(item, str) or not item or len(item) > 256:
            raise ValueError(f"{field} contains an invalid identifier")
        if sha256_only and not re.fullmatch(r"[0-9a-f]{64}", item):
            raise ValueError(f"{field} must contain lowercase SHA-256 identifiers")
        cleaned.append(item)
    return json.dumps(cleaned, separators=(",", ":"))


def _canonical_arguments(function: str, raw: dict[str, Any], operator_query: str) -> dict[str, str]:
    unexpected = set(raw) - TOOL_ARGUMENTS[function]
    missing = _REQUIRED_ARGUMENTS[function] - set(raw)
    if unexpected or missing:
        raise ValueError(
            f"Closed tool schema rejected unexpected={sorted(unexpected)} missing={sorted(missing)}"
        )
    arguments = {key: str(value) for key, value in raw.items()}

    if function in {"search_log_events", "rank_anomalies"}:
        selected_query = _normalized_query(arguments["query"])
        if selected_query != operator_query:
            raise ValueError("Agent-selected query must exactly match the guardrailed operator query")
        arguments["query"] = selected_query
    if function == "search_log_events":
        mode = arguments.get("mode", "hybrid")
        if mode not in {"lexical", "vector", "hybrid"}:
            raise ValueError("search_log_events mode must be lexical, vector, or hybrid")
        arguments["mode"] = mode
        arguments["top_k"] = _bounded_integer(arguments.get("top_k", "8"), "top_k", 1, 20)
    elif function == "rank_anomalies":
        arguments["top_k"] = _bounded_integer(arguments.get("top_k", "3"), "top_k", 3, 3)
    elif function == "query_hdfs_graph":
        arguments["pattern_ids_json"] = _json_array(
            arguments["pattern_ids_json"], "pattern_ids_json", True
        )
        arguments["max_hops"] = _bounded_integer(
            arguments.get("max_hops", "2"), "max_hops", 1, 2
        )
    elif function in {"get_anomaly_evidence", "generate_remediation_plan"}:
        arguments["pattern_ids_json"] = _json_array(
            arguments["pattern_ids_json"], "pattern_ids_json", True
        )
    elif function == "validate_remediation":
        arguments["pattern_ids_json"] = _json_array(
            arguments["pattern_ids_json"], "pattern_ids_json", True
        )
        if not re.fullmatch(r"[0-9a-f]{64}", arguments["original_evidence_hash"]):
            raise ValueError("original_evidence_hash must be a lowercase SHA-256 value")
    elif function in {"correlate_block_failures", "analyze_node_behavior"}:
        arguments["block_ids_json"] = _json_array(
            arguments["block_ids_json"], "block_ids_json", False
        )
        if function == "correlate_block_failures":
            arguments["top_k"] = _bounded_integer(
                arguments.get("top_k", "20"), "top_k", 1, 20
            )
    return {key: arguments[key] for key in sorted(arguments)}


def build_tool_plan(
    *,
    chat_id: str,
    operator_query: str,
    approval_round: int,
    invocation_id: str,
    invocation_inputs: list[dict[str, Any]],
    created_at: int,
) -> dict[str, Any]:
    """Validate Bedrock RETURN_CONTROL inputs and create an exact approval plan."""
    query = _normalized_query(operator_query)
    if not CHAT_ID_PATTERN.fullmatch(chat_id):
        raise ValueError("Invalid chat identifier in tool approval plan")
    if approval_round < 1 or approval_round > MAX_APPROVAL_ROUNDS:
        raise ValueError(f"approval_round must be between 1 and {MAX_APPROVAL_ROUNDS}")
    if not isinstance(invocation_id, str) or not 1 <= len(invocation_id) <= 256:
        raise ValueError("Bedrock return-control invocation ID is missing or invalid")
    if not isinstance(invocation_inputs, list) or not 1 <= len(invocation_inputs) <= 4:
        raise ValueError("Bedrock must select between one and four tools per approval round")

    calls: list[dict[str, Any]] = []
    selected_names: set[str] = set()
    for item in invocation_inputs:
        if set(item) != {"functionInvocationInput"}:
            raise ValueError("Only function-based GraphRAG return-control inputs are accepted")
        selected = item["functionInvocationInput"]
        if selected.get("actionGroup") != ACTION_GROUP_NAME:
            raise ValueError("Return-control input came from an unexpected action group")
        function = selected.get("function")
        if function not in TOOL_ARGUMENTS:
            raise ValueError(f"Agent selected a non-GraphRAG or non-read-only tool: {function}")
        if function in selected_names:
            raise ValueError(f"Agent selected duplicate tool {function} in one approval round")
        selected_names.add(function)
        raw_parameters: dict[str, Any] = {}
        for parameter in selected.get("parameters", []):
            name = parameter.get("name")
            if not isinstance(name, str) or name in raw_parameters:
                raise ValueError("Agent returned a missing or duplicate tool parameter")
            raw_parameters[name] = parameter.get("value")
        arguments = _canonical_arguments(function, raw_parameters, query)
        calls.append(
            {
                "sequence": len(calls) + 1,
                "action_group": ACTION_GROUP_NAME,
                "tool": function,
                "arguments": arguments,
                "reason": TOOL_REASONS[function],
                "authority": "read-only",
            }
        )

    if approval_round == 1 and "rank_anomalies" not in selected_names:
        raise ValueError("The first GraphRAG selection round must include rank_anomalies")

    unsigned = {
        "schema_version": "1.0",
        "chat_id": chat_id,
        "operator_query": query,
        "approval_round": approval_round,
        "invocation_id": invocation_id,
        "created_at": int(created_at),
        "approval_expires_at": int(created_at) + TOOL_APPROVAL_TTL_SECONDS,
        "calls": calls,
    }
    return {**unsigned, "plan_hash": _canonical_hash(unsigned)}


def validate_tool_plan(
    plan: dict[str, Any], *, chat_id: str, operator_query: str, now_epoch: int | None = None
) -> dict[str, Any]:
    """Reject approval-plan mutation, cross-chat replay, and expired execution."""
    if not isinstance(plan, dict):
        raise ValueError("Stored tool plan is missing")
    expected_fields = {
        "schema_version",
        "chat_id",
        "operator_query",
        "approval_round",
        "invocation_id",
        "created_at",
        "approval_expires_at",
        "calls",
        "plan_hash",
    }
    if set(plan) != expected_fields:
        raise ValueError("Stored tool plan does not match the closed approval schema")
    if plan["schema_version"] != "1.0" or not CHAT_ID_PATTERN.fullmatch(str(plan["chat_id"])):
        raise ValueError("Tool plan schema version or chat identifier is invalid")
    if plan["chat_id"] != chat_id or plan["operator_query"] != _normalized_query(operator_query):
        raise ValueError("Tool plan is not bound to this chat and operator query")
    if not isinstance(plan["invocation_id"], str) or not 1 <= len(plan["invocation_id"]) <= 256:
        raise ValueError("Tool plan invocation ID is invalid")
    try:
        approval_round = int(plan["approval_round"])
        created_at = int(plan["created_at"])
        approval_expires_at = int(plan["approval_expires_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Tool plan round or timestamps are invalid") from exc
    if approval_round < 1 or approval_round > MAX_APPROVAL_ROUNDS:
        raise ValueError("Tool plan approval round is outside the configured limit")
    if approval_expires_at != created_at + TOOL_APPROVAL_TTL_SECONDS:
        raise ValueError("Tool plan approval lifetime was modified")
    if not isinstance(plan["plan_hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", plan["plan_hash"]):
        raise ValueError("Tool plan hash has an invalid format")
    unsigned = {key: value for key, value in plan.items() if key != "plan_hash"}
    if plan["plan_hash"] != _canonical_hash(unsigned):
        raise ValueError("Tool plan hash does not match the canonical approval payload")
    if now_epoch is not None and int(now_epoch) > int(plan["approval_expires_at"]):
        raise ValueError("Tool plan approval window has expired")
    if not isinstance(plan["calls"], list) or not 1 <= len(plan["calls"]) <= 4:
        raise ValueError("Tool plan has an invalid call count")
    selected_names: set[str] = set()
    for expected_sequence, call in enumerate(plan["calls"], 1):
        if set(call) != {"sequence", "action_group", "tool", "arguments", "reason", "authority"}:
            raise ValueError("Tool call does not match the closed approval schema")
        if call["sequence"] != expected_sequence or call["action_group"] != ACTION_GROUP_NAME:
            raise ValueError("Tool call order or action-group binding was modified")
        if call["tool"] not in TOOL_ARGUMENTS or call["authority"] != "read-only":
            raise ValueError("Tool plan contains unauthorized authority")
        if call["tool"] in selected_names:
            raise ValueError("Tool plan contains a duplicate call")
        selected_names.add(call["tool"])
        if call["reason"] != TOOL_REASONS[call["tool"]]:
            raise ValueError("Tool plan reason was modified")
        if _canonical_arguments(call["tool"], call["arguments"], plan["operator_query"]) != call[
            "arguments"
        ]:
            raise ValueError("Tool arguments are not canonical")
    if approval_round == 1 and "rank_anomalies" not in selected_names:
        raise ValueError("The first GraphRAG approval round must include rank_anomalies")
    return plan
