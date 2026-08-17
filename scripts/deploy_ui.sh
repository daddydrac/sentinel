#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
for command_name in aws terraform jq node npm zip curl; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required UI deployment command: $command_name" >&2
    exit 1
  }
done

api_url="$(terraform -chdir="$repo_dir/infra" output -raw demo_url)"
events_endpoint="$(terraform -chdir="$repo_dir/infra" output -raw appsync_events_endpoint)"
user_pool_id="$(terraform -chdir="$repo_dir/infra" output -raw cognito_user_pool_id)"
user_pool_client_id="$(terraform -chdir="$repo_dir/infra" output -raw cognito_user_pool_client_id)"
app_id="$(terraform -chdir="$repo_dir/infra" output -raw amplify_app_id)"
branch="$(terraform -chdir="$repo_dir/infra" output -raw amplify_branch_name)"

mkdir -p "$repo_dir/ui/public"
jq -n \
  --arg apiUrl "$api_url" \
  --arg endpoint "$events_endpoint" \
  --arg region "$region" \
  --arg userPoolId "$user_pool_id" \
  --arg userPoolClientId "$user_pool_client_id" \
  '{apiUrl:$apiUrl,userPoolId:$userPoolId,userPoolClientId:$userPoolClientId,events:{endpoint:$endpoint,region:$region,defaultAuthMode:"userPool"}}' \
  > "$repo_dir/ui/public/runtime-config.json"

npm --prefix "$repo_dir/ui" ci --ignore-scripts
npm --prefix "$repo_dir/ui" run build

bundle_dir="$(mktemp -d)"
cleanup() { rm -rf -- "$bundle_dir"; }
trap cleanup EXIT
bundle="$bundle_dir/graphrag-ui.zip"
(cd "$repo_dir/ui/dist" && zip -qr "$bundle" .)

deployment="$(aws amplify create-deployment --region "$region" --app-id "$app_id" --branch-name "$branch")"
job_id="$(jq -r .jobId <<<"$deployment")"
upload_url="$(jq -r .zipUploadUrl <<<"$deployment")"
curl --fail --silent --show-error -X PUT -H 'Content-Type: application/zip' --upload-file "$bundle" "$upload_url"
aws amplify start-deployment --region "$region" --app-id "$app_id" --branch-name "$branch" --job-id "$job_id" >/dev/null

while true; do
  status="$(aws amplify get-job --region "$region" --app-id "$app_id" --branch-name "$branch" --job-id "$job_id" --query 'job.summary.status' --output text)"
  case "$status" in
    SUCCEED) break ;;
    FAILED|CANCELLED)
      aws amplify get-job --region "$region" --app-id "$app_id" --branch-name "$branch" --job-id "$job_id" --output json >&2
      exit 1
      ;;
    *) sleep 5 ;;
  esac
done

echo "Amplify deployment succeeded: $(terraform -chdir="$repo_dir/infra" output -raw graphrag_ui_url)"
