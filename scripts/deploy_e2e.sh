#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
var_file="${1:-$repo_dir/infra/environments/demo.tfvars}"

if [[ "${CONFIRM_HPC_COST:-}" != "100G-GRAPHRAG" ]]; then
  echo "End-to-end deployment provisions the benchmark-sized managed data plane and processes 100 GiB." >&2
  echo "Set CONFIRM_HPC_COST=100G-GRAPHRAG to acknowledge the cost boundary." >&2
  exit 1
fi
if [[ -z "${INITIAL_OPERATOR_EMAIL:-}" ]]; then
  echo "Set INITIAL_OPERATOR_EMAIL to the email address for the first Cognito operator." >&2
  echo "INITIAL_OPERATOR_GROUPS defaults to admin; use approver or approver,admin if preferred." >&2
  exit 1
fi

SKIP_UI=1 "$repo_dir/scripts/deploy.sh" "$var_file"
"$repo_dir/scripts/provision_cognito_user.sh" \
  "$INITIAL_OPERATOR_EMAIL" "${INITIAL_OPERATOR_GROUPS:-admin}"
if [[ -n "${DEPLOY_USERS_FILE:-}" ]]; then
  "$repo_dir/scripts/provision_cognito_users.sh" "$DEPLOY_USERS_FILE"
fi
"$repo_dir/scripts/run_100g_benchmark.sh"
"$repo_dir/scripts/deploy_ui.sh"

echo
echo "End-to-end GraphRAG system is deployed, loaded, smoke-tested, and published."
echo "UI: $(terraform -chdir="$repo_dir/infra" output -raw graphrag_ui_url)"
echo "Initial Cognito operator: $INITIAL_OPERATOR_EMAIL (${INITIAL_OPERATOR_GROUPS:-admin})"
