"""Build the production HDFS GraphRAG stores from a staged 100 GiB Parquet corpus.

This EMR Serverless Spark job is deliberately fail-closed. It creates a run-scoped
OpenSearch record index, a FAISS/HNSW semantic-pattern index, and Neptune bulk-load
CSV files. Aliases are moved only after both stores pass their validation gates.
Ground-truth labels are written to an isolated evaluation prefix and are never
included in retrieval or anomaly scoring.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from typing import Any


def l2_normalize(values: Iterable[float], expected_dimension: int) -> list[float]:
    vector = [float(value) for value in values]
    if len(vector) != expected_dimension:
        raise ValueError(
            f"Embedding dimension mismatch: expected {expected_dimension}, received {len(vector)}"
        )
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Embedding contains a non-finite value")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        raise ValueError("Embedding has zero L2 norm")
    normalized = [value / norm for value in vector]
    check = math.sqrt(sum(value * value for value in normalized))
    if abs(check - 1.0) > 1e-5:
        raise ValueError(f"L2 normalization validation failed: norm={check}")
    return normalized


def opensearch_mapping(
    dimension: int, shards: int, replicas: int, remote_index_build: bool = True
) -> dict[str, Any]:
    if dimension <= 0 or shards <= 0 or replicas < 0:
        raise ValueError("Invalid OpenSearch mapping dimensions or shard settings")
    index_settings: dict[str, Any] = {
        "knn": True,
        "knn.advanced.approximate_threshold": 10000,
        "number_of_shards": shards,
        "number_of_replicas": replicas,
        "refresh_interval": "-1",
    }
    # Remote GPU-accelerated FAISS segment builds exist only on domains created with
    # serverless_vector_acceleration. Smaller domains reject the setting outright, so
    # the caller declares it rather than the mapping assuming it.
    if remote_index_build:
        index_settings["knn.remote_index_build.enabled"] = True
    return {
        "settings": {"index": index_settings},
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "pattern_id": {"type": "keyword"},
                "text": {"type": "text"},
                "event_codes": {"type": "keyword"},
                "host_ids": {"type": "keyword"},
                "datanode_ids": {"type": "keyword"},
                "occurrence_count": {"type": "long"},
                "rarity_score": {"type": "float"},
                "structural_score": {"type": "float"},
                "anomaly_score": {"type": "float"},
                "embedding_model": {"type": "keyword"},
                "embedding_dimension": {"type": "integer"},
                "normalization": {"type": "keyword"},
                "content_hash": {"type": "keyword"},
                "dataset_revision": {"type": "keyword"},
                "corpus_run_id": {"type": "keyword"},
                "pipeline_run_id": {"type": "keyword"},
                "provenance_uri": {"type": "keyword", "index": False},
                "tenant_id": {"type": "keyword"},
                "classification": {"type": "keyword"},
                "graph_entity_ids": {"type": "keyword"},
                "pre_normalization_norm": {"type": "float"},
                "post_normalization_norm": {"type": "float"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dimension,
                    "space_type": "innerproduct",
                    "method": {
                        "name": "hnsw",
                        "engine": "faiss",
                        "parameters": {"ef_construction": 256, "m": 32},
                    },
                },
            },
        },
    }


def record_mapping(shards: int, replicas: int) -> dict[str, Any]:
    return {
        "settings": {
            "index": {
                "number_of_shards": shards,
                "number_of_replicas": replicas,
                "refresh_interval": "-1",
            }
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "record_id": {"type": "keyword"},
                "source_block_id": {"type": "keyword"},
                "pattern_id": {"type": "keyword"},
                "replica": {"type": "long"},
                "text": {"type": "text"},
                "raw_event_preview": {"type": "text", "index": False},
                "event_codes": {"type": "keyword"},
                "host_ids": {"type": "keyword"},
                "datanode_ids": {"type": "keyword"},
                "anomaly_score": {"type": "float"},
                "source_uri": {"type": "keyword", "index": False},
                "provenance_uri": {"type": "keyword", "index": False},
                "source_hash": {"type": "keyword"},
                "dataset_revision": {"type": "keyword"},
                "corpus_run_id": {"type": "keyword"},
                "pipeline_run_id": {"type": "keyword"},
                "tenant_id": {"type": "keyword"},
                "classification": {"type": "keyword"},
                "graph_entity_ids": {"type": "keyword"},
            },
        },
    }


def _signed_request(
    method: str,
    url: str,
    service: str,
    region: str,
    body: bytes = b"",
    content_type: str = "application/json",
    timeout: int = 60,
) -> tuple[int, str]:
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials are available for SigV4")
    headers = {"content-type": content_type, "host": urllib.parse.urlparse(url).netloc}
    request = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(credentials.get_frozen_credentials(), service, region).add_auth(request)
    prepared = request.prepare()
    outgoing = urllib.request.Request(
        prepared.url,
        data=body if method not in {"GET", "HEAD"} else None,
        method=method,
        headers=dict(prepared.headers.items()),
    )
    try:
        with urllib.request.urlopen(outgoing, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{service} {method} {url} failed with HTTP {exc.code}: {payload}") from exc


def _json_request(
    method: str, endpoint: str, path: str, service: str, region: str, payload: Any | None = None
) -> dict[str, Any]:
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    _, raw = _signed_request(
        method,
        f"https://{endpoint.rstrip('/')}/{path.lstrip('/')}",
        service,
        region,
        body,
    )
    return json.loads(raw) if raw.strip() else {}


def _ensure_new_index(endpoint: str, name: str, mapping: dict[str, Any], region: str) -> None:
    # HEAD returns 404 for a safe-to-create index; urllib raises for that expected response.
    try:
        _signed_request("HEAD", f"https://{endpoint}/{name}", "es", region, timeout=30)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
    else:
        raise RuntimeError(f"Run-scoped OpenSearch index already exists: {name}")
    _json_request("PUT", endpoint, name, "es", region, mapping)


def _alias_indices(endpoint: str, alias: str, region: str) -> list[str]:
    try:
        _, raw = _signed_request(
            "GET", f"https://{endpoint.rstrip('/')}/_alias/{alias}", "es", region, timeout=30
        )
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return []
        raise
    payload = json.loads(raw)
    return sorted(str(index) for index in payload)


def _delete_s3_prefix(uri: str) -> None:
    """Delete only a failed run's publication marker; keep diagnostic artifacts."""
    import boto3

    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Expected a non-root S3 URI, received {uri}")
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/").rstrip("/") + "/"
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    pending: list[dict[str, str]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        pending.extend({"Key": item["Key"]} for item in page.get("Contents", []))
        while len(pending) >= 1000:
            client.delete_objects(Bucket=bucket, Delete={"Objects": pending[:1000], "Quiet": True})
            pending = pending[1000:]
    if pending:
        client.delete_objects(Bucket=bucket, Delete={"Objects": pending, "Quiet": True})


def _nova_embeddings(
    rows: Iterator[Any], model_id: str, dimension: int, region: str
) -> Iterator[
    tuple[
        str,
        str,
        list[str],
        int,
        float,
        float,
        float,
        list[str],
        list[str],
        list[float],
        float,
        float,
    ]
]:
    import boto3
    from botocore.config import Config

    runtime = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"mode": "adaptive", "max_attempts": 10}),
    )
    for row in rows:
        request = {
            "taskType": "SINGLE_EMBEDDING",
            "singleEmbeddingParams": {
                "embeddingPurpose": "GENERIC_INDEX",
                "embeddingDimension": dimension,
                "text": {"truncationMode": "END", "value": row.text[:8192]},
            },
        }
        response = runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(request).encode("utf-8"),
            accept="application/json",
            contentType="application/json",
        )
        decoded = json.loads(response["body"].read())
        candidates = decoded.get("embeddings") or decoded.get("embedding")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            candidates = candidates[0].get("embedding")
        if not isinstance(candidates, list):
            raise RuntimeError("Nova embedding response did not contain an embedding vector")
        raw_vector = [float(value) for value in candidates]
        pre_norm = math.sqrt(sum(value * value for value in raw_vector))
        normalized = l2_normalize(raw_vector, dimension)
        post_norm = math.sqrt(sum(value * value for value in normalized))
        yield (
            row.pattern_id,
            row.text,
            list(row.event_codes or []),
            int(row.occurrence_count),
            float(row.rarity_score),
            float(row.structural_score),
            float(row.anomaly_score),
            list(row.host_ids or []),
            list(row.datanode_ids or []),
            normalized,
            pre_norm,
            post_norm,
        )


def _bulk_partition(
    rows: Iterator[Any], endpoint: str, index: str, region: str, id_field: str
) -> Iterator[tuple[int, int]]:
    documents: list[str] = []
    document_count = 0
    byte_count = 0

    def flush() -> tuple[int, int]:
        nonlocal documents, document_count, byte_count
        if not documents:
            return 0, 0
        body = ("\n".join(documents) + "\n").encode("utf-8")
        # The domain sets rest.action.multi.allow_explicit_index=false, which
        # rejects an _index inside the bulk action metadata so a bulk writer
        # cannot target arbitrary indices. Address the index in the path instead
        # and keep that restriction in place.
        _, raw = _signed_request(
            "POST",
            f"https://{endpoint}/{index}/_bulk",
            "es",
            region,
            body,
            content_type="application/x-ndjson",
            timeout=120,
        )
        result = json.loads(raw)
        if result.get("errors"):
            failures = [
                item for item in result.get("items", []) if next(iter(item.values())).get("error")
            ][:3]
            raise RuntimeError(f"OpenSearch bulk request contained failures: {failures}")
        sent = document_count
        sent_bytes = byte_count
        documents = []
        document_count = 0
        byte_count = 0
        return sent, sent_bytes

    for row in rows:
        document = row.asDict(recursive=True)
        doc_id = document[id_field]
        action = json.dumps({"index": {"_id": doc_id}}, separators=(",", ":"))
        source = json.dumps(document, separators=(",", ":"), ensure_ascii=False)
        documents.extend((action, source))
        document_count += 1
        byte_count += len(action) + len(source) + 2
        if document_count >= 1000 or byte_count >= 5 * 1024 * 1024:
            yield flush()
    if documents:
        yield flush()


def _neptune_load(
    endpoint: str,
    port: int,
    source: str,
    loader_role_arn: str,
    region: str,
    timeout_seconds: int,
) -> tuple[str, int]:
    payload = {
        "source": source,
        "format": "csv",
        "iamRoleArn": loader_role_arn,
        "region": region,
        "failOnError": "TRUE",
        "parallelism": "OVERSUBSCRIBE",
        "updateSingleCardinalityProperties": "FALSE",
        "queueRequest": "FALSE",
        "mode": "NEW",
    }
    result = _json_request("POST", f"{endpoint}:{port}", "loader", "neptune-db", region, payload)
    load_id = result.get("payload", {}).get("loadId")
    if not load_id:
        raise RuntimeError(f"Neptune loader did not return a load ID: {result}")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = _json_request(
            "GET", f"{endpoint}:{port}", f"loader/{load_id}?details=true", "neptune-db", region
        )
        overall = status.get("payload", {}).get("overallStatus", {})
        state = overall.get("status")
        if state == "LOAD_COMPLETED":
            error_fields = (
                "parsingErrors",
                "datatypeMismatchErrors",
                "insertErrors",
            )
            errors = sum(int(overall.get(field) or 0) for field in error_fields)
            total_records = int(overall.get("totalRecords") or 0)
            if errors != 0 or total_records <= 0:
                raise RuntimeError(
                    f"Neptune load {load_id} completed without a valid zero-error count: {overall}"
                )
            return load_id, total_records
        if state in {"LOAD_FAILED", "LOAD_CANCELLED", "LOAD_NOT_STARTED", "LOAD_UNEXPECTED_ERROR"}:
            raise RuntimeError(f"Neptune load {load_id} failed: {status}")
        time.sleep(5)
    raise TimeoutError(f"Neptune loader {load_id} exceeded {timeout_seconds} seconds")


def _neptune_cypher(
    endpoint: str,
    port: int,
    region: str,
    query: str,
    parameters: dict[str, Any],
    timeout: int = 120,
) -> list[dict[str, Any]]:
    body = urllib.parse.urlencode(
        {"query": query, "parameters": json.dumps(parameters, separators=(",", ":"))}
    ).encode("utf-8")
    _, raw = _signed_request(
        "POST",
        f"https://{endpoint}:{port}/openCypher",
        "neptune-db",
        region,
        body,
        content_type="application/x-www-form-urlencoded",
        timeout=timeout,
    )
    response = json.loads(raw)
    results = response.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"Neptune openCypher returned an unexpected response: {response}")
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset-revision", required=True)
    parser.add_argument("--opensearch-endpoint", required=True)
    parser.add_argument("--record-index-alias", default="hdfs-records")
    parser.add_argument("--pattern-index-alias", default="hdfs-patterns")
    parser.add_argument("--pattern-shards", type=int, default=1)
    parser.add_argument("--record-shards", type=int, default=24)
    parser.add_argument("--opensearch-replicas", type=int, default=1)
    parser.add_argument("--neptune-endpoint", required=True)
    parser.add_argument("--neptune-port", type=int, default=8182)
    parser.add_argument("--neptune-loader-role-arn", required=True)
    parser.add_argument("--embedding-model-id", required=True)
    parser.add_argument("--embedding-dimension", type=int, default=1024)
    parser.add_argument("--region", required=True)
    parser.add_argument("--partitions", type=int, default=1024)
    parser.add_argument("--slo-seconds", type=int, default=600)
    parser.add_argument(
        "--minimum-gib",
        type=int,
        default=100,
        help="Physical payload bytes the timed job must scan before it may publish.",
    )
    parser.add_argument(
        "--remote-index-build",
        choices=["true", "false"],
        default="true",
        help="Set false on domains without serverless vector acceleration.",
    )
    return parser.parse_args()


def main() -> None:
    from pyspark import StorageLevel
    from pyspark.sql import SparkSession, functions as F, types as T

    args = _parse_args()
    started = time.monotonic()
    if not args.run_id.replace("-", "").isalnum():
        raise ValueError("run-id must contain only letters, numbers, and hyphens")
    if args.minimum_gib <= 0:
        raise ValueError("minimum-gib must be a positive number of GiB")

    spark = SparkSession.builder.appName("hdfs-100g-graphrag-build").getOrCreate()
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
    source = spark.read.option("recursiveFileLookup", "true").parquet(args.input)
    required = {
        "block_id",
        "event_encoded",
        "tokenized_block",
        "label",
        "replica",
        "synthetic_payload",
        "source_uri",
        # Consumed below to derive source_hash. Omitting it here turned a missing
        # column into an opaque Spark AnalysisException instead of this message.
        "source_json",
    }
    missing = required - set(source.columns)
    if missing:
        raise RuntimeError(f"Staged corpus is missing required fields: {sorted(missing)}")

    # Spark would otherwise prune the synthetic payload column and only scan a
    # small logical projection. This aggregate proves the timed job physically
    # reads and validates at least 100 GiB of payload before publishing stores.
    scan_started = time.monotonic()
    input_payload_bytes = int(
        source.select(F.sum(F.length("synthetic_payload")).alias("bytes")).first()["bytes"] or 0
    )
    scan_seconds = time.monotonic() - scan_started
    minimum_payload_bytes = args.minimum_gib * 1024**3
    if input_payload_bytes < minimum_payload_bytes:
        raise RuntimeError(
            f"Timed input payload is below {args.minimum_gib} GiB: "
            f"{input_payload_bytes} < {minimum_payload_bytes}"
        )

    raw_event = F.col("event_encoded").cast("string")
    transform_started = time.monotonic()
    event_codes = F.filter(
        F.transform(
            F.split(raw_event, r"<\|sep\|>"),
            lambda segment: F.regexp_extract(F.trim(segment), r"^([0-9]+)", 1),
        ),
        lambda code: F.length(code) > 0,
    )
    canonical_text = F.concat(
        F.lit("HDFS Drain event sequence: "), F.array_join(event_codes, " -> ")
    )
    raw_event_preview = F.substring(
        F.regexp_replace(
            F.regexp_replace(raw_event, r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::[0-9]+)?", "<ip>"),
            r"(?<![A-Za-z])[0-9]{4,}(?![A-Za-z])",
            "<number>",
        ),
        1,
        2000,
    )
    parameter_tokens = F.split(raw_event, r"(?:<\|sep\|>|\s+)")
    host_addresses = F.array_distinct(
        F.filter(
            F.transform(
                parameter_tokens,
                lambda token: F.regexp_extract(
                    token, r"/?((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?::[0-9]+)?", 1
                ),
            ),
            lambda address: F.length(address) > 0,
        )
    )
    datanode_addresses = F.array_distinct(
        F.filter(
            F.transform(
                parameter_tokens,
                lambda token: F.regexp_extract(
                    token, r"/?((?:[0-9]{1,3}\.){3}[0-9]{1,3}):50010", 1
                ),
            ),
            lambda address: F.length(address) > 0,
        )
    )
    records = (
        source.select(
            F.col("block_id").cast("string").alias("source_block_id"),
            F.col("replica").cast("long").alias("replica"),
            canonical_text.alias("text"),
            raw_event_preview.alias("raw_event_preview"),
            event_codes.alias("event_codes"),
            F.transform(
                host_addresses,
                lambda address: F.sha2(
                    F.concat_ws(":", F.lit(args.dataset_revision), address), 256
                ),
            ).alias("host_ids"),
            F.transform(
                datanode_addresses,
                lambda address: F.sha2(
                    F.concat_ws(":", F.lit(args.dataset_revision), address), 256
                ),
            ).alias("datanode_ids"),
            F.col("source_uri").cast("string").alias("source_uri"),
            F.sha2(F.col("source_json"), 256).alias("source_hash"),
            F.col("label").cast("string").alias("evaluation_label"),
        )
        .withColumn("pattern_id", F.sha2(F.array_join(F.col("event_codes"), ":"), 256))
        .withColumn(
            "record_id",
            F.sha2(
                F.concat_ws(
                    ":",
                    F.lit(args.dataset_revision),
                    F.col("source_block_id"),
                    F.col("replica"),
                    F.col("source_hash"),
                ),
                256,
            ),
        )
        .repartition(args.partitions, "pattern_id")
    ).persist(StorageLevel.DISK_ONLY)
    record_count = records.count()
    if record_count <= 0:
        raise RuntimeError("The staged corpus contained no records")

    # Labels are evaluation-only. They never enter either online serving index.
    records.select("record_id", "evaluation_label").write.mode("errorifexists").parquet(
        f"{args.output.rstrip('/')}/runs/{args.run_id}/evaluation/labels"
    )

    grouped = records.groupBy("pattern_id", "text", "event_codes").agg(
        F.count("*").alias("occurrence_count"),
        F.first("raw_event_preview", ignorenulls=True).alias("representative_event_preview"),
        F.array_distinct(F.flatten(F.collect_list("host_ids"))).alias("host_ids"),
        F.array_distinct(F.flatten(F.collect_list("datanode_ids"))).alias("datanode_ids"),
    ).withColumn(
        "text",
        F.concat_ws(". ", F.col("text"), F.col("representative_event_preview")),
    ).drop("representative_event_preview").persist(StorageLevel.DISK_ONLY)
    stats = grouped.agg(
        F.max("occurrence_count").alias("max_count"),
        F.avg(F.length("text")).alias("mean_length"),
        F.stddev_pop(F.length("text")).alias("std_length"),
    ).first()
    max_count = max(int(stats.max_count or 1), 1)
    mean_length = float(stats.mean_length or 0.0)
    std_length = max(float(stats.std_length or 1.0), 1.0)
    rarity = 1.0 - (F.log1p(F.col("occurrence_count")) / math.log1p(max_count))
    length_z = F.abs((F.length("text") - F.lit(mean_length)) / F.lit(std_length))
    structural = F.least(F.lit(1.0), length_z / F.lit(6.0))
    patterns = (
        grouped.withColumn("rarity_score", rarity.cast("float"))
        .withColumn("structural_score", structural.cast("float"))
        .withColumn(
            "anomaly_score",
            (F.lit(0.65) * F.col("rarity_score") + F.lit(0.35) * F.col("structural_score")).cast(
                "float"
            ),
        )
    ).persist(StorageLevel.DISK_ONLY)
    pattern_count = patterns.count()
    if pattern_count <= 0:
        raise RuntimeError("No semantic patterns were produced")
    transform_seconds = time.monotonic() - transform_started

    embedding_started = time.monotonic()
    embedding_schema = T.StructType(
        [
            T.StructField("pattern_id", T.StringType(), False),
            T.StructField("text", T.StringType(), False),
            T.StructField("event_codes", T.ArrayType(T.StringType()), False),
            T.StructField("occurrence_count", T.LongType(), False),
            T.StructField("rarity_score", T.FloatType(), False),
            T.StructField("structural_score", T.FloatType(), False),
            T.StructField("anomaly_score", T.FloatType(), False),
            T.StructField("host_ids", T.ArrayType(T.StringType()), False),
            T.StructField("datanode_ids", T.ArrayType(T.StringType()), False),
            T.StructField("embedding", T.ArrayType(T.FloatType()), False),
            T.StructField("pre_normalization_norm", T.FloatType(), False),
            T.StructField("post_normalization_norm", T.FloatType(), False),
        ]
    )
    embedding_partitions = min(args.partitions, max(32, patterns.rdd.getNumPartitions()))
    embedded = spark.createDataFrame(
        patterns.repartition(embedding_partitions).rdd.mapPartitions(
            lambda rows: _nova_embeddings(
                rows, args.embedding_model_id, args.embedding_dimension, args.region
            )
        ),
        embedding_schema,
    ).withColumn("embedding_model", F.lit(args.embedding_model_id)).withColumn(
        "embedding_dimension", F.lit(args.embedding_dimension)
    ).withColumn("normalization", F.lit("l2")).withColumn(
        "content_hash", F.col("pattern_id")
    ).withColumn("dataset_revision", F.lit(args.dataset_revision)).withColumn(
        "corpus_run_id", F.lit(args.run_id)
    ).withColumn("pipeline_run_id", F.lit(args.run_id)).withColumn(
        "provenance_uri", F.concat(F.lit(args.input.rstrip("/") + "/#pattern="), F.col("pattern_id"))
    ).withColumn("tenant_id", F.lit("default")).withColumn(
        "classification", F.lit("internal")
    ).withColumn(
        "graph_entity_ids",
        F.array(
            F.concat(F.lit(f"run:{args.run_id}:template:"), F.col("pattern_id")),
            F.concat(F.lit(f"run:{args.run_id}:anomaly:"), F.col("pattern_id")),
            F.concat(F.lit(f"run:{args.run_id}:evidence:"), F.col("pattern_id")),
        ),
    ).persist(StorageLevel.DISK_ONLY)
    embedded_count = embedded.count()
    if embedded_count != pattern_count:
        raise RuntimeError(
            f"Nova embedding count mismatch: embedded={embedded_count}, patterns={pattern_count}"
        )
    embedding_seconds = time.monotonic() - embedding_started

    opensearch_started = time.monotonic()
    pattern_index = f"{args.pattern_index_alias}-{args.run_id}"
    record_index = f"{args.record_index_alias}-{args.run_id}"
    _ensure_new_index(
        args.opensearch_endpoint,
        pattern_index,
        opensearch_mapping(
            args.embedding_dimension,
            args.pattern_shards,
            args.opensearch_replicas,
            args.remote_index_build == "true",
        ),
        args.region,
    )
    _ensure_new_index(
        args.opensearch_endpoint,
        record_index,
        record_mapping(args.record_shards, args.opensearch_replicas),
        args.region,
    )

    pattern_bulk = embedded.rdd.mapPartitions(
        lambda rows: _bulk_partition(
            rows, args.opensearch_endpoint, pattern_index, args.region, "pattern_id"
        )
    ).collect()
    scored_records = (
        records.drop("evaluation_label")
        .join(patterns.select("pattern_id", "anomaly_score"), "pattern_id", "inner")
        .withColumn("dataset_revision", F.lit(args.dataset_revision))
        .withColumn("corpus_run_id", F.lit(args.run_id))
        .withColumn("pipeline_run_id", F.lit(args.run_id))
        .withColumn(
            "provenance_uri",
            F.concat(
                F.col("source_uri"),
                F.lit("#replica="),
                F.col("replica").cast("string"),
                F.lit("&record="),
                F.col("record_id"),
            ),
        )
        .withColumn("tenant_id", F.lit("default"))
        .withColumn("classification", F.lit("internal"))
        .withColumn(
            "graph_entity_ids",
            F.array(
                F.concat(F.lit(f"run:{args.run_id}:block:"), F.col("source_block_id")),
                F.concat(F.lit(f"run:{args.run_id}:template:"), F.col("pattern_id")),
            ),
        )
    )
    record_bulk = scored_records.rdd.mapPartitions(
        lambda rows: _bulk_partition(
            rows, args.opensearch_endpoint, record_index, args.region, "record_id"
        )
    ).collect()
    indexed_patterns = sum(count for count, _ in pattern_bulk)
    indexed_records = sum(count for count, _ in record_bulk)
    if indexed_patterns != pattern_count or indexed_records != record_count:
        raise RuntimeError(
            "OpenSearch client-side count gate failed: "
            f"patterns={indexed_patterns}/{pattern_count}, records={indexed_records}/{record_count}"
        )

    for index in (pattern_index, record_index):
        _json_request(
            "PUT",
            args.opensearch_endpoint,
            f"{index}/_settings",
            "es",
            args.region,
            {"index": {"refresh_interval": "1s"}},
        )
        _json_request("POST", args.opensearch_endpoint, f"{index}/_refresh", "es", args.region)
        health = _json_request(
            "GET",
            args.opensearch_endpoint,
            f"_cluster/health/{index}?wait_for_status=green&wait_for_no_relocating_shards=true&timeout=120s",
            "es",
            args.region,
        )
        if health.get("timed_out") or health.get("status") != "green":
            raise RuntimeError(f"OpenSearch index did not reach serving health: {index} {health}")
    server_pattern_count = int(
        _json_request("GET", args.opensearch_endpoint, f"{pattern_index}/_count", "es", args.region)[
            "count"
        ]
    )
    server_record_count = int(
        _json_request("GET", args.opensearch_endpoint, f"{record_index}/_count", "es", args.region)[
            "count"
        ]
    )
    if server_pattern_count != pattern_count or server_record_count != record_count:
        raise RuntimeError(
            "OpenSearch server-side count gate failed: "
            f"patterns={server_pattern_count}/{pattern_count}, records={server_record_count}/{record_count}"
        )
    opensearch_seconds = time.monotonic() - opensearch_started

    neptune_started = time.monotonic()
    graph_prefix = f"{args.output.rstrip('/')}/runs/{args.run_id}/neptune"
    run_prefix = f"run:{args.run_id}:"
    published_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_provenance = f"{args.output.rstrip('/')}/runs/{args.run_id}/manifest.json"

    def with_graph_metadata(frame: Any) -> Any:
        enriched = frame
        common = {
            "pipeline_run_id:String": args.run_id,
            "dataset_revision:String": args.dataset_revision,
            "classification:String": "internal",
            "tenant_id:String": "default",
            "valid_from:String": published_at,
        }
        for column, value in common.items():
            if column not in enriched.columns:
                enriched = enriched.withColumn(column, F.lit(value))
        if "provenance_uri:String" not in enriched.columns:
            enriched = enriched.withColumn("provenance_uri:String", F.lit(manifest_provenance))
        return enriched

    grouped_pairs = records.groupBy("source_block_id", "pattern_id").agg(
        F.count("*").alias("replica_count"),
        F.min("replica").alias("first_replica"),
        F.max("replica").alias("last_replica"),
        F.first("source_uri", ignorenulls=True).alias("source_uri"),
        F.first("source_hash", ignorenulls=True).alias("source_hash"),
        F.array_distinct(F.flatten(F.collect_list("host_ids"))).alias("host_ids"),
        F.array_distinct(F.flatten(F.collect_list("datanode_ids"))).alias("datanode_ids"),
    ).withColumn(
        "group_hash",
        F.sha2(
            F.concat_ws(
                ":",
                F.lit(args.run_id),
                F.col("source_block_id"),
                F.col("pattern_id"),
            ),
            256,
        ),
    ).persist(StorageLevel.DISK_ONLY)

    pipeline_nodes = with_graph_metadata(
        spark.createDataFrame([(args.run_id,)], ["run_id"]).select(
            F.concat(F.lit(run_prefix), F.lit("pipeline")).alias("~id"),
            F.lit("PipelineRun").alias("~label"),
            F.col("run_id").alias("run_id:String"),
            F.lit("PUBLISHED").alias("status:String"),
            F.lit(args.embedding_model_id).alias("embedding_model:String"),
            F.lit("2.0").alias("graph_schema_version:String"),
        )
    )
    block_nodes = with_graph_metadata(
        records.groupBy("source_block_id").agg(
            F.first("source_uri", ignorenulls=True).alias("source_uri"),
            F.first("source_hash", ignorenulls=True).alias("source_hash"),
        ).select(
            F.concat(F.lit(run_prefix + "block:"), F.col("source_block_id")).alias("~id"),
            F.lit("HDFSBlock").alias("~label"),
            F.col("source_block_id").alias("block_id:String"),
            F.col("source_hash").alias("source_hash:String"),
            F.col("source_uri").alias("provenance_uri:String"),
        )
    )
    template_nodes = with_graph_metadata(
        patterns.select(
            F.concat(F.lit(run_prefix + "template:"), F.col("pattern_id")).alias("~id"),
            F.lit("LogTemplate").alias("~label"),
            F.col("pattern_id").alias("pattern_id:String"),
            F.col("text").alias("text:String"),
            F.col("occurrence_count").alias("occurrence_count:Long"),
            F.col("anomaly_score").alias("anomaly_score:Double"),
            F.col("pattern_id").alias("source_hash:String"),
        )
    )
    event_nodes = with_graph_metadata(
        patterns.select(F.explode("event_codes").alias("event_code"))
        .filter(F.length("event_code") > 0)
        .distinct()
        .select(
            F.concat(F.lit(run_prefix + "event:"), F.col("event_code")).alias("~id"),
            F.lit("LogEvent").alias("~label"),
            F.col("event_code").alias("event_code:String"),
            F.sha2(F.col("event_code"), 256).alias("source_hash:String"),
        )
    )
    host_nodes = with_graph_metadata(
        records.select(F.explode("host_ids").alias("host_id"))
        .filter(F.length("host_id") > 0)
        .distinct()
        .select(
            F.concat(F.lit(run_prefix + "host:"), F.col("host_id")).alias("~id"),
            F.lit("Host").alias("~label"),
            F.col("host_id").alias("host_id:String"),
            F.col("host_id").alias("source_hash:String"),
        )
    )
    datanode_nodes = with_graph_metadata(
        records.select(F.explode("datanode_ids").alias("host_id"))
        .filter(F.length("host_id") > 0)
        .distinct()
        .select(
            F.concat(F.lit(run_prefix + "datanode:"), F.col("host_id")).alias("~id"),
            F.lit("DataNode").alias("~label"),
            F.col("host_id").alias("host_id:String"),
            F.lit("DataNode").alias("hdfs_role:String"),
            F.col("host_id").alias("source_hash:String"),
        )
    )
    process_nodes = with_graph_metadata(
        records.select(F.explode("datanode_ids").alias("host_id"))
        .filter(F.length("host_id") > 0)
        .distinct()
        .select(
            F.concat(F.lit(run_prefix + "process:"), F.col("host_id"), F.lit(":50010")).alias(
                "~id"
            ),
            F.lit("Process").alias("~label"),
            F.concat(F.col("host_id"), F.lit(":50010")).alias("process_id:String"),
            F.lit("HDFS DataNode IPC").alias("process_type:String"),
            F.lit(50010).alias("port:Int"),
            F.col("host_id").alias("source_hash:String"),
        )
    )
    source_nodes = with_graph_metadata(
        grouped_pairs.select(
            F.concat(F.lit(run_prefix + "source:"), F.col("group_hash")).alias("~id"),
            F.lit("SourceRecord").alias("~label"),
            F.col("group_hash").alias("occurrence_group_id:String"),
            F.col("source_block_id").alias("block_id:String"),
            F.col("pattern_id").alias("pattern_id:String"),
            F.col("replica_count").alias("occurrence_count:Long"),
            F.col("first_replica").alias("first_replica:Long"),
            F.col("last_replica").alias("last_replica:Long"),
            F.col("source_hash").alias("source_hash:String"),
            F.col("source_uri").alias("provenance_uri:String"),
        )
    )
    replication_nodes = with_graph_metadata(
        grouped_pairs.select(
            F.concat(F.lit(run_prefix + "replication:"), F.col("group_hash")).alias("~id"),
            F.lit("ReplicationGroup").alias("~label"),
            F.col("group_hash").alias("replication_group_id:String"),
            F.col("replica_count").alias("occurrence_count:Long"),
            F.col("first_replica").alias("first_replica:Long"),
            F.col("last_replica").alias("last_replica:Long"),
            F.col("source_hash").alias("source_hash:String"),
            F.col("source_uri").alias("provenance_uri:String"),
        )
    )
    anomaly_nodes = with_graph_metadata(
        patterns.select(
            F.concat(F.lit(run_prefix + "anomaly:"), F.col("pattern_id")).alias("~id"),
            F.lit("Anomaly").alias("~label"),
            F.concat(F.lit("hdfs-"), F.substring("pattern_id", 1, 12)).alias("finding_id:String"),
            F.col("pattern_id").alias("pattern_id:String"),
            F.col("anomaly_score").alias("score:Double"),
            F.when(F.col("anomaly_score") >= 0.75, F.lit("HIGH"))
            .otherwise(F.lit("MEDIUM"))
            .alias("severity:String"),
            F.col("pattern_id").alias("source_hash:String"),
        )
    )
    evidence_nodes = with_graph_metadata(
        patterns.select(
            F.concat(F.lit(run_prefix + "evidence:"), F.col("pattern_id")).alias("~id"),
            F.lit("Evidence").alias("~label"),
            F.col("pattern_id").alias("evidence_id:String"),
            F.col("rarity_score").alias("rarity_score:Double"),
            F.col("structural_score").alias("structural_score:Double"),
            F.col("anomaly_score").alias("rank_contribution:Double"),
            F.col("pattern_id").alias("source_hash:String"),
        )
    )
    anomaly_type_nodes = with_graph_metadata(
        spark.createDataFrame([("unsupervised-sequence-deviation",)], ["type_id"]).select(
            F.concat(F.lit(run_prefix + "anomaly-type:"), F.col("type_id")).alias("~id"),
            F.lit("AnomalyType").alias("~label"),
            F.col("type_id").alias("anomaly_type_id:String"),
            F.lit("Rare or structurally unusual HDFS event sequence").alias("description:String"),
        )
    )
    action_nodes = with_graph_metadata(
        spark.createDataFrame([("investigate-hdfs-evidence",)], ["action_id"]).select(
            F.concat(F.lit(run_prefix + "action:"), F.col("action_id")).alias("~id"),
            F.lit("RecommendedAction").alias("~label"),
            F.col("action_id").alias("action_id:String"),
            F.lit("Inspect cited HDFS blocks and corroborate with live cluster telemetry.").alias(
                "human_step:String"
            ),
            F.lit("human-approval-required").alias("required_role:String"),
        )
    )

    def edge(frame: Any, label: str) -> Any:
        return with_graph_metadata(
            frame.withColumn("~label", F.lit(label)).withColumn(
                "~id",
                F.sha2(
                    F.concat_ws(
                        ":",
                        F.lit(args.run_id),
                        F.lit(label),
                        F.col("~from"),
                        F.col("~to"),
                    ),
                    256,
                ),
            )
        )

    source_block = edge(
        grouped_pairs.select(
            F.concat(F.lit(run_prefix + "source:"), F.col("group_hash")).alias("~from"),
            F.concat(F.lit(run_prefix + "block:"), F.col("source_block_id")).alias("~to"),
            F.col("replica_count").alias("occurrence_count:Long"),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "REFERENCES_BLOCK",
    )
    source_template = edge(
        grouped_pairs.select(
            F.concat(F.lit(run_prefix + "source:"), F.col("group_hash")).alias("~from"),
            F.concat(F.lit(run_prefix + "template:"), F.col("pattern_id")).alias("~to"),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "MATCHES_TEMPLATE",
    )
    template_event = edge(
        patterns.select("pattern_id", F.explode("event_codes").alias("event_code"))
        .filter(F.length("event_code") > 0)
        .distinct()
        .select(
            F.concat(F.lit(run_prefix + "template:"), F.col("pattern_id")).alias("~from"),
            F.concat(F.lit(run_prefix + "event:"), F.col("event_code")).alias("~to"),
        ),
        "CONTAINS_EVENT",
    )
    anomaly_evidence = edge(
        patterns.select(
            F.concat(F.lit(run_prefix + "anomaly:"), F.col("pattern_id")).alias("~from"),
            F.concat(F.lit(run_prefix + "evidence:"), F.col("pattern_id")).alias("~to"),
        ),
        "HAS_EVIDENCE",
    )
    evidence_supports = edge(
        patterns.select(
            F.concat(F.lit(run_prefix + "evidence:"), F.col("pattern_id")).alias("~from"),
            F.concat(F.lit(run_prefix + "anomaly:"), F.col("pattern_id")).alias("~to"),
        ),
        "SUPPORTS",
    )
    evidence_indicates = edge(
        patterns.select(
            F.concat(F.lit(run_prefix + "evidence:"), F.col("pattern_id")).alias("~from"),
            F.lit(run_prefix + "anomaly-type:unsupervised-sequence-deviation").alias("~to"),
        ),
        "INDICATES",
    )
    anomaly_affects = edge(
        grouped_pairs.select(
            F.concat(F.lit(run_prefix + "anomaly:"), F.col("pattern_id")).alias("~from"),
            F.concat(F.lit(run_prefix + "block:"), F.col("source_block_id")).alias("~to"),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "AFFECTS",
    )
    evidence_source = edge(
        grouped_pairs.select(
            F.concat(F.lit(run_prefix + "evidence:"), F.col("pattern_id")).alias("~from"),
            F.concat(F.lit(run_prefix + "source:"), F.col("group_hash")).alias("~to"),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "DERIVED_FROM",
    )
    anomaly_run = edge(
        patterns.select(
            F.concat(F.lit(run_prefix + "anomaly:"), F.col("pattern_id")).alias("~from"),
            F.lit(run_prefix + "pipeline").alias("~to"),
        ),
        "INGESTED_BY",
    )
    replication_source = edge(
        grouped_pairs.select(
            F.concat(F.lit(run_prefix + "replication:"), F.col("group_hash")).alias("~from"),
            F.concat(F.lit(run_prefix + "source:"), F.col("group_hash")).alias("~to"),
            F.col("replica_count").alias("occurrence_count:Long"),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "EXPANDS_TO",
    )
    block_replication = edge(
        grouped_pairs.select(
            F.concat(F.lit(run_prefix + "block:"), F.col("source_block_id")).alias("~from"),
            F.concat(F.lit(run_prefix + "replication:"), F.col("group_hash")).alias("~to"),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "HAS_REPLICATION_GROUP",
    )
    type_action = edge(
        spark.createDataFrame([(1,)], ["one"]).select(
            F.lit(run_prefix + "anomaly-type:unsupervised-sequence-deviation").alias("~from"),
            F.lit(run_prefix + "action:investigate-hdfs-evidence").alias("~to"),
        ),
        "RECOMMENDS",
    )
    source_host = edge(
        grouped_pairs.select(
            "group_hash",
            "source_uri",
            F.explode("host_ids").alias("host_id"),
        ).select(
            F.concat(F.lit(run_prefix + "source:"), F.col("group_hash")).alias("~from"),
            F.concat(F.lit(run_prefix + "host:"), F.col("host_id")).alias("~to"),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "OCCURRED_ON",
    )
    anomaly_host = edge(
        grouped_pairs.select(
            "pattern_id",
            "source_uri",
            F.explode("host_ids").alias("host_id"),
        ).groupBy("pattern_id", "host_id").agg(
            F.first("source_uri", ignorenulls=True).alias("source_uri")
        ).select(
            F.concat(F.lit(run_prefix + "anomaly:"), F.col("pattern_id")).alias("~from"),
            F.concat(F.lit(run_prefix + "host:"), F.col("host_id")).alias("~to"),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "AFFECTS_HOST",
    )
    source_process = edge(
        grouped_pairs.select(
            "group_hash",
            "source_uri",
            F.explode("datanode_ids").alias("host_id"),
        ).select(
            F.concat(F.lit(run_prefix + "source:"), F.col("group_hash")).alias("~from"),
            F.concat(F.lit(run_prefix + "process:"), F.col("host_id"), F.lit(":50010")).alias(
                "~to"
            ),
            F.col("source_uri").alias("provenance_uri:String"),
        ),
        "EMITTED_BY",
    )
    process_host = edge(
        records.select(F.explode("datanode_ids").alias("host_id"))
        .filter(F.length("host_id") > 0)
        .distinct()
        .select(
            F.concat(F.lit(run_prefix + "process:"), F.col("host_id"), F.lit(":50010")).alias(
                "~from"
            ),
            F.concat(F.lit(run_prefix + "host:"), F.col("host_id")).alias("~to"),
        ),
        "RUNS_ON",
    )
    datanode_host = edge(
        records.select(F.explode("datanode_ids").alias("host_id"))
        .filter(F.length("host_id") > 0)
        .distinct()
        .select(
            F.concat(F.lit(run_prefix + "datanode:"), F.col("host_id")).alias("~from"),
            F.concat(F.lit(run_prefix + "host:"), F.col("host_id")).alias("~to"),
        ),
        "RUNS_ON",
    )

    graph_frames = (
        (pipeline_nodes, "nodes/pipeline-run"),
        (block_nodes, "nodes/hdfs-block"),
        (template_nodes, "nodes/log-template"),
        (event_nodes, "nodes/log-event"),
        (host_nodes, "nodes/host"),
        (datanode_nodes, "nodes/datanode"),
        (process_nodes, "nodes/process"),
        (source_nodes, "nodes/source-record"),
        (replication_nodes, "nodes/replication-group"),
        (anomaly_nodes, "nodes/anomaly"),
        (evidence_nodes, "nodes/evidence"),
        (anomaly_type_nodes, "nodes/anomaly-type"),
        (action_nodes, "nodes/recommended-action"),
        (source_block, "edges/references-block"),
        (source_template, "edges/matches-template"),
        (template_event, "edges/contains-event"),
        (anomaly_evidence, "edges/has-evidence"),
        (evidence_supports, "edges/supports"),
        (evidence_indicates, "edges/indicates"),
        (anomaly_affects, "edges/affects"),
        (evidence_source, "edges/derived-from"),
        (anomaly_run, "edges/ingested-by"),
        (replication_source, "edges/expands-to"),
        (block_replication, "edges/has-replication-group"),
        (type_action, "edges/recommends"),
        (source_host, "edges/occurred-on"),
        (anomaly_host, "edges/affects-host"),
        (source_process, "edges/emitted-by"),
        (process_host, "edges/process-runs-on"),
        (datanode_host, "edges/datanode-runs-on"),
    )
    expected_graph_records = 0
    graph_artifact_counts: dict[str, int] = {}
    for frame, suffix in graph_frames:
        cached_frame = frame.persist(StorageLevel.DISK_ONLY)
        frame_count = cached_frame.count()
        if frame_count <= 0:
            raise RuntimeError(f"Graph artifact {suffix} is empty")
        expected_graph_records += frame_count
        graph_artifact_counts[suffix] = frame_count
        cached_frame.coalesce(max(1, min(64, math.ceil(frame_count / 1_000_000)))).write.mode(
            "errorifexists"
        ).option("header", True).csv(f"{graph_prefix}/{suffix}")
        cached_frame.unpersist()
    grouped_pairs.unpersist()

    load_id, neptune_records = _neptune_load(
        args.neptune_endpoint,
        args.neptune_port,
        graph_prefix,
        args.neptune_loader_role_arn,
        args.region,
        max(60, args.slo_seconds),
    )
    # The loader's totalRecords counts individual property statements, not CSV
    # rows: a node row with 13 property columns is 13 records. Comparing it
    # against the frame row counts can therefore never match on any successful
    # load. Reconcile against the graph itself, scoped to this run through
    # pipeline_run_id, which compares like with like and stays correct when the
    # database already holds rows from earlier runs.
    expected_node_records = sum(
        count for suffix, count in graph_artifact_counts.items() if suffix.startswith("nodes/")
    )
    expected_edge_records = sum(
        count for suffix, count in graph_artifact_counts.items() if suffix.startswith("edges/")
    )
    count_timeout = max(300, min(900, args.slo_seconds))
    loaded_nodes = int(
        _neptune_cypher(
            args.neptune_endpoint,
            args.neptune_port,
            args.region,
            "MATCH (n {pipeline_run_id: $run_id}) RETURN count(n) AS total",
            {"run_id": args.run_id},
            timeout=count_timeout,
        )[0]["total"]
    )
    loaded_edges = int(
        _neptune_cypher(
            args.neptune_endpoint,
            args.neptune_port,
            args.region,
            "MATCH ()-[r {pipeline_run_id: $run_id}]->() RETURN count(r) AS total",
            {"run_id": args.run_id},
            timeout=count_timeout,
        )[0]["total"]
    )
    if loaded_nodes != expected_node_records or loaded_edges != expected_edge_records:
        raise RuntimeError(
            "Neptune count reconciliation failed: "
            f"nodes loaded={loaded_nodes} expected={expected_node_records}, "
            f"edges loaded={loaded_edges} expected={expected_edge_records} "
            f"(loader reported {neptune_records} property statements across "
            f"{expected_graph_records} rows)"
        )
    complete_anomalies = _neptune_cypher(
        args.neptune_endpoint,
        args.neptune_port,
        args.region,
        (
            "MATCH (a:Anomaly)-[:HAS_EVIDENCE]->(e:Evidence), "
            "(a)-[:AFFECTS]->(b:HDFSBlock), (a)-[:INGESTED_BY]->(r:PipelineRun) "
            "WHERE a.pipeline_run_id = $run_id AND e.pipeline_run_id = $run_id "
            "AND b.pipeline_run_id = $run_id AND r.run_id = $run_id "
            "RETURN count(DISTINCT a) AS count"
        ),
        {"run_id": args.run_id},
    )
    provenance_anomalies = _neptune_cypher(
        args.neptune_endpoint,
        args.neptune_port,
        args.region,
        (
            "MATCH (a:Anomaly)-[:HAS_EVIDENCE]->(e:Evidence)-[:DERIVED_FROM]->(s:SourceRecord) "
            "WHERE a.pipeline_run_id = $run_id AND s.pipeline_run_id = $run_id "
            "RETURN count(DISTINCT a) AS count"
        ),
        {"run_id": args.run_id},
    )
    if (
        len(complete_anomalies) != 1
        or len(provenance_anomalies) != 1
        or int(complete_anomalies[0].get("count") or 0) != pattern_count
        or int(provenance_anomalies[0].get("count") or 0) != pattern_count
    ):
        raise RuntimeError(
            "Neptune relationship validation failed: "
            f"complete={complete_anomalies}, provenance={provenance_anomalies}, "
            f"expected_patterns={pattern_count}"
        )
    neptune_seconds = time.monotonic() - neptune_started

    prepared_elapsed = time.monotonic() - started
    manifest = {
        "schema_version": "2.0",
        "status": "PREPARED",
        "run_id": args.run_id,
        "input": args.input,
        "records": record_count,
        "input_payload_bytes": input_payload_bytes,
        "patterns": pattern_count,
        "record_index": record_index,
        "pattern_index": pattern_index,
        "neptune_load_id": load_id,
        # neptune_records is the loader's property-statement count and is kept for
        # provenance only. It is deliberately NOT comparable to the row counts
        # below; the reconciliation pairs are the loaded_/expected_ node and edge
        # counts, which are measured in the same units.
        "neptune_records": neptune_records,
        "expected_graph_records": expected_graph_records,
        "expected_node_records": expected_node_records,
        "expected_edge_records": expected_edge_records,
        "neptune_loaded_nodes": loaded_nodes,
        "neptune_loaded_edges": loaded_edges,
        "graph_artifact_counts": graph_artifact_counts,
        "embedding_model": args.embedding_model_id,
        "embedding_dimension": args.embedding_dimension,
        "dataset_revision": args.dataset_revision,
        "l2_normalized": True,
        "labels_isolated": True,
        "run_isolated": True,
        "neptune_relationships_validated": True,
        "tenant_id": "default",
        "classification": "internal",
        "elapsed_seconds_inside_spark": prepared_elapsed,
        "slo_seconds": args.slo_seconds,
        "slo_passed_inside_spark": prepared_elapsed <= args.slo_seconds,
        "scan_seconds": scan_seconds,
        "transform_seconds": transform_seconds,
        "embedding_seconds": embedding_seconds,
        "opensearch_seconds": opensearch_seconds,
        "neptune_seconds": neptune_seconds,
    }
    spark.createDataFrame([manifest]).coalesce(1).write.mode("errorifexists").json(
        f"{args.output.rstrip('/')}/runs/{args.run_id}/manifests/prepared"
    )

    previous_pattern_indexes = _alias_indices(
        args.opensearch_endpoint, args.pattern_index_alias, args.region
    )
    previous_record_indexes = _alias_indices(
        args.opensearch_endpoint, args.record_index_alias, args.region
    )
    alias_actions = {
        "actions": [
            {"remove": {"index": f"{args.pattern_index_alias}-*", "alias": args.pattern_index_alias, "must_exist": False}},
            {"add": {"index": pattern_index, "alias": args.pattern_index_alias}},
            {"remove": {"index": f"{args.record_index_alias}-*", "alias": args.record_index_alias, "must_exist": False}},
            {"add": {"index": record_index, "alias": args.record_index_alias}},
        ]
    }
    _json_request("POST", args.opensearch_endpoint, "_aliases", "es", args.region, alias_actions)

    elapsed = time.monotonic() - started
    manifest.update(
        {
            "status": "PUBLISHED",
            "elapsed_seconds_inside_spark": elapsed,
            "slo_passed_inside_spark": elapsed <= args.slo_seconds,
        }
    )
    spark.createDataFrame([manifest]).coalesce(1).write.mode("errorifexists").json(
        f"{args.output.rstrip('/')}/runs/{args.run_id}/manifests/published"
    )
    acceptance_elapsed = time.monotonic() - started
    if acceptance_elapsed > args.slo_seconds:
        rollback_actions: list[dict[str, Any]] = [
            {
                "remove": {
                    "index": pattern_index,
                    "alias": args.pattern_index_alias,
                    "must_exist": False,
                }
            },
            {
                "remove": {
                    "index": record_index,
                    "alias": args.record_index_alias,
                    "must_exist": False,
                }
            },
        ]
        rollback_actions.extend(
            {"add": {"index": index, "alias": args.pattern_index_alias}}
            for index in previous_pattern_indexes
        )
        rollback_actions.extend(
            {"add": {"index": index, "alias": args.record_index_alias}}
            for index in previous_record_indexes
        )
        cleanup_errors: list[str] = []
        try:
            _json_request(
                "POST",
                args.opensearch_endpoint,
                "_aliases",
                "es",
                args.region,
                {"actions": rollback_actions},
            )
        except Exception as exc:  # Surface any failure to restore the prior active run.
            cleanup_errors.append(f"alias rollback failed: {type(exc).__name__}: {exc}")
        try:
            _delete_s3_prefix(
                f"{args.output.rstrip('/')}/runs/{args.run_id}/manifests/published"
            )
        except Exception as exc:
            cleanup_errors.append(f"publication cleanup failed: {type(exc).__name__}: {exc}")
        detail = "; ".join(cleanup_errors) if cleanup_errors else "prior aliases restored"
        raise RuntimeError(
            "GraphRAG build exceeded SLO after the publication write: "
            f"{acceptance_elapsed:.2f}s > {args.slo_seconds}s; {detail}"
        )
    print(json.dumps({**manifest, "acceptance_elapsed_seconds": acceptance_elapsed}, sort_keys=True))


if __name__ == "__main__":
    main()
