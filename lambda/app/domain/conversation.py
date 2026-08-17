"""Persisted Converse conversation state for the governed GraphRAG chat.

Bedrock Agents held conversation state server-side behind a session identifier.
Converse is stateless, so the transcript becomes this application's state, and it
must survive across separate Lambda invocations: the model proposes tools in one
invocation, a human approves in another, and the tools execute in a third.

That is an improvement rather than a cost. The transcript is now inspectable,
hashable, and replayable, so an approval can be bound to the exact conversation
that produced it instead of to an opaque vendor session.

This module is deliberately free of AWS clients so the state transitions are
unit-testable without network or credentials. The caller owns storage.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


# A single tool result is already bounded by the chat handler. This bounds the
# whole transcript, which grows by one assistant turn and one result turn per
# approval round, so a runaway loop cannot produce an unstorable object.
MAX_TRANSCRIPT_BYTES = 3_000_000


class ConversationError(RuntimeError):
    """Raised when a Converse payload does not match the expected contract."""


def start(prompt: str) -> list[dict[str, Any]]:
    """Open a transcript with the operator's guardrailed question."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ConversationError("Conversation prompt must be a non-empty string")
    return [{"role": "user", "content": [{"text": prompt}]}]


def assistant_turn(response: dict[str, Any]) -> dict[str, Any]:
    """Extract the assistant message from a Converse response, fail-closed."""
    message = response.get("output", {}).get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ConversationError("Converse response did not contain an assistant message")
    if not isinstance(message.get("content"), list):
        raise ConversationError("Converse assistant message has no content list")
    return message


def tool_uses(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the toolUse blocks the model emitted, in order."""
    uses: list[dict[str, Any]] = []
    for block in message.get("content", []):
        if not isinstance(block, dict):
            continue
        use = block.get("toolUse")
        if use is None:
            continue
        if not isinstance(use, dict) or not use.get("toolUseId") or not use.get("name"):
            raise ConversationError("Converse toolUse block is missing an id or name")
        uses.append(use)
    return uses


def assistant_text(message: dict[str, Any]) -> str:
    """Concatenate the assistant's visible text blocks."""
    parts = [
        block["text"]
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "".join(parts).strip()


def tool_result_turn(
    tool_uses_in_order: list[dict[str, Any]], results: list[Any]
) -> dict[str, Any]:
    """Build the user turn carrying one result per proposed tool call.

    Converse requires exactly one toolResult per toolUse in the preceding
    assistant turn; a mismatch is rejected by the API. Checking it here turns
    that into a clear local failure instead of an opaque validation error.
    """
    if len(tool_uses_in_order) != len(results):
        raise ConversationError(
            f"Expected {len(tool_uses_in_order)} tool results, received {len(results)}"
        )
    content = []
    for use, result in zip(tool_uses_in_order, results, strict=True):
        content.append(
            {
                "toolResult": {
                    "toolUseId": use["toolUseId"],
                    "content": [{"json": result}],
                    "status": "success",
                }
            }
        )
    return {"role": "user", "content": content}


def transcript_hash(messages: list[dict[str, Any]]) -> str:
    """Hash the transcript so an approval can be bound to the exact history."""
    payload = json.dumps(messages, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def serialize(messages: list[dict[str, Any]]) -> bytes:
    """Encode a transcript for storage, refusing anything unstorable."""
    if not isinstance(messages, list) or not messages:
        raise ConversationError("Refusing to serialize an empty transcript")
    encoded = json.dumps(
        messages, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(encoded) > MAX_TRANSCRIPT_BYTES:
        raise ConversationError(
            f"Transcript is {len(encoded)} bytes, above the {MAX_TRANSCRIPT_BYTES} limit"
        )
    return encoded


def deserialize(raw: bytes) -> list[dict[str, Any]]:
    """Decode a stored transcript, validating its shape before use."""
    try:
        messages = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversationError("Stored transcript is not valid JSON") from exc
    if not isinstance(messages, list) or not messages:
        raise ConversationError("Stored transcript is not a non-empty list")
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.get("role") not in {"user", "assistant"}
            or not isinstance(message.get("content"), list)
        ):
            raise ConversationError("Stored transcript contains a malformed message")
    return messages
