#!/usr/bin/env python3
"""Dependency-free local runner for the same bounded demo behavior."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


APP_DIR = Path(__file__).resolve().parents[1] / "lambda" / "app"
sys.path.insert(0, str(APP_DIR))
from domain import core  # noqa: E402


class Store:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.decisions: dict[str, tuple[threading.Event, bool | None]] = {}
        self.lock = threading.Lock()

    def put(self, state: dict) -> None:
        with self.lock:
            self.items[state["workflow_id"]] = copy.deepcopy(state)

    def get(self, workflow_id: str) -> dict | None:
        with self.lock:
            item = self.items.get(workflow_id)
            return copy.deepcopy(item) if item else None

    def create_approval(self, workflow_id: str) -> threading.Event:
        with self.lock:
            event = threading.Event()
            self.decisions[workflow_id] = (event, None)
            return event

    def decide(self, workflow_id: str, approved: bool) -> bool:
        with self.lock:
            if workflow_id not in self.decisions:
                return False
            event, _ = self.decisions[workflow_id]
            self.decisions[workflow_id] = (event, approved)
            event.set()
            return True

    def decision(self, workflow_id: str) -> bool | None:
        with self.lock:
            return self.decisions[workflow_id][1]


STORE = Store()
TOKEN = os.getenv("DEMO_TOKEN", "local-demo-token")
UI = (APP_DIR / "handlers" / "ui.html").read_text(encoding="utf-8")


def run_workflow(workflow_id: str, scenario_id: str) -> None:
    scenario = core.load_scenarios()[scenario_id]
    state = {"workflow_id": workflow_id, "scenario_id": scenario_id, "created_at": core.utc_now(), "trace": []}
    state.update({"title": scenario["title"], "severity": scenario["severity"]})
    core.append_trace(state, "INTAKE", "RUNNING", "Caller and scenario accepted; tenant is demo-tenant.")
    STORE.put(state)

    state["evidence"] = core.retrieve(scenario)
    core.append_trace(state, "RETRIEVE", "RUNNING", "Four retrieval branches assembled an evidence manifest.")
    STORE.put(state)

    state["plan"] = core.build_plan(scenario, state["evidence"])
    core.append_trace(state, "PLAN", "RUNNING", "Created a schema-valid plan bound to evidence and rollback.")
    STORE.put(state)

    state["policy"] = core.decide_policy(state["plan"])
    core.append_trace(state, "POLICY", "RUNNING", f"Policy returned {state['policy']['decision']}.")
    STORE.put(state)

    if state["policy"]["decision"] == "DENY":
        core.append_trace(state, "DENY", "DENIED", "Policy denied execution.")
        STORE.put(state)
        return

    if state["policy"]["decision"] == "REQUIRE_APPROVAL":
        event = STORE.create_approval(workflow_id)
        core.append_trace(state, "APPROVAL", "AWAITING_APPROVAL", "Waiting for a decision bound to the plan hash.")
        STORE.put(state)
        if not event.wait(timeout=900):
            core.append_trace(state, "DENY", "DENIED", "Approval expired.")
            STORE.put(state)
            return
        if not STORE.decision(workflow_id):
            core.append_trace(state, "DENY", "DENIED", "Approver denied the plan.")
            STORE.put(state)
            return

    state["execution"] = core.execute_simulated(scenario, state["plan"], workflow_id)
    core.append_trace(state, "EXECUTE", "RUNNING", "Simulated one idempotent, canary-scoped tool action.")
    STORE.put(state)

    state["verification"] = core.verify_simulated(scenario, state["execution"])
    core.append_trace(state, "VERIFY", "RUNNING", f"Independent verification {'passed' if state['verification']['passed'] else 'failed'}.")
    STORE.put(state)
    if state["verification"]["passed"]:
        core.append_trace(state, "COMPLETE", "VERIFIED", "The authoritative verification gate passed.")
    else:
        state["compensation"] = core.compensate_simulated(scenario, state["plan"], workflow_id)
        core.append_trace(state, "COMPENSATE", "COMPENSATED", "Rollback completed and incident escalated.")
    STORE.put(state)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[demo] {self.address_string()} {fmt % args}")

    def send_payload(self, status: int, payload: object, content_type: str = "application/json") -> None:
        body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return self.headers.get("x-demo-token") == TOKEN

    def body(self) -> dict:
        length = int(self.headers.get("content-length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self.send_payload(200, UI, "text/html; charset=utf-8")
            return
        if not self.authorized():
            self.send_payload(401, {"error": "Missing or invalid X-Demo-Token"})
            return
        if path == "/api/scenarios":
            self.send_payload(200, {"scenarios": core.scenario_summaries()})
            return
        if path.startswith("/api/workflows/"):
            workflow_id = path.rsplit("/", 1)[-1]
            state = STORE.get(workflow_id)
            self.send_payload(200, state) if state else self.send_payload(404, {"error": "Workflow not found"})
            return
        self.send_payload(404, {"error": "Route not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self.authorized():
            self.send_payload(401, {"error": "Missing or invalid X-Demo-Token"})
            return
        if path == "/api/workflows":
            scenario_id = self.body().get("scenario_id")
            if scenario_id not in core.load_scenarios():
                self.send_payload(400, {"error": "Unknown scenario_id"})
                return
            workflow_id = f"wf-{uuid.uuid4()}"
            STORE.put(
                {
                    "workflow_id": workflow_id,
                    "scenario_id": scenario_id,
                    "created_at": core.utc_now(),
                    "step": "INTAKE",
                    "status": "QUEUED",
                    "trace": [],
                }
            )
            threading.Thread(target=run_workflow, args=(workflow_id, scenario_id), daemon=True).start()
            self.send_payload(202, {"workflow_id": workflow_id})
            return
        if path.startswith("/api/workflows/") and path.endswith("/decision"):
            workflow_id = path.split("/")[3]
            state = STORE.get(workflow_id)
            payload = self.body()
            if not state or state.get("status") != "AWAITING_APPROVAL":
                self.send_payload(409, {"error": "Workflow is not awaiting approval"})
                return
            if payload.get("plan_hash") != state["plan"]["plan_hash"]:
                self.send_payload(409, {"error": "Plan hash mismatch; approval is invalid"})
                return
            if not isinstance(payload.get("approved"), bool):
                self.send_payload(400, {"error": "approved must be a JSON boolean"})
                return
            if not STORE.decide(workflow_id, payload["approved"]):
                self.send_payload(409, {"error": "Approval callback not registered"})
                return
            self.send_payload(202, {"workflow_id": workflow_id, "approved": payload["approved"]})
            return
        self.send_payload(404, {"error": "Route not found"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Demo: http://{args.host}:{args.port}/?token={TOKEN}")
    server.serve_forever()


if __name__ == "__main__":
    main()
