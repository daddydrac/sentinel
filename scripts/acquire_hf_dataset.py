#!/usr/bin/env python3
"""Acquire a pinned public Hugging Face dataset into S3 from CodeBuild.

The acquisition manifest is uploaded last and is the atomic readiness marker.
Only immutable revisions are accepted.  Every downloaded object is hashed while
streaming and, when Hugging Face exposes an LFS oid, that sha256 is enforced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from pathlib import Path
from typing import Any

import boto3


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LICENSE_RE = re.compile(r"^license:\s*mit\s*$", re.IGNORECASE | re.MULTILINE)
USER_AGENT = "hdfs-graphrag-managed-acquisition/1.0"


# Throttling and server faults are worth another attempt. Any other 4xx is a
# permanent answer -- a wrong revision, a missing path, a rejected page size --
# and retrying it only delays the real error behind half a minute of backoff.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _request(url: str, attempts: int = 6) -> urllib.response.addinfourl:
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            return urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as exc:
            # HTTPError subclasses URLError, so this clause must stay first.
            if exc.code not in RETRYABLE_STATUS or attempt == attempts - 1:
                raise
            time.sleep(min(2**attempt, 20))
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
            time.sleep(min(2**attempt, 20))
    raise RuntimeError("unreachable")


def _json(url: str) -> Any:
    with _request(url) as response:
        return json.load(response)


# The tree API rejects a page size above 100 with
# "Invalid limit for index tree pagination". Paginate rather than assuming the
# whole listing fits in one response.
TREE_PAGE_SIZE = 100
MAX_TREE_PAGES = 100


def _next_page(response: Any) -> str | None:
    """Parse the RFC 5988 Link header for the next page, if any."""
    link = response.headers.get("Link")
    if not link:
        return None
    for part in link.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        if any(segment.strip() == 'rel="next"' for segment in segments[1:]):
            return segments[0].strip().strip("<>")
    return None


def _tree(dataset: str, revision: str) -> list[dict[str, Any]]:
    encoded_dataset = urllib.parse.quote(dataset, safe="/")
    encoded_revision = urllib.parse.quote(revision, safe="")
    url: str | None = (
        f"https://huggingface.co/api/datasets/{encoded_dataset}/tree/"
        f"{encoded_revision}?recursive=true&expand=true&limit={TREE_PAGE_SIZE}"
    )
    entries: list[dict[str, Any]] = []
    for _ in range(MAX_TREE_PAGES):
        if url is None:
            return entries
        with _request(url) as response:
            payload = json.load(response)
            url = _next_page(response)
        if not isinstance(payload, list):
            raise RuntimeError("Hugging Face tree API returned an unexpected payload")
        entries.extend(item for item in payload if isinstance(item, dict))
    raise RuntimeError(f"Hugging Face tree listing exceeded {MAX_TREE_PAGES} pages")


def _download(dataset: str, revision: str, path: str, destination: Path) -> tuple[int, str]:
    encoded_dataset = urllib.parse.quote(dataset, safe="/")
    encoded_revision = urllib.parse.quote(revision, safe="")
    encoded_path = urllib.parse.quote(path, safe="/")
    url = (
        f"https://huggingface.co/datasets/{encoded_dataset}/resolve/"
        f"{encoded_revision}/{encoded_path}?download=true"
    )
    digest = hashlib.sha256()
    size = 0
    with _request(url) as response, destination.open("wb") as stream:
        while True:
            chunk = response.read(8 * 1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size == 0:
        raise RuntimeError(f"Downloaded an empty file: {path}")
    return size, digest.hexdigest()


def _expected_sha(entry: dict[str, Any]) -> str | None:
    lfs = entry.get("lfs")
    if not isinstance(lfs, dict):
        return None
    oid = lfs.get("oid")
    if not isinstance(oid, str):
        return None
    return oid.removeprefix("sha256:")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-parquet-files", type=int, required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args()

    if not SHA_RE.fullmatch(args.revision):
        raise SystemExit("revision must be an immutable 40-character git SHA")
    prefix = args.prefix.strip("/")
    if not prefix:
        raise SystemExit("prefix must not be empty")

    started_at = int(time.time())
    entries = _tree(args.dataset, args.revision)
    readme_entry = next((item for item in entries if item.get("path") == "README.md"), None)
    parquet_entries = sorted(
        (
            item
            for item in entries
            if isinstance(item.get("path"), str)
            and item["path"].startswith("data/")
            and item["path"].endswith(".parquet")
            and item.get("type") == "file"
        ),
        key=lambda item: item["path"],
    )
    if readme_entry is None:
        raise SystemExit("Pinned dataset revision has no README.md")
    if len(parquet_entries) != args.expected_parquet_files:
        raise SystemExit(
            f"Expected {args.expected_parquet_files} Parquet files at the pinned revision; "
            f"found {len(parquet_entries)}"
        )

    s3 = boto3.client("s3", region_name=args.region)
    kms_key_id = os.environ.get("KMS_KEY_ARN", "")
    put_args: dict[str, Any] = {"ServerSideEncryption": "aws:kms"}
    if kms_key_id:
        put_args["SSEKMSKeyId"] = kms_key_id

    acquired: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="hdfs-hf-") as temp_dir:
        temp = Path(temp_dir)
        selected = [readme_entry, *parquet_entries]
        for entry in selected:
            path = str(entry["path"])
            local_path = temp / Path(path).name
            size, sha256 = _download(args.dataset, args.revision, path, local_path)
            expected = _expected_sha(entry)
            if expected and sha256 != expected:
                raise SystemExit(f"LFS sha256 mismatch for {path}: {sha256} != {expected}")
            if path == "README.md":
                readme = local_path.read_text(encoding="utf-8")
                if not LICENSE_RE.search(readme):
                    raise SystemExit("Dataset license gate failed: pinned README is not MIT")
            key = f"{prefix}/{path}"
            s3.upload_file(
                str(local_path),
                args.bucket,
                key,
                ExtraArgs={
                    **put_args,
                    "Metadata": {
                        "source-dataset": args.dataset,
                        "source-revision": args.revision,
                        "sha256": sha256,
                    },
                },
            )
            acquired.append({"path": path, "s3_key": key, "bytes": size, "sha256": sha256})

    finished_at = int(time.time())
    manifest = {
        "schema_version": "1.0",
        "status": "READY",
        "dataset": args.dataset,
        "revision": args.revision,
        "license": "mit",
        "files": acquired,
        "parquet_file_count": len(parquet_entries),
        "total_bytes": sum(item["bytes"] for item in acquired if item["path"].endswith(".parquet")),
        "started_epoch": started_at,
        "finished_epoch": finished_at,
        "elapsed_seconds": finished_at - started_at,
    }
    manifest_key = f"{prefix}/acquisition-manifest.json"
    s3.put_object(
        Bucket=args.bucket,
        Key=manifest_key,
        Body=(json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        ContentType="application/json",
        Metadata={"status": "ready", "source-revision": args.revision},
        **put_args,
    )
    print(json.dumps({**manifest, "manifest_uri": f"s3://{args.bucket}/{manifest_key}"}))


if __name__ == "__main__":
    main()
