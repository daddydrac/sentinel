"""Guarded GraphRAG chat with Bedrock Converse tool use and human approval.

The model autonomously selects read-only GraphRAG tools and stops. Its exact
selection is canonicalized, hash-bound, and shown to the operator. AgentCore MCP
is not invoked until the matching plan is approved, and every later selection
round repeats the same checkpoint.

Converse replaces the Bedrock Agents RETURN_CONTROL action group, which is no
longer available for new agents. The governance contract is unchanged: the model
proposes, a human approves an exact hash, and only then does anything execute.
Because Converse is stateless, the transcript is persisted by this application
(see conversation.py) rather than held behind a vendor session identifier.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.exceptions import ClientError

from domain import conversation
from domain import converse_tools
from adapters import mcp_client
from domain import tool_approval


TABLE_NAME = os.environ["TABLE_NAME"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
GUARDRAIL_ID = os.environ["GUARDRAIL_ID"]
GUARDRAIL_VERSION = os.environ["GUARDRAIL_VERSION"]
SAGEMAKER_ENDPOINT_NAME = os.environ["SAGEMAKER_ENDPOINT_NAME"]
APPSYNC_HTTP_ENDPOINT = os.environ["APPSYNC_HTTP_ENDPOINT"]
EVIDENCE_BUCKET = os.environ["EVIDENCE_BUCKET"]
KMS_KEY_ARN = os.environ["KMS_KEY_ARN"]
MAX_TOOL_RESULT_BYTES = 180_000
# Must not exceed the endpoint's own ceiling; Terraform passes the same value to
# both so a lowered limit cannot silently fail every synthesis request.
MAX_NEW_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "768"))

# Clients are created once per execution environment and reused across
# invocations; adaptive retries absorb Bedrock throttling without a hand-rolled
# backoff loop.
table = boto3.resource("dynamodb").Table(TABLE_NAME)
bedrock = boto3.client(
    "bedrock-runtime",
    config=Config(read_timeout=120, retries={"mode": "adaptive", "max_attempts": 8}),
)
sagemaker = boto3.client(
    "sagemaker-runtime",
    config=Config(read_timeout=480, retries={"mode": "adaptive", "max_attempts": 4}),
)
s3 = boto3.client("s3")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _dynamodb_safe(value: Any) -> Any:
    """Convert model/tool floats to Decimal before DynamoDB persistence."""
    return json.loads(json.dumps(value), parse_float=Decimal)


def _publish(owner_sub: str, chat_id: str, event: dict[str, Any]) -> None:
    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    channel = f"/sessions/{owner_sub}/{chat_id}"
    body = json.dumps(
        {"channel": channel, "events": [json.dumps(event, separators=(",", ":"))]},
        separators=(",", ":"),
    ).encode("utf-8")
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials are available to publish AppSync events")
    headers = {
        "content-type": "application/json",
        "host": urllib.parse.urlparse(APPSYNC_HTTP_ENDPOINT).netloc,
    }
    request = AWSRequest(method="POST", url=APPSYNC_HTTP_ENDPOINT, data=body, headers=headers)
    SigV4Auth(credentials.get_frozen_credentials(), "appsync", region).add_auth(request)
    prepared = request.prepare()
    outgoing = urllib.request.Request(
        prepared.url, data=body, method="POST", headers=dict(prepared.headers.items())
    )
    try:
        with urllib.request.urlopen(outgoing, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AppSync publish failed with HTTP {exc.code}: {detail}") from exc
    if payload.get("failed"):
        raise RuntimeError(f"AppSync rejected an event: {payload['failed']}")


def _guardrail(text: str, source: str) -> None:
    response = bedrock.apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        source=source,
        content=[
            {
                "text": {
                    "text": text,
                    "qualifiers": ["query"] if source == "INPUT" else ["guard_content"],
                }
            }
        ],
    )
    if response.get("action") == "GUARDRAIL_INTERVENED":
        raise PermissionError(f"Bedrock Guardrail blocked {source.lower()} content")


AGENT_SYSTEM_PROMPT = (
    "You investigate HDFS log anomalies using only the supplied read-only GraphRAG tools. "
    "In the first round you must call rank_anomalies with the operator's question verbatim "
    "and top_k=3. Afterwards call only the additional read tools whose evidence materially "
    "improves the diagnosis, and stop calling tools once you can answer. Never invent a "
    "finding that the tool results do not support. Every tool call you request is shown to a "
    "human for exact approval before it runs, so request precisely what you need."
)


# A tool selection is a few hundred tokens. The turn after tool results carries
# the entire evidence set back into context, so the model writes to whatever
# ceiling it is given: measured against the live endpoint it consumed 2048 of 2048
# and 4096 of 4096 with a 78 KB rank_anomalies result in context. Raising the
# limit therefore does not stop truncation, it only costs more, which is why
# _select_tools tolerates a clipped summary instead of chasing a bigger number.
SELECTION_MAX_TOKENS = 2048
POST_TOOL_MAX_TOKENS = 4096


def _converse(
    messages: list[dict[str, Any]],
    force_tools: bool = False,
    max_tokens: int = SELECTION_MAX_TOKENS,
) -> dict[str, Any]:
    """Ask the model to select tools. It proposes; nothing here executes them."""
    tool_config = converse_tools.tool_config()
    if force_tools:
        # The first turn has no legitimate text-only outcome: the design requires
        # a tool plan to put in front of a human. Make the API enforce that rather
        # than hoping the model volunteers one and failing the request when it
        # narrates instead. Later turns stay on the default "auto" because the
        # model must be free to stop calling tools and write the final answer.
        tool_config["toolChoice"] = {"any": {}}
    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": AGENT_SYSTEM_PROMPT}],
        messages=messages,
        toolConfig=tool_config,
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
    )
    stop_reason = response.get("stopReason")
    if stop_reason not in {"tool_use", "end_turn", "max_tokens", "stop_sequence"}:
        raise RuntimeError(f"Converse returned an unusable stop reason: {stop_reason}")
    return response


def _select_tools(
    messages: list[dict[str, Any]],
    force_tools: bool = False,
    max_tokens: int = SELECTION_MAX_TOKENS,
    allow_truncation: bool = False,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """Run one model turn and return (updated transcript, text, proposed calls).

    The text is narration only whenever calls are present. Models routinely emit a
    reasoning preamble in the same message as a toolUse block, so the caller must
    discard that text rather than treat it as an answer; the gate is that no model
    prose reaches the user before approval, not that the model stayed silent.
    """
    response = _converse(messages, force_tools=force_tools, max_tokens=max_tokens)
    truncated = response.get("stopReason") == "max_tokens"
    message = conversation.assistant_turn(response)
    updated = [*messages, message]
    tool_uses = conversation.tool_uses(message)
    # A truncated tool selection is unsafe: half a call must never reach the
    # approval gate. A truncated summary is not, and refusing it would throw away
    # tool work the operator already approved and that already ran. The operator
    # facing answer is produced separately by the endpoint, so a clipped interim
    # summary costs detail, not correctness.
    if truncated and (tool_uses or not allow_truncation):
        raise RuntimeError(
            f"Converse truncated a tool selection at maxTokens={max_tokens}; "
            "a partial selection must not reach the approval gate"
        )
    text = "" if tool_uses else conversation.assistant_text(message)
    return updated, text, tool_uses


# The synthesizer turns ranked findings into prose, so it needs the narrative
# fields and the citations that ground them, not the raw rows behind each finding.
# source_records is 20 KB of the roughly 24 KB each finding occupies, so sending
# the whole blob put about 20k tokens in front of an 8B model with 743 MiB of GPU
# memory free after weights: generation died mid-stream with CUDA out of memory.
# The rows remain in evidence storage and the UI still renders them from there.
SYNTHESIS_BULK_FIELDS = ("source_records",)
MAX_SYNTHESIS_SUMMARY_CHARS = 4_000


def _synthesis_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Project approved evidence down to what the synthesizer actually reads."""
    findings = evidence.get("findings")
    if not isinstance(findings, list):
        return evidence
    projected: list[Any] = []
    for finding in findings:
        if not isinstance(finding, dict):
            projected.append(finding)
            continue
        trimmed = {key: value for key, value in finding.items() if key not in SYNTHESIS_BULK_FIELDS}
        for field in SYNTHESIS_BULK_FIELDS:
            rows = finding.get(field)
            if isinstance(rows, list):
                # Keep the count so the model can still speak to how much
                # evidence supports the finding without carrying the rows.
                trimmed[f"{field}_count"] = len(rows)
        projected.append(trimmed)
    return {**evidence, "findings": projected}


def _model_stream(query: str, agent_summary: str, evidence: dict[str, Any]):
    system = (
        "You are the final HDFS anomaly response synthesizer. Use only the supplied approved evidence. "
        "Return exactly three numbered findings. For each include: what happened, why it matters, "
        "confidence, citations, and a three-step human action plan. State that dataset labels were "
        "not used for ranking. Never claim an action was executed. Do not reveal chain-of-thought."
    )
    body = json.dumps(
        {
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": query,
                            "approved_agent_summary": agent_summary,
                            "approved_evidence": evidence,
                        },
                        separators=(",", ":"),
                    ),
                },
            ],
            "max_new_tokens": MAX_NEW_TOKENS,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    response = sagemaker.invoke_endpoint_with_response_stream(
        EndpointName=SAGEMAKER_ENDPOINT_NAME,
        Body=body,
        ContentType="application/json",
        Accept="text/plain",
    )
    decoder = codecs.getincrementaldecoder("utf-8")()
    for event in response["Body"]:
        if "PayloadPart" in event:
            text = decoder.decode(event["PayloadPart"]["Bytes"])
            if text:
                yield text
        elif "ModelStreamError" in event:
            raise RuntimeError(f"SageMaker model stream error: {event['ModelStreamError']}")
        elif "InternalStreamFailure" in event:
            raise RuntimeError(f"SageMaker internal stream failure: {event['InternalStreamFailure']}")
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail


def _claim(chat_id: str, expected_status: str, claimed_status: str) -> dict[str, Any] | None:
    try:
        response = table.update_item(
            Key={"workflow_id": chat_id},
            UpdateExpression="SET #status = :claimed, #step = :claimed, updated_at = :now",
            ConditionExpression="#status = :expected AND #kind = :kind",
            ExpressionAttributeNames={"#status": "status", "#step": "step", "#kind": "kind"},
            ExpressionAttributeValues={
                ":claimed": claimed_status,
                ":expected": expected_status,
                ":kind": "GRAPHRAG_CHAT",
                ":now": int(time.time()),
            },
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return None
        raise


def _result_count(result: dict[str, Any]) -> int:
    for field in ("findings", "results", "patterns", "plans"):
        value = result.get(field)
        if isinstance(value, list):
            return len(value)
    return 1


def _bounded_result(result: Any, tool: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RuntimeError(f"Approved MCP tool {tool} did not return a JSON object")
    encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
        raise RuntimeError(f"Approved MCP tool {tool} exceeded the bounded result contract")
    return result


def _transcript_key(owner_sub: str, chat_id: str) -> str:
    return f"chat-sessions/{owner_sub}/{chat_id}/transcript.json"


def _put_transcript(owner_sub: str, chat_id: str, messages: list[dict[str, Any]]) -> str:
    """Persist the transcript to S3 and return its hash.

    The transcript carries full tool results and grows with every approval
    round, so it lives in the encrypted evidence bucket rather than the
    DynamoDB item, which is capped at 400 KB.
    """
    body = conversation.serialize(messages)
    s3.put_object(
        Bucket=EVIDENCE_BUCKET,
        Key=_transcript_key(owner_sub, chat_id),
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=KMS_KEY_ARN,
        Metadata={"chat-id": chat_id},
    )
    return conversation.transcript_hash(messages)


def _get_transcript(owner_sub: str, chat_id: str, expected_hash: str) -> list[dict[str, Any]]:
    """Load the transcript and refuse it if it does not match the approved one."""
    response = s3.get_object(Bucket=EVIDENCE_BUCKET, Key=_transcript_key(owner_sub, chat_id))
    messages = conversation.deserialize(response["Body"].read())
    if conversation.transcript_hash(messages) != expected_hash:
        raise PermissionError("Stored transcript does not match the approved conversation")
    return messages


def _store_pending_approval(
    *,
    chat_id: str,
    owner_sub: str,
    query: str,
    messages: list[dict[str, Any]],
    tool_uses: list[dict[str, Any]],
    approval_round: int,
    history: list[dict[str, Any]],
    rank_evidence: dict[str, Any] | None,
    expected_status: str,
    emit,
) -> dict[str, Any]:
    plan = tool_approval.build_tool_plan(
        chat_id=chat_id,
        operator_query=query,
        approval_round=approval_round,
        invocation_id=converse_tools.invocation_id(tool_uses),
        invocation_inputs=converse_tools.to_invocation_inputs(tool_uses, query),
        created_at=int(time.time()),
    )
    transcript_hash = _put_transcript(owner_sub, chat_id, messages)
    # Ordered toolUseIds pair each approved call back to the block that proposed
    # it. They are kept outside the hash-bound plan so the approval schema, and
    # its tests, stay exactly as audited.
    tool_use_ids = [str(use["toolUseId"]) for use in tool_uses]
    public_state = {
        "chat_id": chat_id,
        "status": "AWAITING_TOOL_APPROVAL",
        "query": query,
        "pending_tool_plan": plan,
        "tool_approval_history": history,
        "labels_used_for_ranking": False,
    }
    values: dict[str, Any] = {
        ":awaiting": "AWAITING_TOOL_APPROVAL",
        ":expected": expected_status,
        ":state": _dynamodb_safe(public_state),
        ":plan": _dynamodb_safe(plan),
        ":hash": plan["plan_hash"],
        ":transcript": transcript_hash,
        ":tooluseids": tool_use_ids,
        ":round": approval_round,
        ":now": int(time.time()),
    }
    update = (
        "SET #status = :awaiting, #step = :awaiting, #state = :state, "
        "pending_tool_plan = :plan, plan_hash = :hash, transcript_hash = :transcript, "
        "tool_use_ids = :tooluseids, approval_round = :round, updated_at = :now"
    )
    if rank_evidence is not None:
        update += ", rank_evidence = :evidence"
        values[":evidence"] = _dynamodb_safe(rank_evidence)
    update += " REMOVE tool_approval"
    table.update_item(
        Key={"workflow_id": chat_id},
        UpdateExpression=update,
        ConditionExpression="#status = :expected",
        ExpressionAttributeNames={"#status": "status", "#step": "step", "#state": "state"},
        ExpressionAttributeValues=values,
    )
    for call in plan["calls"]:
        emit(
            "trace",
            {
                "tool": call["tool"],
                "reason": call["reason"],
                "authority": "awaiting human approval",
                "result": "The model proposed this call; AgentCore MCP has not executed it.",
            },
        )
    emit("approval_required", {"tool_plan": plan})
    return plan


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    chat_id = event["chat_id"]
    phase = event.get("phase", "PLAN")
    if phase not in {"PLAN", "EXECUTE"}:
        raise ValueError("GraphRAG chat phase must be PLAN or EXECUTE")
    item = _claim(
        chat_id,
        "QUEUED" if phase == "PLAN" else "TOOL_PLAN_APPROVED",
        "PLANNING" if phase == "PLAN" else "PROCESSING",
    )
    if item is None:
        return {"chat_id": chat_id, "status": "ALREADY_CLAIMED", "phase": phase}
    owner_sub = str(item.get("owner_sub", ""))
    if not owner_sub:
        raise PermissionError("Chat record is not bound to a Cognito subject")

    sequence = 0

    def emit(kind: str, payload: dict[str, Any]) -> None:
        nonlocal sequence
        sequence += 1
        _publish(owner_sub, chat_id, {"sequence": sequence, "type": kind, **payload})

    try:
        query = " ".join(str(item["query"]).split())
        _guardrail(query, "INPUT")

        if phase == "PLAN":
            emit(
                "status",
                {"stage": "GUARDRAIL", "message": "Input passed the Bedrock Guardrail."},
            )
            emit(
                "status",
                {
                    "stage": "AGENT_SELECTION",
                    "message": "The model is selecting a bounded read-only MCP tool plan.",
                },
            )
            messages, summary, tool_uses = _select_tools(
                conversation.start(query), force_tools=True
            )
            if not tool_uses:
                raise RuntimeError(
                    "The model did not propose a tool selection for human approval"
                )
            if summary:
                raise RuntimeError("The model answered before the mandatory tool approval gate")

            plan = _store_pending_approval(
                chat_id=chat_id,
                owner_sub=owner_sub,
                query=query,
                messages=messages,
                tool_uses=tool_uses,
                approval_round=1,
                history=[],
                rank_evidence=None,
                expected_status="PLANNING",
                emit=emit,
            )
            return {
                "chat_id": chat_id,
                "status": "AWAITING_TOOL_APPROVAL",
                "plan_hash": plan["plan_hash"],
            }

        state = _json_safe(item.get("state", {}))
        plan = tool_approval.validate_tool_plan(
            _json_safe(item.get("pending_tool_plan", {})),
            chat_id=chat_id,
            operator_query=query,
            now_epoch=int(time.time()),
        )
        approval = _json_safe(item.get("tool_approval", {}))
        if approval.get("approved") is not True or approval.get("plan_hash") != plan["plan_hash"]:
            raise PermissionError("Exact MCP tool-plan approval is missing or does not match")
        if int(approval.get("approval_round", 0)) != int(plan["approval_round"]):
            raise PermissionError("Tool approval round does not match the pending plan")

        # Reload the exact conversation the approved plan was derived from. The
        # hash check refuses a transcript that changed after approval.
        messages = _get_transcript(owner_sub, chat_id, str(item.get("transcript_hash", "")))
        tool_use_ids = [str(value) for value in (item.get("tool_use_ids") or [])]
        if len(tool_use_ids) != len(plan["calls"]):
            raise PermissionError("Stored tool-use identifiers do not match the approved plan")

        emit(
            "status",
            {
                "stage": "HUMAN_APPROVAL",
                "message": f"Exact tool plan {plan['plan_hash'][:12]}… was approved.",
            },
        )
        results: list[dict[str, Any]] = []
        rank_evidence = _json_safe(item.get("rank_evidence")) if item.get("rank_evidence") else None
        for call in plan["calls"]:
            result = _bounded_result(
                mcp_client.call_tool(call["tool"], call["arguments"]), call["tool"]
            )
            results.append(result)
            if call["tool"] == "rank_anomalies":
                if len(result.get("findings", [])) != 3:
                    raise RuntimeError("rank_anomalies did not satisfy the exact top-three contract")
                if result.get("labels_used_for_ranking") is not False:
                    raise RuntimeError("rank_anomalies did not attest to label-independent ranking")
                rank_evidence = result
                emit("findings", {"findings": result["findings"]})
            emit(
                "trace",
                {
                    "tool": call["tool"],
                    "reason": call["reason"],
                    "authority": "human-approved read-only",
                    "result": "Executed through the IAM-authorized AgentCore MCP Gateway.",
                    "result_count": _result_count(result),
                    "plan_hash": plan["plan_hash"],
                },
            )

        history = list(state.get("tool_approval_history", []))
        history.append(
            {
                "approval_round": plan["approval_round"],
                "plan_hash": plan["plan_hash"],
                "approved": True,
                "decided_at": approval["decided_at"],
                "calls": [
                    {
                        "tool": call["tool"],
                        "arguments": call["arguments"],
                        "authority": call["authority"],
                    }
                    for call in plan["calls"]
                ],
            }
        )
        # Feed the approved results back and let the model either propose another
        # round, which requires a fresh approval, or produce its summary.
        pending_uses = [{"toolUseId": tool_use_id} for tool_use_id in tool_use_ids]
        messages.append(conversation.tool_result_turn(pending_uses, results))
        messages, agent_summary, next_tool_uses = _select_tools(
            messages, max_tokens=POST_TOOL_MAX_TOKENS, allow_truncation=True
        )

        if next_tool_uses:
            if agent_summary:
                raise RuntimeError("The model mixed a final answer with another tool selection")
            next_plan = _store_pending_approval(
                chat_id=chat_id,
                owner_sub=owner_sub,
                query=query,
                messages=messages,
                tool_uses=next_tool_uses,
                approval_round=int(plan["approval_round"]) + 1,
                history=history,
                rank_evidence=rank_evidence,
                expected_status="PROCESSING",
                emit=emit,
            )
            return {
                "chat_id": chat_id,
                "status": "AWAITING_TOOL_APPROVAL",
                "plan_hash": next_plan["plan_hash"],
            }

        if not agent_summary:
            raise RuntimeError("The model returned no summary after approved MCP execution")
        if rank_evidence is None or len(rank_evidence.get("findings", [])) != 3:
            raise RuntimeError("Approved GraphRAG session completed without exact top-three evidence")

        answer_parts: list[str] = []
        guarded_batch = ""
        guard_tail = ""
        for chunk in _model_stream(
            query,
            agent_summary[:MAX_SYNTHESIS_SUMMARY_CHARS],
            _synthesis_evidence(rank_evidence),
        ):
            guarded_batch += chunk
            if len(guarded_batch) < 320 and not guarded_batch.endswith((". ", "\n")):
                continue
            _guardrail(guard_tail + guarded_batch, "OUTPUT")
            answer_parts.append(guarded_batch)
            emit("token", {"text": guarded_batch})
            guard_tail = (guard_tail + guarded_batch)[-200:]
            guarded_batch = ""
        if guarded_batch:
            _guardrail(guard_tail + guarded_batch, "OUTPUT")
            answer_parts.append(guarded_batch)
            emit("token", {"text": guarded_batch})
        answer = "".join(answer_parts).strip()
        if not answer:
            raise RuntimeError("SageMaker endpoint returned no answer text")

        final_state = {
            "chat_id": chat_id,
            "status": "COMPLETE",
            "query": query,
            "answer": answer,
            "findings": rank_evidence["findings"],
            "tool_approval_history": history,
            "labels_used_for_ranking": False,
            "updated_at": int(time.time()),
        }
        final_body = json.dumps(
            final_state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        final_hash = hashlib.sha256(final_body).hexdigest()
        final_key = f"chat-sessions/{owner_sub}/{chat_id}/final.json"
        s3.put_object(
            Bucket=EVIDENCE_BUCKET,
            Key=final_key,
            Body=final_body,
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=KMS_KEY_ARN,
            Metadata={"chat-id": chat_id, "sha256": final_hash},
        )
        final_state["evidence_uri"] = f"s3://{EVIDENCE_BUCKET}/{final_key}"
        final_state["evidence_sha256"] = final_hash
        table.update_item(
            Key={"workflow_id": chat_id},
            UpdateExpression=(
                "SET #status = :status, #step = :status, #state = :state, updated_at = :now "
                "REMOVE pending_tool_plan, plan_hash, tool_approval, rank_evidence"
            ),
            ConditionExpression="#status = :processing",
            ExpressionAttributeNames={"#status": "status", "#step": "step", "#state": "state"},
            ExpressionAttributeValues={
                ":status": "COMPLETE",
                ":processing": "PROCESSING",
                ":state": _dynamodb_safe(final_state),
                ":now": int(time.time()),
            },
        )
        emit("complete", {"status": "COMPLETE"})
        return {"chat_id": chat_id, "status": "COMPLETE"}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        table.update_item(
            Key={"workflow_id": chat_id},
            UpdateExpression="SET #status = :status, #step = :status, error_message = :error, updated_at = :now",
            ExpressionAttributeNames={"#status": "status", "#step": "step"},
            ExpressionAttributeValues={
                ":status": "FAILED",
                ":error": error[:2000],
                ":now": int(time.time()),
            },
        )
        try:
            emit("error", {"status": "FAILED", "message": error[:1000]})
        except Exception:
            pass
        raise
