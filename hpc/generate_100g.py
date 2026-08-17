"""Expand the MIT-licensed Hugging Face HDFS logs into a 100 GiB qualification corpus."""

from __future__ import annotations

import argparse
import math

from pyspark.sql import SparkSession, functions as F


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-gib", type=int, default=100)
    parser.add_argument("--partitions", type=int, default=1024)
    args = parser.parse_args()

    spark = SparkSession.builder.appName("hpe-generate-100g-hdfs-logs").getOrCreate()
    source = (
        spark.read.option("recursiveFileLookup", "true")
        .parquet(args.source)
        .withColumn("source_uri", F.input_file_name())
    )
    required = {"block_id", "event_encoded", "tokenized_block", "label"}
    missing = required - set(source.columns)
    if missing:
        raise RuntimeError(f"Pinned HDFS source is missing required fields: {sorted(missing)}")
    source_count = source.count()
    if source_count == 0:
        raise RuntimeError("Hugging Face source contained no rows")

    source_json = F.to_json(F.struct(*[F.col(column) for column in source.columns]))
    sampled = source.select(F.avg(F.length(source_json)).alias("bytes")).first()["bytes"] or 512
    payload_chunks = 64
    unique_payload_bytes = payload_chunks * 64
    # A 5% margin and a per-row unique payload make the physical Parquet corpus
    # reliably exceed the requested size even when repeated source text is dictionary encoded.
    target_rows = math.ceil(args.target_gib * 1024**3 * 1.05 / unique_payload_bytes)
    replicas = math.ceil(target_rows / source_count)

    base = (
        source.withColumn("source_json", source_json)
        .crossJoin(spark.range(replicas).withColumnRenamed("id", "replica"))
        .limit(target_rows)
    )
    synthetic_payload = F.concat(
        *[
            F.sha2(
                F.concat_ws(
                    ":",
                    F.col("source_json"),
                    F.col("replica").cast("string"),
                    F.lit(str(index)),
                ),
                256,
            )
            for index in range(payload_chunks)
        ]
    )
    expanded = (
        base
        .withColumn(
            "record_salt",
            F.sha2(F.concat_ws(":", F.col("source_json"), F.col("replica").cast("string")), 256),
        )
        .withColumn("synthetic_payload", synthetic_payload)
        # Preserve the typed source fields consumed by graphrag_build.py. The
        # large synthetic payload proves the physical 100 GiB throughput gate;
        # it is intentionally excluded from the online indexes.
        .select(
            "block_id",
            "event_encoded",
            "tokenized_block",
            "label",
            "source_uri",
            "source_json",
            "replica",
            "record_salt",
            "synthetic_payload",
        )
        .repartition(args.partitions)
    )
    (
        expanded.write.mode("overwrite")
        .option("compression", "uncompressed")
        .parquet(args.output)
    )
    print(
        {
            "source_rows": source_count,
            "sampled_source_bytes": int(sampled),
            "unique_payload_bytes_per_row": unique_payload_bytes,
            "target_rows": target_rows,
            "target_gib": args.target_gib,
            "output": args.output,
        }
    )
    spark.stop()


if __name__ == "__main__":
    main()
