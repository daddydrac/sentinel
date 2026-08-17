#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
demo_url="$(terraform -chdir="$repo_dir/infra" output -raw demo_url)"
demo_token="$(terraform -chdir="$repo_dir/infra" output -raw demo_token)"

created="$(curl -fsS -X POST "$demo_url/api/workflows" \
  -H "content-type: application/json" \
  -H "x-demo-token: $demo_token" \
  -d '{"scenario_id":"storage_path_regression"}')"
workflow_id="$(jq -r .workflow_id <<<"$created")"

for _ in $(seq 1 180); do
  state="$(curl -fsS "$demo_url/api/workflows/$workflow_id" -H "x-demo-token: $demo_token")"
  status="$(jq -r .status <<<"$state")"
  if [[ "$status" == "AWAITING_APPROVAL" ]]; then
    break
  fi
  sleep 1
done

if [[ "${status:-}" != "AWAITING_APPROVAL" ]]; then
  echo "Workflow did not reach approval: ${status:-unknown}" >&2
  exit 1
fi

plan_hash="$(jq -r .plan.plan_hash <<<"$state")"
curl -fsS -X POST "$demo_url/api/workflows/$workflow_id/decision" \
  -H "content-type: application/json" \
  -H "x-demo-token: $demo_token" \
  -d "{\"approved\":true,\"plan_hash\":\"$plan_hash\"}" >/dev/null

for _ in $(seq 1 180); do
  state="$(curl -fsS "$demo_url/api/workflows/$workflow_id" -H "x-demo-token: $demo_token")"
  status="$(jq -r .status <<<"$state")"
  if [[ "$status" == "VERIFIED" || "$status" == "COMPENSATED" || "$status" == "DENIED" || "$status" == "FAILED" ]]; then
    break
  fi
  sleep 1
done

jq '{workflow_id,status,step,policy:.policy.decision,tool:.plan.tool,verification:.verification.passed}' <<<"$state"
[[ "$status" == "VERIFIED" ]]
