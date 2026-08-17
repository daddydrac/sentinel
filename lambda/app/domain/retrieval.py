"""Hybrid retrieval adapter: lexical, vector, graph, and live state."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, deque
from typing import Any

from domain.model import canonical_hash, utc_now


TOKEN_RE = re.compile(r"[a-zA-Z0-9_.:-]+")


def tokenize(value: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(value)]


def _stable_vector(value: str, dimensions: int = 64) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(value):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(component * component for component in vector))
    return vector if norm == 0 else [component / norm for component in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _documents(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    return scenario["logs"] + scenario["incidents"] + scenario["runbooks"]


def _lexical_results(
    query: str, documents: list[dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    query_terms = tokenize(query)
    document_terms = [tokenize(document["text"]) for document in documents]
    document_frequency = Counter()
    for terms in document_terms:
        document_frequency.update(set(terms))

    results = []
    for document, terms in zip(documents, document_terms):
        counts = Counter(terms)
        score = 0.0
        for term in query_terms:
            if counts[term]:
                inverse_frequency = math.log(
                    (len(documents) + 1) / (document_frequency[term] + 0.5)
                ) + 1
                score += inverse_frequency * (counts[term] / (counts[term] + 1.2))
        if score:
            results.append({**document, "score": round(score, 4), "retriever": "BM25-like lexical"})
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]


def _vector_results(
    query: str, documents: list[dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    query_vector = _stable_vector(query)
    results = []
    for document in documents:
        score = _cosine(query_vector, _stable_vector(document["text"]))
        results.append({**document, "score": round(score, 4), "retriever": "hashed-vector cosine"})
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]


def _graph_paths(scenario: dict[str, Any], max_hops: int = 2) -> list[dict[str, Any]]:
    start = scenario["affected_node"]
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for source, relationship, target in scenario["graph_edges"]:
        adjacency.setdefault(source, []).append((relationship, target))

    queue = deque([(start, [])])
    paths: list[dict[str, Any]] = []
    visited = {(start, 0)}
    while queue:
        node, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        for relationship, target in adjacency.get(node, []):
            next_path = path + [{"from": node, "relationship": relationship, "to": target}]
            paths.append({"id": f"graph-{len(paths) + 1:03d}", "path": next_path, "hops": len(next_path)})
            marker = (target, len(next_path))
            if marker not in visited:
                visited.add(marker)
                queue.append((target, next_path))
    return paths[:6]


def retrieve(scenario: dict[str, Any]) -> dict[str, Any]:
    documents = _documents(scenario)
    lexical = _lexical_results(scenario["query"], documents)
    vector = _vector_results(scenario["query"], documents)
    graph = _graph_paths(scenario)
    live = [
        {**item, "source": "simulated authoritative live state", "retriever": "live-state lookup"}
        for item in scenario["live_state"]
    ]
    evidence_ids = sorted(
        {item["id"] for item in lexical + vector}
        | {item["id"] for item in graph}
        | {item["id"] for item in live}
    )
    evidence_content = {
        "query": scenario["query"],
        "lexical": lexical,
        "vector": vector,
        "graph": graph,
        "live": live,
        "evidence_ids": evidence_ids,
    }
    packet = {
        **evidence_content,
        "assembled_at": utc_now(),
        "manifest_hash": canonical_hash(evidence_content),
    }
    return packet
