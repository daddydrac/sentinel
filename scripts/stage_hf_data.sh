#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
for command_name in aws terraform jq; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done

project="$(terraform -chdir="$repo_dir/infra" output -raw dataset_acquisition_project)"
source_prefix="$(terraform -chdir="$repo_dir/infra" output -raw dataset_source_prefix)"
bucket="${source_prefix#s3://}"
bucket="${bucket%%/*}"
key_prefix="${source_prefix#s3://$bucket/}"
key_prefix="${key_prefix%/}"

if [[ -z "$project" || -z "$bucket" || -z "$key_prefix" ]]; then
  echo "Terraform did not return a complete managed acquisition configuration." >&2
  exit 1
fi

build_id="$(aws codebuild start-build --region "$region" --project-name "$project" \
  --query 'build.id' --output text)"
echo "Managed dataset acquisition started: $build_id"
while true; do
  build_status="$(aws codebuild batch-get-builds --region "$region" --ids "$build_id" \
    --query 'builds[0].buildStatus' --output text)"
  case "$build_status" in
    SUCCEEDED) break ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      aws codebuild batch-get-builds --region "$region" --ids "$build_id" --output json >&2
      exit 1
      ;;
    IN_PROGRESS) sleep 5 ;;
    *) echo "Unexpected CodeBuild state: $build_status" >&2; exit 1 ;;
  esac
done

stage_tmp="$(mktemp -d)"
cleanup() { rm -rf -- "$stage_tmp"; }
trap cleanup EXIT
aws s3api get-object --region "$region" --bucket "$bucket" \
  --key "$key_prefix/acquisition-manifest.json" "$stage_tmp/acquisition-manifest.json" \
  >/dev/null
manifest="$(<"$stage_tmp/acquisition-manifest.json")"
if ! jq -e '
  .status == "READY" and
  .license == "mit" and
  (.revision | test("^[0-9a-f]{40}$")) and
  .parquet_file_count > 0 and
  .total_bytes > 0 and
  ([.files[] | select(.path | endswith(".parquet")) | .sha256 | test("^[0-9a-f]{64}$")] | all)
' <<<"$manifest" >/dev/null; then
  echo "Managed acquisition manifest failed its readiness contract." >&2
  jq . <<<"$manifest" >&2 || true
  exit 1
fi

jq --arg build_id "$build_id" --arg source "$source_prefix" \
  '{phase:"managed_acquisition",build_id:$build_id,source:$source,dataset,revision,total_bytes,elapsed_seconds}' \
  <<<"$manifest"
