"""Real AWS GraphRAG retrieval for HDFS anomaly investigation.

The module has no fixture or in-memory fallback. Missing configuration, invalid
Nova vectors, OpenSearch errors, and Neptune errors stop the request.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


MAX_QUERY_CHARS = 4_000
MAX_RESULTS = 20


def l2_normalize(values: list[float], expected_dimension: int) -> list[float]:
    vector = [float(value) for value in values]
    if len(vector) != expected_dimension:
        raise ValueError(
            f"Nova vector dimension mismatch: expected {expected_dimension}, received {len(vector)}"
        )
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Nova vector contains a non-finite value")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        raise ValueError("Nova vector has zero L2 norm")
    normalized = [value / norm for value in vector]
    if abs(math.sqrt(sum(value * value for value in normalized)) - 1.0) > 1e-5:
        raise ValueError("Nova vector failed the L2 normalization gate")
    return normalized


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required GraphRAG configuration is missing: {name}")
    return value


def _query(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("query must be a non-empty string")
    normalized = " ".join(value.split())
    if len(normalized) > MAX_QUERY_CHARS:
        raise ValueError(f"query exceeds {MAX_QUERY_CHARS} characters")
    return normalized


def _limit(value: Any, default: int = 8) -> int:
    limit = default if value is None else int(value)
    if limit < 1 or limit > MAX_RESULTS:
        raise ValueError(f"top_k must be between 1 and {MAX_RESULTS}")
    return limit


def _signed_http(
    method: str,
    url: str,
    service: str,
    body: bytes = b"",
    content_type: str = "application/json",
    timeout: int = 25,
) -> str:
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials available for GraphRAG SigV4 request")
    headers = {"content-type": content_type, "host": urllib.parse.urlparse(url).netloc}
    aws_request = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(credentials.get_frozen_credentials(), service, region).add_auth(aws_request)
    prepared = aws_request.prepare()
    request = urllib.request.Request(
        prepared.url,
        data=body if method not in {"GET", "HEAD"} else None,
        method=method,
        headers=dict(prepared.headers.items()),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{service} request failed with HTTP {exc.code}: {detail}") from exc


def _opensearch(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    endpoint = _required("OPENSEARCH_ENDPOINT").removeprefix("https://").rstrip("/")
    raw = _signed_http(
        "POST",
        f"https://{endpoint}/{path.lstrip('/')}",
        "es",
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    return json.loads(raw)


def nova_embedding(text: str) -> list[float]:
    import boto3
    from botocore.config import Config

    query = _query(text)
    dimension = int(_required("NOVA_EMBEDDING_DIMENSION"))
    model_id = _required("NOVA_EMBEDDING_MODEL_ID")
    runtime = boto3.client(
        "bedrock-runtime",
        config=Config(retries={"mode": "adaptive", "max_attempts": 8}),
    )
    request = {
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": "GENERIC_RETRIEVAL",
            "embeddingDimension": dimension,
            "text": {"truncationMode": "END", "value": query},
        },
    }
    response = runtime.invoke_model(
        modelId=model_id,
        body=json.dumps(request).encode("utf-8"),
        accept="application/json",
        contentType="application/json",
    )
    decoded = json.loads(response["body"].read())
    vector: Any = decoded.get("embeddings") or decoded.get("embedding")
    if isinstance(vector, list) and vector and isinstance(vector[0], dict):
        vector = vector[0].get("embedding")
    if not isinstance(vector, list):
        raise RuntimeError("Nova response did not contain an embedding vector")
    return l2_normalize(vector, dimension)


def _hits(response: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    results = []
    for hit in response.get("hits", {}).get("hits", []):
        source = dict(hit.get("_source") or {})
        source.update(
            {
                "id": hit.get("_id"),
                "retrieval_score": float(hit.get("_score") or 0.0),
                "retrieval_mode": mode,
            }
        )
        source.pop("embedding", None)
        results.append(source)
    return results


def search_hdfs_logs(query: str, top_k: int = 8) -> dict[str, Any]:
    text = _query(query)
    limit = _limit(top_k)
    index = _required("OPENSEARCH_PATTERN_INDEX")
    response = _opensearch(
        f"{index}/_search",
        {
            "size": limit,
            "_source": {"excludes": ["embedding"]},
            "query": {
                "bool": {
                    "should": [
                        {"match": {"text": {"query": text, "operator": "or"}}},
                        {"term": {"event_codes": text}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        },
    )
    return {
        "retrieval_mode": "lexical",
        "reason": "Exact and lexical evidence is useful for HDFS event codes and operator phrases.",
        "results": _hits(response, "lexical"),
    }


def find_similar_hdfs_patterns(query: str, top_k: int = 8) -> dict[str, Any]:
    text = _query(query)
    limit = _limit(top_k)
    index = _required("OPENSEARCH_PATTERN_INDEX")
    response = _opensearch(
        f"{index}/_search",
        {
            "size": limit,
            "_source": {"excludes": ["embedding"]},
            "query": {"knn": {"embedding": {"vector": nova_embedding(text), "k": limit}}},
        },
    )
    return {
        "retrieval_mode": "vector",
        "reason": "Nova semantic retrieval finds anomalous sequences whose wording differs from the question.",
        "results": _hits(response, "vector"),
    }


def search_log_events(query: str, mode: str = "hybrid", top_k: int = 8) -> dict[str, Any]:
    text = _query(query)
    limit = _limit(top_k)
    if mode == "lexical":
        return search_hdfs_logs(text, limit)
    if mode == "vector":
        return find_similar_hdfs_patterns(text, limit)
    if mode != "hybrid":
        raise ValueError("mode must be lexical, vector, or hybrid")
    lexical = search_hdfs_logs(text, limit)
    vector = find_similar_hdfs_patterns(text, limit)
    scores = _rrf([lexical["results"], vector["results"]])
    by_id: dict[str, dict[str, Any]] = {}
    for item in lexical["results"] + vector["results"]:
        pattern_id = item.get("pattern_id") or item.get("id")
        if pattern_id:
            by_id.setdefault(pattern_id, item)
    results = [
        {**by_id[pattern_id], "retrieval_mode": "hybrid", "rrf_score": score}
        for pattern_id, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]
    ]
    return {
        "retrieval_mode": "hybrid",
        "reason": "Hybrid retrieval fuses exact HDFS terms with Nova semantic similarity.",
        "results": results,
    }


def expand_hdfs_graph(pattern_ids: list[str], max_hops: int = 2) -> dict[str, Any]:
    if not pattern_ids or len(pattern_ids) > MAX_RESULTS:
        raise ValueError(f"pattern_ids must contain between 1 and {MAX_RESULTS} values")
    if max_hops not in {1, 2}:
        raise ValueError("max_hops must be 1 or 2")
    cleaned = []
    for pattern_id in pattern_ids:
        if not isinstance(pattern_id, str) or len(pattern_id) != 64:
            raise ValueError("Every pattern_id must be a 64-character SHA-256 identifier")
        int(pattern_id, 16)
        cleaned.append(pattern_id)

    active = _opensearch(
        f"{_required('OPENSEARCH_PATTERN_INDEX')}/_search",
        {
            "size": len(cleaned),
            "_source": ["pattern_id", "pipeline_run_id", "dataset_revision"],
            "query": {"terms": {"pattern_id": cleaned}},
        },
    )
    active_hits = _hits(active, "run-resolution")
    resolved_ids = {str(item.get("pattern_id")) for item in active_hits}
    if resolved_ids != set(cleaned):
        raise RuntimeError("Graph traversal IDs are not all present in the active OpenSearch run")
    run_ids = {str(item.get("pipeline_run_id") or item.get("corpus_run_id")) for item in active_hits}
    if len(run_ids) != 1 or "" in run_ids or "None" in run_ids:
        raise RuntimeError("Active OpenSearch aliases do not resolve to exactly one GraphRAG run")
    run_id = next(iter(run_ids))

    if max_hops == 1:
        cypher = (
            "MATCH (a:Anomaly)-[:HAS_EVIDENCE]->(v:Evidence)-[:INDICATES]->(t:AnomalyType) "
            "WHERE a.pattern_id IN $pattern_ids AND a.pipeline_run_id = $run_id "
            "AND v.pipeline_run_id = $run_id "
            "OPTIONAL MATCH (t)-[:RECOMMENDS]->(r:RecommendedAction) "
            "RETURN a.pattern_id AS pattern_id, a.score AS anomaly_score, "
            "v.rarity_score AS rarity_score, v.structural_score AS structural_score, "
            "t.anomaly_type_id AS anomaly_type, r.human_step AS recommended_action LIMIT 200"
        )
    else:
        cypher = (
            "MATCH (a:Anomaly)-[:AFFECTS]->(b:HDFSBlock), "
            "(a)-[:HAS_EVIDENCE]->(v:Evidence)-[:DERIVED_FROM]->(s:SourceRecord), "
            "(s)-[:MATCHES_TEMPLATE]->(t:LogTemplate)-[:CONTAINS_EVENT]->(e:LogEvent) "
            "WHERE a.pattern_id IN $pattern_ids AND a.pipeline_run_id = $run_id "
            "AND b.pipeline_run_id = $run_id AND s.pipeline_run_id = $run_id "
            "OPTIONAL MATCH (a)-[:AFFECTS_HOST]->(h:Host) "
            "OPTIONAL MATCH (s)-[:EMITTED_BY]->(p:Process) "
            "RETURN a.pattern_id AS pattern_id, b.block_id AS block_id, "
            "s.occurrence_count AS replica_count, s.occurrence_group_id AS source_group_id, "
            "s.provenance_uri AS provenance_uri, e.event_code AS event_code, "
            "h.host_id AS host_id, p.process_id AS process_id LIMIT 200"
        )
    body = urllib.parse.urlencode(
        {
            "query": cypher,
            "parameters": json.dumps({"pattern_ids": cleaned, "run_id": run_id}),
        }
    ).encode("utf-8")
    endpoint = _required("NEPTUNE_ENDPOINT")
    port = int(os.getenv("NEPTUNE_PORT", "8182"))
    raw = _signed_http(
        "POST",
        f"https://{endpoint}:{port}/openCypher",
        "neptune-db",
        body,
        content_type="application/x-www-form-urlencoded",
    )
    decoded = json.loads(raw)
    return {
        "retrieval_mode": "graph",
        "reason": "The graph connects candidate patterns to affected HDFS blocks and constituent events.",
        "max_hops": max_hops,
        "pipeline_run_id": run_id,
        "results": decoded.get("results", []),
    }


def _pattern_ids(values: list[str]) -> list[str]:
    if not values or len(values) > MAX_RESULTS:
        raise ValueError(f"pattern IDs must contain between 1 and {MAX_RESULTS} values")
    cleaned = []
    for value in values:
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("Every pattern ID must be a 64-character SHA-256 identifier")
        int(value, 16)
        cleaned.append(value)
    return cleaned


def _record_evidence(pattern_ids: list[str], per_pattern: int = 3) -> dict[str, list[dict[str, Any]]]:
    ids = _pattern_ids(pattern_ids)
    if per_pattern < 1 or per_pattern > 5:
        raise ValueError("per_pattern must be between 1 and 5")
    index = _required("OPENSEARCH_RECORD_INDEX")
    response = _opensearch(
        f"{index}/_search",
        {
            "size": 0,
            "query": {"terms": {"pattern_id": ids}},
            "aggs": {
                "by_pattern": {
                    "terms": {"field": "pattern_id", "size": len(ids)},
                    "aggs": {
                        "records": {
                            "top_hits": {
                                "size": per_pattern,
                                "sort": [{"record_id": "asc"}],
                                "_source": [
                                    "record_id",
                                    "source_block_id",
                                    "pattern_id",
                                    "replica",
                                    "text",
                                    "raw_event_preview",
                                    "event_codes",
                                    "anomaly_score",
                                    "source_uri",
                                    "source_hash",
                                    "provenance_uri",
                                    "corpus_run_id",
                                    "pipeline_run_id",
                                    "host_ids",
                                    "datanode_ids",
                                ],
                            }
                        }
                    },
                }
            },
        },
    )
    grouped: dict[str, list[dict[str, Any]]] = {pattern_id: [] for pattern_id in ids}
    for bucket in response.get("aggregations", {}).get("by_pattern", {}).get("buckets", []):
        grouped[str(bucket.get("key"))] = _hits(
            bucket.get("records", {}), "source-record"
        )
    return grouped


def get_anomaly_evidence(pattern_ids: list[str]) -> dict[str, Any]:
    ids = _pattern_ids(pattern_ids)
    index = _required("OPENSEARCH_PATTERN_INDEX")
    patterns = _hits(
        _opensearch(
            f"{index}/_search",
            {
                "size": len(ids),
                "_source": {"excludes": ["embedding"]},
                "query": {"terms": {"pattern_id": ids}},
            },
        ),
        "evidence",
    )
    graph = expand_hdfs_graph(ids, max_hops=2)
    source_records = _record_evidence(ids)
    content = {"patterns": patterns, "graph": graph["results"], "source_records": source_records}
    evidence_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **content,
        "evidence_hash": evidence_hash,
        "reason": "Resolve the ranked patterns to their stored features, blocks, and events before recommending action.",
    }


def correlate_block_failures(block_ids: list[str], top_k: int = 20) -> dict[str, Any]:
    limit = _limit(top_k)
    if not block_ids or len(block_ids) > 20:
        raise ValueError("block_ids must contain between 1 and 20 values")
    cleaned = []
    for value in block_ids:
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError("Every block ID must be a non-empty string of at most 256 characters")
        cleaned.append(value.strip())
    index = _required("OPENSEARCH_RECORD_INDEX")
    response = _opensearch(
        f"{index}/_search",
        {
            "size": limit,
            "_source": ["record_id", "source_block_id", "pattern_id", "text", "event_codes", "anomaly_score"],
            "query": {"terms": {"source_block_id": cleaned}},
            "sort": [{"anomaly_score": "desc"}, {"record_id": "asc"}],
        },
    )
    return {
        "retrieval_mode": "block-correlation",
        "reason": "Correlate repeated high-scoring patterns attached to the same HDFS blocks.",
        "results": _hits(response, "block-correlation"),
    }


def analyze_node_behavior(block_ids: list[str]) -> dict[str, Any]:
    correlated = correlate_block_failures(block_ids, top_k=20)
    by_block: dict[str, list[float]] = {}
    for row in correlated["results"]:
        by_block.setdefault(str(row.get("source_block_id")), []).append(
            float(row.get("anomaly_score") or 0.0)
        )
    analysis = []
    for block_id in block_ids:
        values = by_block.get(block_id, [])
        analysis.append(
            {
                "block_id": block_id,
                "sample_size": len(values),
                "mean_anomaly_score": round(sum(values) / len(values), 6) if values else None,
                "status": "ANALYZED" if len(values) >= 3 else "INSUFFICIENT_EVIDENCE",
            }
        )
    return {
        "reason": "Compare each requested block against the corpus-derived anomaly score while enforcing a minimum sample size.",
        "analysis": analysis,
    }


def generate_remediation_plan(pattern_ids: list[str]) -> dict[str, Any]:
    evidence = get_anomaly_evidence(pattern_ids)
    plans = []
    for item in evidence["patterns"]:
        plans.append(
            {
                "pattern_id": item.get("pattern_id") or item.get("id"),
                "human_action_plan": _action_plan(item),
                "execution_authorized": False,
                "approval_required_for_write": True,
            }
        )
    return {
        "reason": "Translate stored evidence into bounded human actions without executing a write operation.",
        "evidence_hash": evidence["evidence_hash"],
        "plans": plans,
    }


def validate_remediation(pattern_ids: list[str], original_evidence_hash: str) -> dict[str, Any]:
    if len(original_evidence_hash) != 64:
        raise ValueError("original_evidence_hash must be a SHA-256 value")
    int(original_evidence_hash, 16)
    current = get_anomaly_evidence(pattern_ids)
    unchanged = current["evidence_hash"] == original_evidence_hash
    return {
        "status": "INSUFFICIENT_EVIDENCE",
        "reason": (
            "The demo corpus is a static HDFS snapshot and cannot prove a live remediation outcome; "
            "connect an authoritative post-change telemetry source before enabling verification."
        ),
        "evidence_unchanged": unchanged,
        "current_evidence_hash": current["evidence_hash"],
        "write_performed": False,
    }


def _rrf(lists: list[list[dict[str, Any]]], constant: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for values in lists:
        for rank, item in enumerate(values, start=1):
            pattern_id = item.get("pattern_id") or item.get("id")
            if pattern_id:
                scores[pattern_id] = scores.get(pattern_id, 0.0) + 1.0 / (constant + rank)
    return scores


def _action_plan(item: dict[str, Any]) -> list[str]:
    """Build steps that name the templates and blocks this finding actually cites.

    The previous implementation matched English keywords against a pattern text
    made of numeric Drain ids, so no branch could ever be selected and every
    finding received the same fallback plan.
    """
    profile = sequence_profile(item.get("event_codes") or [])
    dominant = profile["dominant_code"]
    blocks = [str(block) for block in (item.get("affected_blocks") or []) if block]
    cited = ", ".join(blocks[:3]) if blocks else "the cited blocks"

    if not profile["total_events"]:
        return [
            f"Retrieve the raw log lines for {cited} directly; no event sequence was indexed for this pattern.",
            "Compare that window against NameNode, DataNode, JVM, and storage telemetry.",
            "Escalate any remediation through deterministic policy and exact-plan approval, then verify the postcondition.",
        ]

    first = (
        f"Resolve Drain template {dominant} in your parser configuration, then pull the raw log "
        f"lines for {cited} where it occurs; it is {profile['dominant_share']:.0%} of this block's "
        f"{profile['total_events']} events."
    )
    if profile["longest_run"] >= 10:
        second = (
            f"Template {profile['longest_run_code']} repeats {profile['longest_run']} times "
            "unbroken, so check whether the responsible handler is retrying or stalled rather "
            "than treating each occurrence as an independent event."
        )
    elif profile["repeated_cycle_count"] >= 2:
        second = (
            f"The cycle {profile['repeated_cycle']} repeats {profile['repeated_cycle_count']} "
            "times at the head of the block, so check the component that drives that loop for "
            "repeated retries before looking at later events."
        )
    else:
        second = (
            f"The block spans {profile['distinct_events']} distinct templates with no dominant "
            "repetition, so correlate the sequence against deployments and configuration drift "
            "in the same window rather than a single failing component."
        )
    third = (
        "Escalate any remediation through deterministic policy and exact-plan approval, then "
        "verify the postcondition independently. Do not use the dataset label as operational evidence."
    )
    return [first, second, third]


def sequence_profile(event_codes: list[str]) -> dict[str, Any]:
    """Describe the shape of a Drain event sequence using only the sequence itself.

    The corpus encodes each log line as a Drain template id, so the pattern text
    contains no English to match on. These are measured properties of the actual
    sequence: which template dominates, how long it repeats unbroken, and whether
    a short cycle recurs. Nothing here asserts what a template *means*, because
    the dataset ships no template legend and inventing one would produce a
    confident and unverifiable cause.
    """
    codes = [str(code) for code in event_codes or []]
    profile: dict[str, Any] = {
        "total_events": len(codes),
        "distinct_events": len(set(codes)),
        "dominant_code": None,
        "dominant_count": 0,
        "dominant_share": 0.0,
        "longest_run_code": None,
        "longest_run": 0,
        "repeated_cycle": None,
        "repeated_cycle_count": 0,
    }
    if not codes:
        return profile

    counts: dict[str, int] = {}
    for code in codes:
        counts[code] = counts.get(code, 0) + 1
    dominant_code, dominant_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    profile["dominant_code"] = dominant_code
    profile["dominant_count"] = dominant_count
    profile["dominant_share"] = round(dominant_count / len(codes), 4)

    run_code, run_length, best_code, best_length = codes[0], 1, codes[0], 1
    for code in codes[1:]:
        if code == run_code:
            run_length += 1
        else:
            run_code, run_length = code, 1
        if run_length > best_length:
            best_code, best_length = run_code, run_length
    profile["longest_run_code"] = best_code
    profile["longest_run"] = best_length

    # Shortest cycle that repeats back to back at the head of the sequence, which
    # is what distinguishes "opens with a handshake loop" from "one long run".
    for size in range(2, 7):
        if len(codes) < size * 2:
            break
        window = codes[:size]
        repeats = 1
        while codes[repeats * size : (repeats + 1) * size] == window:
            repeats += 1
        if repeats >= 2:
            profile["repeated_cycle"] = "->".join(window)
            profile["repeated_cycle_count"] = repeats
            break
    return profile


def _probable_cause(item: dict[str, Any]) -> str:
    """State what is measurably unusual about this sequence, and nothing more."""
    profile = sequence_profile(item.get("event_codes") or [])
    total = profile["total_events"]
    if not total:
        return "No event sequence was indexed for this pattern, so no cause can be derived from it."

    parts: list[str] = []
    share = profile["dominant_share"]
    parts.append(
        f"Template {profile['dominant_code']} accounts for {profile['dominant_count']} of "
        f"{total} events ({share:.0%}), across {profile['distinct_events']} distinct templates"
    )
    if profile["longest_run"] >= 3:
        parts.append(
            f"and repeats unbroken {profile['longest_run']} times "
            f"(template {profile['longest_run_code']})"
        )
    if profile["repeated_cycle_count"] >= 2:
        parts.append(
            f"; the block opens with the cycle {profile['repeated_cycle']} repeated "
            f"{profile['repeated_cycle_count']} times"
        )
    head = " ".join(parts).replace(" ;", ";") + "."

    # Characterise the shape, without claiming to know what the templates do.
    if share >= 0.5 and profile["longest_run"] >= 10:
        shape = (
            "The block is dominated by sustained repetition of a single template rather than a "
            "varied failure sequence, which is the signature of a retry or stalled-handler loop."
        )
    elif profile["repeated_cycle_count"] >= 3:
        shape = (
            "The block is cyclic: a short template sequence recurs, which is the signature of a "
            "repeated request or handshake rather than a one-off fault."
        )
    elif profile["distinct_events"] >= 8 and profile["longest_run"] <= 3:
        shape = (
            "The block shows high template variety with little repetition, which is the signature "
            "of a multi-stage sequence rather than a single stuck operation."
        )
    else:
        shape = "The block shows no dominant repetition or cycle in its template sequence."

    rarity = item.get("rarity_score")
    structural = item.get("structural_score")
    scored = ""
    if isinstance(rarity, (int, float)) and isinstance(structural, (int, float)):
        scored = (
            f" It was ranked on an unsupervised rarity score of {float(rarity):.3f} and a "
            f"structural-deviation score of {float(structural):.3f}, not on the dataset label."
        )

    legend = (
        " Template ids are Drain-parser specific and this dataset ships no legend, so resolve "
        f"{profile['dominant_code']} against your own Drain configuration to name the operation."
    )
    return f"{head} {shape}{scored}{legend}"


def rank_hdfs_anomalies(query: str, top_k: int = 3) -> dict[str, Any]:
    text = _query(query)
    requested = _limit(top_k, default=3)
    if requested > 3:
        raise ValueError("The anomaly result contract permits at most three findings")
    lexical = search_hdfs_logs(text, min(MAX_RESULTS, max(8, requested * 3)))
    vector = find_similar_hdfs_patterns(text, min(MAX_RESULTS, max(8, requested * 3)))
    rrf_scores = _rrf([lexical["results"], vector["results"]])
    by_id: dict[str, dict[str, Any]] = {}
    for item in lexical["results"] + vector["results"]:
        pattern_id = item.get("pattern_id") or item.get("id")
        if pattern_id:
            by_id.setdefault(pattern_id, item)

    ranked = []
    for pattern_id, item in by_id.items():
        anomaly = float(item.get("anomaly_score") or 0.0)
        fusion = rrf_scores.get(pattern_id, 0.0) + 0.02 * anomaly
        ranked.append({**item, "pattern_id": pattern_id, "fusion_score": fusion})
    ranked.sort(key=lambda item: (-item["fusion_score"], item["pattern_id"]))
    candidates = ranked[: max(requested, 1)]
    graph = expand_hdfs_graph([item["pattern_id"] for item in candidates], max_hops=2)
    source_records = _record_evidence([item["pattern_id"] for item in candidates])

    graph_by_pattern: dict[str, list[dict[str, Any]]] = {}
    for row in graph["results"]:
        graph_by_pattern.setdefault(str(row.get("pattern_id", "")), []).append(row)
    findings = []
    for rank, item in enumerate(candidates, start=1):
        pattern_id = item["pattern_id"]
        paths = graph_by_pattern.get(pattern_id, [])[:8]
        records = source_records.get(pattern_id, [])[:3]
        affected_blocks = sorted(
            {str(path.get("block_id")) for path in paths if path.get("block_id")}
        )[:8]
        affected_hosts = sorted(
            {str(path.get("host_id")) for path in paths if path.get("host_id")}
        )[:8]
        affected_processes = sorted(
            {str(path.get("process_id")) for path in paths if path.get("process_id")}
        )[:8]
        anomaly_score = float(item.get("anomaly_score") or 0.0)
        evidence_strength = min(
            0.99,
            0.45 + 0.35 * anomaly_score + (0.1 if paths else 0.0) + (0.05 if records else 0.0),
        )
        findings.append(
            {
                "rank": rank,
                "finding_id": f"hdfs-{pattern_id[:12]}",
                "pattern_id": pattern_id,
                "summary": item.get("text", "")[:600],
                "event_codes": item.get("event_codes", []),
                "severity": "HIGH" if anomaly_score >= 0.75 else "MEDIUM",
                "anomaly_score": round(anomaly_score, 6),
                "confidence": round(evidence_strength, 6),
                "fusion_score": round(float(item["fusion_score"]), 6),
                "occurrence_count": int(item.get("occurrence_count") or 0),
                "affected_blocks": affected_blocks,
                "affected_hosts": affected_hosts,
                "affected_processes": affected_processes,
                "graph_evidence": paths,
                "source_records": records,
                "probable_cause": _probable_cause(item),
                "why_ranked": [
                    "matched the operator question through lexical and/or Nova vector retrieval",
                    "received an unsupervised rarity and structural-deviation score",
                    "was linked to concrete HDFS blocks and events in Neptune",
                ],
                "action_plan": _action_plan(item),
                "validation_steps": [
                    "Collect a new post-action HDFS event window for the cited blocks.",
                    "Re-run lexical, Nova vector, and Neptune correlation against the new window.",
                    "Require the anomaly indicators and affected-block errors to decrease before closing the incident.",
                ],
                "uncertainties": [
                    "The corpus is a static benchmark snapshot, not live cluster telemetry.",
                    "The unsupervised score is evidence prioritization, not a calibrated failure probability.",
                ],
                "citations": [
                    {"store": "opensearch", "index": _required("OPENSEARCH_PATTERN_INDEX"), "id": pattern_id},
                    {"store": "neptune", "pattern_id": pattern_id},
                ]
                + [
                    {
                        "store": "opensearch",
                        "index": _required("OPENSEARCH_RECORD_INDEX"),
                        "id": record.get("record_id") or record.get("id"),
                        "source_uri": record.get("source_uri"),
                    }
                    for record in records
                ],
            }
        )
    if len(findings) != requested:
        raise RuntimeError(
            f"GraphRAG returned {len(findings)} findings; the request required {requested}"
        )
    return {
        "query": text,
        "findings": findings,
        "labels_used_for_ranking": False,
        "tool_trace": [
            {"tool": "search_hdfs_logs", "reason": lexical["reason"], "result_count": len(lexical["results"])},
            {"tool": "find_similar_hdfs_patterns", "reason": vector["reason"], "result_count": len(vector["results"])},
            {"tool": "expand_hdfs_graph", "reason": graph["reason"], "result_count": len(graph["results"])},
        ],
    }
