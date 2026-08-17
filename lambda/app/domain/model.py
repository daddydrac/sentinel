"""Shared domain primitives and fixture loading."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIXTURE_PATH = Path(__file__).with_name("fixtures.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_scenarios() -> dict[str, dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return {item["id"]: item for item in payload["scenarios"]}


def scenario_summaries() -> list[dict[str, str]]:
    return [
        {
            "id": scenario["id"],
            "title": scenario["title"],
            "severity": scenario["severity"],
            "summary": scenario["summary"],
        }
        for scenario in load_scenarios().values()
    ]


def append_trace(state: dict[str, Any], step: str, status: str, detail: str) -> dict[str, Any]:
    state.setdefault("trace", []).append(
        {"step": step, "status": status, "detail": detail, "at": utc_now()}
    )
    state["step"] = step
    state["status"] = status
    state["updated_at"] = utc_now()
    return state
