import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "e2e-test-token"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = {**os.environ, "DEMO_TOKEN": TOKEN}
        cls.server = subprocess.Popen(
            [sys.executable, str(ROOT / "local" / "server.py"), "--port", str(cls.port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(40):
            try:
                cls.request("GET", "/api/scenarios")
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise RuntimeError("Local demo server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    @classmethod
    def request(cls, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}{path}",
            data=data,
            method=method,
            headers={"x-demo-token": TOKEN, "content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read())

    @classmethod
    def wait_for(cls, workflow_id, statuses, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = cls.request("GET", f"/api/workflows/{workflow_id}")
            if state["status"] in statuses:
                return state
            time.sleep(0.05)
        raise AssertionError(f"workflow {workflow_id} did not reach {statuses}")

    def run_approved_scenario(self, scenario_id):
        created = self.request("POST", "/api/workflows", {"scenario_id": scenario_id})
        waiting = self.wait_for(created["workflow_id"], {"AWAITING_APPROVAL"})
        self.request(
            "POST",
            f"/api/workflows/{created['workflow_id']}/decision",
            {"approved": True, "plan_hash": waiting["plan"]["plan_hash"]},
        )
        return created["workflow_id"]

    def test_success_path_reaches_verified(self):
        workflow_id = self.run_approved_scenario("storage_path_regression")
        final = self.wait_for(workflow_id, {"VERIFIED", "COMPENSATED", "DENIED"})
        self.assertEqual("VERIFIED", final["status"])
        self.assertTrue(final["verification"]["passed"])

    def test_failed_verification_compensates(self):
        workflow_id = self.run_approved_scenario("verification_failure")
        final = self.wait_for(workflow_id, {"VERIFIED", "COMPENSATED", "DENIED"})
        self.assertEqual("COMPENSATED", final["status"])
        self.assertEqual("restore_previous_replica", final["compensation"]["action"])

    def test_plan_hash_mismatch_is_rejected(self):
        created = self.request("POST", "/api/workflows", {"scenario_id": "storage_path_regression"})
        self.wait_for(created["workflow_id"], {"AWAITING_APPROVAL"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(
                "POST",
                f"/api/workflows/{created['workflow_id']}/decision",
                {"approved": True, "plan_hash": "tampered"},
            )
        self.assertEqual(409, error.exception.code)
