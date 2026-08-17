"""Bedrock Converse tool definitions for the GraphRAG read tools.

Bedrock Agents entered maintenance mode and refuses new agent creation, so the
RETURN_CONTROL action group it provided is no longer available. Converse gives
the same guarantee natively: the model emits toolUse blocks and stops, and this
application decides whether any of them run. Nothing executes until the operator
approves the hash-bound plan built from those blocks.

The schemas below mirror contracts/graphrag-tools/*.schema.json exactly; a
contract test asserts they do not drift. Every property is a string because the
approval kernel canonicalizes arguments as strings, which keeps tool_approval.py
identical across both the retired action-group path and this one.
"""

from __future__ import annotations

import hashlib
from typing import Any


ACTION_GROUP_NAME = "GraphRAGReadTools"

_PATTERN_IDS = {
    "type": "string",
    "description": "JSON array of 64-character lowercase SHA-256 pattern IDs.",
    "minLength": 68,
    "maxLength": 8192,
}
_BLOCK_IDS = {
    "type": "string",
    "description": "JSON array of HDFS block identifiers.",
    "minLength": 3,
    "maxLength": 8192,
}
_QUERY = {
    "type": "string",
    "description": "The operator question, verbatim. It must match exactly.",
    "minLength": 1,
    "maxLength": 4000,
}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_log_events": {
        "required": ["query"],
        "properties": {
            "query": _QUERY,
            "mode": {"type": "string", "enum": ["lexical", "vector", "hybrid"], "default": "hybrid"},
            "top_k": {"type": "string", "pattern": "^(?:[1-9]|1[0-9]|20)$", "default": "8"},
        },
    },
    "query_hdfs_graph": {
        "required": ["pattern_ids_json"],
        "properties": {
            "pattern_ids_json": _PATTERN_IDS,
            "max_hops": {"type": "string", "enum": ["1", "2"], "default": "2"},
        },
    },
    "get_anomaly_evidence": {
        "required": ["pattern_ids_json"],
        "properties": {"pattern_ids_json": _PATTERN_IDS},
    },
    "correlate_block_failures": {
        "required": ["block_ids_json"],
        "properties": {
            "block_ids_json": _BLOCK_IDS,
            "top_k": {"type": "string", "pattern": "^(?:[1-9]|1[0-9]|20)$", "default": "20"},
        },
    },
    "analyze_node_behavior": {
        "required": ["block_ids_json"],
        "properties": {"block_ids_json": _BLOCK_IDS},
    },
    "rank_anomalies": {
        "required": ["query"],
        "properties": {
            "query": _QUERY,
            "top_k": {"type": "string", "const": "3", "default": "3"},
        },
    },
    "generate_remediation_plan": {
        "required": ["pattern_ids_json"],
        "properties": {"pattern_ids_json": _PATTERN_IDS},
    },
    "validate_remediation": {
        "required": ["pattern_ids_json", "original_evidence_hash"],
        "properties": {
            "pattern_ids_json": _PATTERN_IDS,
            "original_evidence_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    },
}

# Reasons are the operator-facing justification shown beside each approval.
# tool_approval.TOOL_REASONS is the authority; a contract test pins them together.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_log_events": "Search exact HDFS event text and semantically similar patterns.",
    "query_hdfs_graph": "Connect approved pattern candidates to blocks and constituent events.",
    "get_anomaly_evidence": "Resolve approved pattern IDs to rank features, graph paths, and citations.",
    "correlate_block_failures": "Check whether failures repeat on the approved HDFS blocks.",
    "analyze_node_behavior": "Compare approved blocks against their available corpus baseline.",
    "rank_anomalies": "Apply the approved hybrid policy and select exactly three findings.",
    "generate_remediation_plan": "Translate approved evidence into human actions without executing writes.",
    "validate_remediation": "Read follow-up evidence without claiming recovery from a static snapshot.",
}


# tool_approval requires the query on these tools to equal the operator question
# that cleared the guardrail. Asking the model to echo a string the system already
# holds is a losing proposition -- it paraphrases, repunctuates, or summarises, and
# the request dies at the gate. Hide the parameter from the model and bind it
# server-side instead, so the property holds by construction. The kernel's equality
# check stays exactly as audited and now serves as a backstop.
OPERATOR_QUERY_TOOLS = frozenset({"search_log_events", "rank_anomalies"})


def _model_visible_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    properties = dict(schema["properties"])
    required = [field for field in schema["required"] if field != "query"]
    if name in OPERATOR_QUERY_TOOLS:
        properties.pop("query", None)
    else:
        required = list(schema["required"])
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def tool_config() -> dict[str, Any]:
    """Build the Converse toolConfig for every read-only GraphRAG tool."""
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": name,
                    "description": TOOL_DESCRIPTIONS[name],
                    "inputSchema": {"json": _model_visible_schema(name, schema)},
                }
            }
            for name, schema in sorted(TOOL_SCHEMAS.items())
        ]
    }


def invocation_id(tool_uses: list[dict[str, Any]]) -> str:
    """Derive a stable round identifier from the model's toolUse IDs.

    Bedrock Agents supplied an invocationId that bound an approval to one
    return-control round. Converse has no equivalent, so the plan is bound to a
    digest of the exact toolUseIds it covers instead: a different selection
    produces a different identifier, and therefore a different plan hash.
    """
    joined = "|".join(str(use.get("toolUseId", "")) for use in tool_uses)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def to_invocation_inputs(
    tool_uses: list[dict[str, Any]], operator_query: str
) -> list[dict[str, Any]]:
    """Reshape Converse toolUse blocks into the approval kernel's input form.

    tool_approval validates the retired action-group shape. Adapting here rather
    than changing that module keeps the audited approval logic, and its tests,
    byte-identical across the migration.
    """
    inputs: list[dict[str, Any]] = []
    for use in tool_uses:
        raw = use.get("input")
        if not isinstance(raw, dict):
            raise ValueError("Converse tool input must be a JSON object")
        name = use.get("name")
        arguments = dict(raw)
        if name in OPERATOR_QUERY_TOOLS:
            # The model never sees this field, so anything it invents here is
            # discarded rather than trusted. The operator's guardrailed question
            # is the only value that can reach the tool.
            arguments["query"] = operator_query
        inputs.append(
            {
                "functionInvocationInput": {
                    "actionGroup": ACTION_GROUP_NAME,
                    "function": name,
                    "parameters": [
                        {"name": key, "value": str(value)}
                        for key, value in sorted(arguments.items())
                    ],
                }
            }
        )
    return inputs
