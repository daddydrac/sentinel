"""Small SigV4 MCP client for an IAM-authorized AgentCore Gateway."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


MCP_PROTOCOL_VERSION = "2025-06-18"
_TOOL_NAMES: dict[str, str] = {}
_SESSION_ID: str | None = None


def _endpoint() -> str:
    gateway_url = os.environ["MCP_GATEWAY_URL"].rstrip("/")
    return gateway_url if gateway_url.endswith("/mcp") else f"{gateway_url}/mcp"


def _decode_response(raw: str, content_type: str) -> dict[str, Any]:
    if "text/event-stream" in content_type:
        events = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
        if not events:
            raise RuntimeError("AgentCore Gateway returned an empty event stream")
        return json.loads(events[-1])
    return json.loads(raw)


def _send(
    method: str,
    params: dict[str, Any] | None = None,
    session_id: str | None = None,
    notification: bool = False,
) -> tuple[dict[str, Any], str | None]:
    # A JSON-RPC notification carries no id and expects no result, which is how
    # notifications/initialized is distinguished from a request.
    envelope: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        envelope["id"] = str(uuid.uuid4())
    payload = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "mcp-protocol-version": MCP_PROTOCOL_VERSION,
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    aws_request = AWSRequest(method="POST", url=_endpoint(), data=payload, headers=headers)
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials available for MCP SigV4 signing")
    SigV4Auth(credentials.get_frozen_credentials(), "bedrock-agentcore", region).add_auth(aws_request)
    prepared = aws_request.prepare()
    http_request = urllib.request.Request(
        prepared.url,
        data=payload,
        method="POST",
        headers=dict(prepared.headers.items()),
    )
    try:
        with urllib.request.urlopen(http_request, timeout=25) as response:
            body = response.read().decode("utf-8")
            content_type = response.headers.get("content-type", "application/json")
            returned_session_id = response.headers.get("mcp-session-id")
    except urllib.error.HTTPError as exc:
        # The gateway explains itself in the body; without it a caller only sees
        # "HTTP Error 400: Bad Request" and has to reproduce the call by hand.
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:  # noqa: BLE001 - diagnostics only, never mask the original
            pass
        raise RuntimeError(f"MCP {method} failed with HTTP {exc.code}: {detail}") from exc
    if notification and not body.strip():
        return {}, returned_session_id
    decoded = _decode_response(body, content_type)
    if "error" in decoded:
        raise RuntimeError(f"MCP {method} failed: {decoded['error']}")
    return decoded, returned_session_id


def _initialize() -> str:
    global _SESSION_ID
    response, session_id = _send(
        "initialize",
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "hpe-agentic-remediation", "version": "1.0.0"},
        },
    )
    negotiated = response.get("result", {}).get("protocolVersion")
    if negotiated not in {"2025-03-26", "2025-06-18"}:
        raise RuntimeError(f"AgentCore Gateway negotiated unsupported MCP version: {negotiated}")
    if not session_id:
        raise RuntimeError("Session-enabled AgentCore Gateway did not return Mcp-Session-Id")
    # MCP requires the client to confirm the handshake before it may issue any
    # other request. Without this the gateway rejects tools/list and tools/call
    # with -32600 "Session not initialized. Send notifications/initialized first."
    _send("notifications/initialized", session_id=session_id, notification=True)
    _SESSION_ID = session_id
    return session_id


def request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    global _SESSION_ID
    if method == "initialize":
        return _send(method, params)[0]
    session_id = _SESSION_ID or _initialize()
    try:
        return _send(method, params, session_id)[0]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        _SESSION_ID = None
        return _send(method, params, _initialize())[0]


def _resolve_tool_name(short_name: str) -> str:
    if short_name in _TOOL_NAMES:
        return _TOOL_NAMES[short_name]
    listed = request("tools/list")
    tools = listed.get("result", {}).get("tools", [])
    for tool in tools:
        name = tool.get("name", "")
        if name == short_name or name.endswith(f"___{short_name}"):
            _TOOL_NAMES[short_name] = name
            return name
    raise RuntimeError(f"MCP tool {short_name!r} was not advertised by AgentCore Gateway")


def call_tool(short_name: str, arguments: dict[str, Any]) -> Any:
    response = request(
        "tools/call",
        {"name": _resolve_tool_name(short_name), "arguments": arguments},
    )
    result = response.get("result", {})
    if result.get("isError"):
        raise RuntimeError(f"MCP tool {short_name} returned an error: {result}")
    for item in result.get("content", []):
        if item.get("type") == "text":
            text = item.get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return result
