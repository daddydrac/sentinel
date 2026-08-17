#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
if [[ "${CONFIRM_HPC_COST:-}" != "100G-GRAPHRAG" ]]; then
  echo "This starts a high-capacity EMR/OpenSearch/Neptune workload." >&2
  echo "Set CONFIRM_HPC_COST=100G-GRAPHRAG to continue." >&2
  exit 1
fi
if [[ -z "${EXPECTED_AWS_ACCOUNT_ID:-}" || -z "${EXPECTED_AWS_REGION:-}" ]]; then
  echo "EXPECTED_AWS_ACCOUNT_ID and EXPECTED_AWS_REGION are mandatory." >&2
  exit 1
fi
if [[ "$region" != "$EXPECTED_AWS_REGION" ]]; then
  echo "Configured region $region does not match EXPECTED_AWS_REGION." >&2
  exit 1
fi
for command_name in aws terraform jq; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done
actual_account="$(aws sts get-caller-identity --query Account --output text)"
if [[ "$actual_account" != "$EXPECTED_AWS_ACCOUNT_ID" ]]; then
  echo "Authenticated account $actual_account does not match EXPECTED_AWS_ACCOUNT_ID." >&2
  exit 1
fi

state_machine_arn="$(terraform -chdir="$repo_dir/infra" output -raw graphrag_ingestion_state_machine_arn)"
bucket="$(terraform -chdir="$repo_dir/infra" output -raw evidence_bucket)"
dataset_revision="$(terraform -chdir="$repo_dir/infra" output -raw dataset_revision)"
run_id="$(date -u +%Y%m%d-%H%M%S)-$$"
execution_name="graphrag-${run_id}"
started_epoch="$(date +%s)"

execution_arn="$(aws stepfunctions start-execution --region "$region" \
  --state-machine-arn "$state_machine_arn" \
  --name "$execution_name" \
  --input "$(jq -nc --arg run_id "$run_id" '{run_id:$run_id}')" \
  --query executionArn --output text)"
echo "Managed 100 GiB GraphRAG ingestion started: $execution_arn"

while true; do
  execution="$(aws stepfunctions describe-execution --region "$region" \
    --execution-arn "$execution_arn" --output json)"
  status="$(jq -r .status <<<"$execution")"
  case "$status" in
    SUCCEEDED) break ;;
    FAILED|TIMED_OUT|ABORTED)
      echo "GraphRAG ingestion state machine failed:" >&2
      jq '{status,error,cause,executionArn,startDate,stopDate}' <<<"$execution" >&2
      exit 1
      ;;
    RUNNING) sleep 5 ;;
    *) echo "Unexpected Step Functions status: $status" >&2; exit 1 ;;
  esac
done

manifest_prefix="hpc/graphrag/runs/$run_id/manifests/published/"
manifest_key="$(aws s3api list-objects-v2 --region "$region" --bucket "$bucket" \
  --prefix "$manifest_prefix" \
  --query 'Contents[?Size>`0` && starts_with(Key, `hpc/`) && contains(Key, `/part-`)].Key | [0]' \
  --output text)"
if [[ "$manifest_key" == "None" || -z "$manifest_key" ]]; then
  echo "State machine succeeded but no published manifest exists for $run_id." >&2
  exit 1
fi

benchmark_tmp="$(mktemp -d)"
cleanup() { rm -rf -- "$benchmark_tmp"; }
trap cleanup EXIT
aws s3 cp "s3://$bucket/$manifest_key" "$benchmark_tmp/manifest.json" \
  --region "$region" --only-show-errors
minimum_bytes=$((100 * 1024 * 1024 * 1024))
if ! jq -e --arg run_id "$run_id" --arg revision "$dataset_revision" --argjson minimum "$minimum_bytes" '
  .status == "PUBLISHED" and
  .run_id == $run_id and
  .dataset_revision == $revision and
  .slo_passed_inside_spark == true and
  .input_payload_bytes >= $minimum and
  .labels_isolated == true and
  .l2_normalized == true and
  .run_isolated == true and
  .neptune_relationships_validated == true and
  .neptune_records == .expected_graph_records
' "$benchmark_tmp/manifest.json" >/dev/null; then
  echo "Published GraphRAG manifest failed the client-side acceptance contract:" >&2
  jq . "$benchmark_tmp/manifest.json" >&2
  exit 1
fi

overall_seconds="$(( $(date +%s) - started_epoch ))"
jq -n \
  --arg run_id "$run_id" \
  --arg execution_arn "$execution_arn" \
  --arg dataset_revision "$dataset_revision" \
  --arg manifest "s3://$bucket/$manifest_key" \
  --argjson overall_seconds "$overall_seconds" \
  --slurpfile accepted "$benchmark_tmp/manifest.json" \
  '{
    run_id:$run_id,
    execution_arn:$execution_arn,
    dataset_revision:$dataset_revision,
    manifest:$manifest,
    overall_pipeline_seconds:$overall_seconds,
    staged_data_graphrag_seconds:$accepted[0].elapsed_seconds_inside_spark,
    slo_seconds:$accepted[0].slo_seconds,
    slo_passed:$accepted[0].slo_passed_inside_spark,
    query_ready_smoke_passed:true,
    graph_records:$accepted[0].neptune_records,
    opensearch_records:$accepted[0].records,
    patterns:$accepted[0].patterns
  }'
