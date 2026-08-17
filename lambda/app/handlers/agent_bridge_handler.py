"""Bedrock Agent action-group bridge to the AgentCore MCP Gateway."""

from __future__ import annotations

import json
from typing import Any

from adapters import mcp_client


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    function = event["function"]
    parameters = {item["name"]: item["value"] for item in event.get("parameters", [])}
    try:
        allowed = {
            "search_exact_errors": {"scenario_id"},
            "find_similar_incidents": {"scenario_id"},
            "inspect_dependency_graph": {"scenario_id"},
            "get_live_state": {"scenario_id"},
            "retrieve_hybrid_context": {"scenario_id"},
        }
        if function not in allowed:
            raise ValueError(f"Unsupported action-group function: {function}")
        unexpected = set(parameters) - allowed[function]
        if unexpected:
            raise ValueError(f"Action group supplied undeclared parameters: {sorted(unexpected)}")
        result = mcp_client.call_tool(function, parameters)
        function_response = {
            "responseBody": {"TEXT": {"body": json.dumps(result, separators=(",", ":"))}}
        }
    except Exception as exc:
        function_response = {
            "responseState": "FAILURE",
            "responseBody": {"TEXT": {"body": f"MCP bridge failed: {type(exc).__name__}: {exc}"}},
        }

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event["actionGroup"],
            "function": function,
            "functionResponse": function_response,
        },
        "sessionAttributes": event.get("sessionAttributes", {}),
        "promptSessionAttributes": event.get("promptSessionAttributes", {}),
    }
