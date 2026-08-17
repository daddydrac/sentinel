"""Fail-closed gates and single-flight lease for the GraphRAG ingestion state machine."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError


BUCKET = os.environ["EVIDENCE_BUCKET"]
DATASET_REVISION = os.environ["DATASET_REVISION"]
TABLE_NAME = os.environ["TABLE_NAME"]
GRAPHRAG_TOOL_FUNCTION = os.environ["GRAPHRAG_TOOL_FUNCTION"]
MINIMUM_BYTES = int(os.environ.get("MINIMUM_BYTES", str(100 * 1024**3)))

s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")
table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _run_id(event: dict[str, Any]) -> str:
    run_id = event.get("run_id")
    if not isinstance(run_id, str) or not run_id.replace("-", "").isalnum() or len(run_id) > 80:
        raise ValueError("run_id must contain only letters, numbers, and hyphens")
    return run_id


def _manifest(key: str) -> dict[str, Any]:
    response = s3.get_object(Bucket=BUCKET, Key=key)
    payload = json.loads(response["Body"].read())
    if not isinstance(payload, dict):
        raise RuntimeError(f"Manifest {key} is not a JSON object")
    return payload


def _published_manifest(run_id: str) -> tuple[str, dict[str, Any]]:
    prefix = f"hpc/graphrag/runs/{run_id}/manifests/published/"
    paginator = s3.get_paginator("list_objects_v2")
    keys = sorted(
        item["Key"]
        for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix)
        for item in page.get("Contents", [])
        if int(item.get("Size") or 0) > 0 and item["Key"].rsplit("/", 1)[-1].startswith("part-")
    )
    if len(keys) != 1:
        raise RuntimeError(f"Expected one published manifest object for {run_id}; found {len(keys)}")
    return keys[0], _manifest(keys[0])


def _lock(run_id: str) -> dict[str, Any]:
    now = int(time.time())
    try:
        table.put_item(
            Item={
                "workflow_id": "lock#graphrag-build",
                "kind": "SINGLEFLIGHT_LOCK",
                "owner_run_id": run_id,
                "expires_at": now + 14400,
            },
            ConditionExpression="attribute_not_exists(workflow_id) OR expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise RuntimeError("Another GraphRAG ingestion owns the single-flight lease") from exc
        raise
    return {"phase": "LOCK", "run_id": run_id, "lease_expires_at": now + 14400}


def _unlock(run_id: str) -> dict[str, Any]:
    try:
        table.delete_item(
            Key={"workflow_id": "lock#graphrag-build"},
            ConditionExpression="owner_run_id = :owner",
            ExpressionAttributeValues={":owner": run_id},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
    return {"phase": "UNLOCK", "run_id": run_id}


def _validate_acquisition(run_id: str) -> dict[str, Any]:
    key = f"hpc/raw/source/{DATASET_REVISION}/acquisition-manifest.json"
    manifest = _manifest(key)
    if (
        manifest.get("status") != "READY"
        or manifest.get("revision") != DATASET_REVISION
        or manifest.get("license") != "mit"
        or int(manifest.get("parquet_file_count") or 0) <= 0
        or int(manifest.get("total_bytes") or 0) <= 0
    ):
        raise RuntimeError("Managed dataset acquisition manifest failed its contract")
    return {"phase": "VALIDATE_ACQUISITION", "run_id": run_id, "manifest_key": key}


def _validate_generated(run_id: str) -> dict[str, Any]:
    paginator = s3.get_paginator("list_objects_v2")
    physical_bytes = sum(
        int(item.get("Size") or 0)
        for page in paginator.paginate(Bucket=BUCKET, Prefix="hpc/staged-100g/")
        for item in page.get("Contents", [])
    )
    if physical_bytes < MINIMUM_BYTES:
        raise RuntimeError(f"Physical staged corpus is below 100 GiB: {physical_bytes}")
    return {"phase": "VALIDATE_GENERATED", "run_id": run_id, "physical_bytes": physical_bytes}


def _validate_published(run_id: str) -> dict[str, Any]:
    key, manifest = _published_manifest(run_id)
    if (
        manifest.get("status") != "PUBLISHED"
        or manifest.get("run_id") != run_id
        or manifest.get("dataset_revision") != DATASET_REVISION
        or manifest.get("slo_passed_inside_spark") is not True
        or manifest.get("l2_normalized") is not True
        or manifest.get("labels_isolated") is not True
        or manifest.get("run_isolated") is not True
        or manifest.get("neptune_relationships_validated") is not True
        or int(manifest.get("input_payload_bytes") or 0) < MINIMUM_BYTES
        # Reconcile like with like. neptune_records is the loader's
        # property-statement count, so comparing it against the CSV row count in
        # expected_graph_records can never hold: a node row with thirteen property
        # columns is thirteen statements. The node and edge counts below are both
        # measured in rows and are the real contract.
        or int(manifest.get("neptune_loaded_nodes") or -1)
        != int(manifest.get("expected_node_records") or -2)
        or int(manifest.get("neptune_loaded_edges") or -1)
        != int(manifest.get("expected_edge_records") or -2)
    ):
        raise RuntimeError("Published GraphRAG manifest failed its reconciliation contract")
    response = lambda_client.invoke(
        FunctionName=GRAPHRAG_TOOL_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {
                "_tool": "rank_anomalies",
                "query": "Identify the three most important anomalous HDFS log behaviors",
                "top_k": "3",
            }
        ).encode("utf-8"),
    )
    if response.get("FunctionError"):
        raise RuntimeError("Published GraphRAG smoke query failed in the read-only tool Lambda")
    smoke = json.loads(response["Payload"].read())
    if smoke.get("labels_used_for_ranking") is not False or len(smoke.get("findings", [])) != 3:
        raise RuntimeError("Published GraphRAG smoke query violated the top-three contract")
    return {
        "phase": "VALIDATE_PUBLISHED",
        "run_id": run_id,
        "manifest_key": key,
        "elapsed_seconds_inside_spark": manifest["elapsed_seconds_inside_spark"],
        "query_ready_smoke_passed": True,
    }


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("Pipeline gate event must be an object")
    run_id = _run_id(event)
    phase = event.get("phase")
    if phase == "LOCK":
        return _lock(run_id)
    if phase == "UNLOCK":
        return _unlock(run_id)
    if phase == "VALIDATE_ACQUISITION":
        return _validate_acquisition(run_id)
    if phase == "VALIDATE_GENERATED":
        return _validate_generated(run_id)
    if phase == "VALIDATE_PUBLISHED":
        return _validate_published(run_id)
    raise ValueError(f"Unsupported pipeline gate phase: {phase}")
