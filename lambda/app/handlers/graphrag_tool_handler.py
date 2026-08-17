"""Read-only AgentCore target for the HDFS GraphRAG tool contract."""

from __future__ import annotations

import json
from typing import Any

from adapters import graphrag


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


def _tool_name(context: Any, event: dict[str, Any]) -> str:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    visible = custom.get("bedrockAgentCoreToolName", event.get("_tool", ""))
    return visible.split("___", 1)[-1]


def _bounded_int(raw: Any, default: int, minimum: int, maximum: int, field: str) -> int:
    try:
        value = int(raw if raw not in (None, "") else default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _ids(event: dict[str, Any], field: str) -> list[str]:
    raw = event.get(field, "")
    if len(raw) > 8192:
        raise ValueError(f"{field} exceeds the tool contract limit")
    value = json.loads(raw)
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ValueError(f"{field} must be a non-empty JSON array of at most 20 values")
    if not all(isinstance(item, str) and 0 < len(item) <= 256 for item in value):
        raise ValueError(f"{field} contains an invalid identifier")
    return value


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("MCP event must be an object")
    tool = _tool_name(context, event)
    if tool not in TOOL_ARGUMENTS:
        raise ValueError(f"Unsupported read-only GraphRAG tool: {tool}")
    unexpected = set(event) - TOOL_ARGUMENTS[tool] - {"_tool"}
    if unexpected:
        raise ValueError(f"Closed MCP schema rejected arguments: {sorted(unexpected)}")
    query = event.get("query", "")
    if not isinstance(query, str) or len(query) > graphrag.MAX_QUERY_CHARS:
        raise ValueError("query exceeds the tool contract limit")

    if tool == "search_log_events":
        return graphrag.search_log_events(
            query,
            event.get("mode", "hybrid"),
            _bounded_int(event.get("top_k"), 8, 1, 20, "top_k"),
        )
    if tool == "query_hdfs_graph":
        return graphrag.expand_hdfs_graph(
            _ids(event, "pattern_ids_json"),
            _bounded_int(event.get("max_hops"), 2, 1, 2, "max_hops"),
        )
    if tool == "get_anomaly_evidence":
        return graphrag.get_anomaly_evidence(_ids(event, "pattern_ids_json"))
    if tool == "correlate_block_failures":
        return graphrag.correlate_block_failures(
            _ids(event, "block_ids_json"),
            _bounded_int(event.get("top_k"), 20, 1, 20, "top_k"),
        )
    if tool == "analyze_node_behavior":
        return graphrag.analyze_node_behavior(_ids(event, "block_ids_json"))
    if tool == "rank_anomalies":
        return graphrag.rank_hdfs_anomalies(
            query, _bounded_int(event.get("top_k"), 3, 1, 3, "top_k")
        )
    if tool == "generate_remediation_plan":
        return graphrag.generate_remediation_plan(_ids(event, "pattern_ids_json"))
    if tool == "validate_remediation":
        evidence_hash = event.get("original_evidence_hash", "")
        if not isinstance(evidence_hash, str) or len(evidence_hash) != 64:
            raise ValueError("original_evidence_hash must be a SHA-256 hex digest")
        return graphrag.validate_remediation(_ids(event, "pattern_ids_json"), evidence_hash)
    raise AssertionError("Validated GraphRAG tool has no implementation")
