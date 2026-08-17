"""API Gateway Lambda for the web demo and approval callback."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3

from domain import core
from domain import tool_approval


TABLE_NAME = os.environ["TABLE_NAME"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
DEMO_TOKEN = os.environ["DEMO_TOKEN"]
CHAT_FUNCTION_NAME = os.getenv("CHAT_FUNCTION_NAME", "")

table = boto3.resource("dynamodb").Table(TABLE_NAME)
sfn = boto3.client("stepfunctions")
lambda_client = boto3.client("lambda")
UI = Path(__file__).with_name("ui.html").read_text(encoding="utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _response(status: int, body: Any, content_type: str = "application/json") -> dict[str, Any]:
    payload = body if isinstance(body, str) else json.dumps(body)
    return {
        "statusCode": status,
        "headers": {
            "content-type": content_type,
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "content-security-policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
        },
        "body": payload,
    }


def _authorized(event: dict[str, Any]) -> bool:
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    return headers.get("x-demo-token") == DEMO_TOKEN


def _jwt_claims(event: dict[str, Any]) -> dict[str, Any]:
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    return claims if isinstance(claims, dict) else {}


def _groups(claims: dict[str, Any]) -> set[str]:
    """Parse the cognito:groups claim in every shape the platform emits.

    An API Gateway HTTP API JWT authorizer renders a multi-valued claim as
    "[admin approver]" with space separators. Splitting on commas alone
    collapses that into the single bogus group "admin approver", which silently
    denies approval authority to anyone holding more than one role.
    """
    raw = claims.get("cognito:groups", [])
    if isinstance(raw, list):
        return {str(value).strip() for value in raw if str(value).strip()}
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                return {str(value).strip() for value in decoded if str(value).strip()}
        except json.JSONDecodeError:
            pass
        return {value for value in re.split(r"[,\s]+", raw.strip("[]").strip()) if value}
    return set()


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    return json.loads(raw)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    claims = _jwt_claims(event)
    owner_sub = claims.get("sub", "")
    is_chat_route = path == "/api/chats" or path.startswith("/api/chats/")

    if method == "GET" and path == "/":
        return _response(200, UI, "text/html; charset=utf-8")

    if is_chat_route and (not isinstance(owner_sub, str) or not owner_sub):
        return _response(401, {"error": "A valid Cognito identity is required"})
    if not is_chat_route and not _authorized(event):
        return _response(401, {"error": "Missing or invalid X-Demo-Token"})

    if method == "GET" and path == "/api/scenarios":
        return _response(200, {"scenarios": core.scenario_summaries()})

    if method == "POST" and path == "/api/chats":
        if not CHAT_FUNCTION_NAME:
            return _response(503, {"error": "GraphRAG chat endpoint is not deployed"})
        payload = _body(event)
        query = payload.get("query")
        chat_id = payload.get("chat_id")
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            return _response(400, {"error": "query must contain 1 through 4000 characters"})
        if not isinstance(chat_id, str) or not re.fullmatch(
            r"chat-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", chat_id
        ):
            return _response(400, {"error": "chat_id must be a client-generated chat UUID"})
        now = int(time.time())
        try:
            table.put_item(
                Item={
                    "workflow_id": chat_id,
                    "kind": "GRAPHRAG_CHAT",
                    "status": "QUEUED",
                    "step": "QUEUED",
                    "query": " ".join(query.split()),
                    "owner_sub": owner_sub,
                    "updated_at": now,
                    "expires_at": now + 86400,
                },
                ConditionExpression="attribute_not_exists(workflow_id)",
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            return _response(409, {"error": "chat_id already exists"})
        try:
            lambda_client.invoke(
                FunctionName=CHAT_FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps(
                    {"chat_id": chat_id, "query": " ".join(query.split()), "phase": "PLAN"}
                ).encode("utf-8"),
            )
        except Exception as exc:
            table.update_item(
                Key={"workflow_id": chat_id},
                UpdateExpression="SET #status = :failed, error_message = :error, updated_at = :now",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":failed": "FAILED",
                    ":error": f"Chat dispatch failed: {type(exc).__name__}"[:500],
                    ":now": int(time.time()),
                },
            )
            return _response(502, {"error": "GraphRAG chat dispatch failed", "chat_id": chat_id})
        return _response(202, {"chat_id": chat_id, "status": "QUEUED"})

    match = re.fullmatch(
        r"/api/chats/(chat-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        path,
    )
    if method == "GET" and match:
        item = table.get_item(Key={"workflow_id": match.group(1)}, ConsistentRead=True).get("Item")
        if not item or item.get("kind") != "GRAPHRAG_CHAT":
            return _response(404, {"error": "Chat not found"})
        if item.get("owner_sub") != owner_sub:
            return _response(404, {"error": "Chat not found"})
        public = _json_safe(item.get("state", {}))
        public.update(
            {
                "chat_id": match.group(1),
                "status": item["status"],
                "error_message": item.get("error_message"),
            }
        )
        return _response(200, public)

    decision_match = re.fullmatch(
        r"/api/chats/(chat-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/tool-decision",
        path,
    )
    if method == "POST" and decision_match:
        if not CHAT_FUNCTION_NAME:
            return _response(503, {"error": "GraphRAG chat endpoint is not deployed"})
        chat_id = decision_match.group(1)
        item = table.get_item(Key={"workflow_id": chat_id}, ConsistentRead=True).get("Item")
        if not item or item.get("kind") != "GRAPHRAG_CHAT":
            return _response(404, {"error": "Chat not found"})
        if item.get("owner_sub") != owner_sub:
            return _response(404, {"error": "Chat not found"})
        if not (_groups(claims) & {"approver", "admin"}):
            return _response(403, {"error": "Tool decisions require the approver or admin group"})
        if item.get("status") != "AWAITING_TOOL_APPROVAL":
            return _response(409, {"error": "Chat is not awaiting a tool decision"})
        payload = _body(event)
        if not isinstance(payload.get("approved"), bool):
            return _response(400, {"error": "approved must be a JSON boolean"})
        supplied_hash = payload.get("plan_hash")
        if not isinstance(supplied_hash, str) or supplied_hash != item.get("plan_hash"):
            return _response(409, {"error": "Tool plan hash mismatch; approval is invalid"})
        try:
            plan = tool_approval.validate_tool_plan(
                _json_safe(item.get("pending_tool_plan", {})),
                chat_id=chat_id,
                operator_query=item["query"],
                now_epoch=int(time.time()),
            )
        except ValueError as exc:
            if "expired" in str(exc).lower():
                try:
                    table.update_item(
                        Key={"workflow_id": chat_id},
                        UpdateExpression="SET #status = :expired, #step = :expired, error_message = :error, updated_at = :now",
                        ConditionExpression="#status = :awaiting AND plan_hash = :hash",
                        ExpressionAttributeNames={"#status": "status", "#step": "step"},
                        ExpressionAttributeValues={
                            ":expired": "TOOL_PLAN_EXPIRED",
                            ":awaiting": "AWAITING_TOOL_APPROVAL",
                            ":hash": supplied_hash,
                            ":error": "The MCP tool approval window expired; start a new chat.",
                            ":now": int(time.time()),
                        },
                    )
                except table.meta.client.exceptions.ConditionalCheckFailedException:
                    pass
            return _response(409, {"error": f"Tool plan cannot be approved: {exc}"})

        decision = {
            "approved": payload["approved"],
            "plan_hash": plan["plan_hash"],
            "approval_round": plan["approval_round"],
            "decided_at": int(time.time()),
        }
        next_status = "TOOL_PLAN_APPROVED" if decision["approved"] else "REJECTED"
        public_state = _json_safe(item.get("state", {}))
        public_state.update(
            {
                "status": next_status,
                "pending_tool_plan": plan,
                "last_tool_decision": decision,
            }
        )
        try:
            table.update_item(
                Key={"workflow_id": chat_id},
                UpdateExpression=(
                    "SET #status = :next, #step = :next, #state = :state, "
                    "tool_approval = :approval, updated_at = :now"
                ),
                ConditionExpression="#status = :awaiting AND plan_hash = :hash",
                ExpressionAttributeNames={"#status": "status", "#step": "step", "#state": "state"},
                ExpressionAttributeValues={
                    ":next": next_status,
                    ":awaiting": "AWAITING_TOOL_APPROVAL",
                    ":hash": supplied_hash,
                    ":state": public_state,
                    ":approval": decision,
                    ":now": int(time.time()),
                },
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            return _response(409, {"error": "Tool plan was already decided or changed"})

        if not decision["approved"]:
            return _response(
                200,
                {
                    "chat_id": chat_id,
                    "status": "REJECTED",
                    "approved": False,
                    "plan_hash": supplied_hash,
                },
            )
        try:
            lambda_client.invoke(
                FunctionName=CHAT_FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps({"chat_id": chat_id, "phase": "EXECUTE"}).encode("utf-8"),
            )
        except Exception as exc:
            table.update_item(
                Key={"workflow_id": chat_id},
                UpdateExpression="SET #status = :failed, #step = :failed, error_message = :error, updated_at = :now",
                ExpressionAttributeNames={"#status": "status", "#step": "step"},
                ExpressionAttributeValues={
                    ":failed": "FAILED",
                    ":error": f"Approved chat dispatch failed: {type(exc).__name__}"[:500],
                    ":now": int(time.time()),
                },
            )
            return _response(502, {"error": "Approved GraphRAG execution dispatch failed"})
        return _response(
            202,
            {
                "chat_id": chat_id,
                "status": "TOOL_PLAN_APPROVED",
                "approved": True,
                "plan_hash": supplied_hash,
            },
        )

    if method == "POST" and path == "/api/workflows":
        payload = _body(event)
        scenario_id = payload.get("scenario_id")
        if scenario_id not in core.load_scenarios():
            return _response(400, {"error": "Unknown scenario_id"})
        workflow_id = f"wf-{uuid.uuid4()}"
        initial = {
            "workflow_id": workflow_id,
            "scenario_id": scenario_id,
            "created_at": core.utc_now(),
            "expires_at": int(time.time()) + 86400,
        }
        table.put_item(
            Item={
                "workflow_id": workflow_id,
                "scenario_id": scenario_id,
                "status": "QUEUED",
                "step": "QUEUED",
                "updated_at": core.utc_now(),
                "state": initial,
                "expires_at": initial["expires_at"],
            }
        )
        execution = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=workflow_id.replace("-", "")[:80],
            input=json.dumps(initial),
        )
        return _response(202, {"workflow_id": workflow_id, "execution_arn": execution["executionArn"]})

    match = re.fullmatch(r"/api/workflows/([^/]+)", path)
    if method == "GET" and match:
        item = table.get_item(Key={"workflow_id": match.group(1)}, ConsistentRead=True).get("Item")
        if not item:
            return _response(404, {"error": "Workflow not found"})
        public = _json_safe(item.get("state", {}))
        public.update({"status": item["status"], "step": item["step"]})
        return _response(200, public)

    match = re.fullmatch(r"/api/workflows/([^/]+)/decision", path)
    if method == "POST" and match:
        workflow_id = match.group(1)
        item = table.get_item(Key={"workflow_id": workflow_id}, ConsistentRead=True).get("Item")
        if not item:
            return _response(404, {"error": "Workflow not found"})
        if item.get("status") != "AWAITING_APPROVAL" or not item.get("approval_token"):
            return _response(409, {"error": "Workflow is not awaiting approval"})
        payload = _body(event)
        supplied_hash = payload.get("plan_hash")
        if supplied_hash != item.get("plan_hash"):
            return _response(409, {"error": "Plan hash mismatch; approval is invalid"})
        if not isinstance(payload.get("approved"), bool):
            return _response(400, {"error": "approved must be a JSON boolean"})
        approved = payload["approved"]
        sfn.send_task_success(
            taskToken=item["approval_token"],
            output=json.dumps({"approved": approved, "plan_hash": supplied_hash, "decided_at": core.utc_now()}),
        )
        table.update_item(
            Key={"workflow_id": workflow_id},
            UpdateExpression="SET #status = :status REMOVE approval_token",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "APPROVED" if approved else "REJECTED"},
        )
        return _response(202, {"workflow_id": workflow_id, "approved": approved})

    return _response(404, {"error": "Route not found"})
