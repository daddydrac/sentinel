#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
var_file="${1:-$repo_dir/infra/environments/demo.tfvars}"
destroy_plan="$repo_dir/infra/destroy.tfplan"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

if [[ "${CONFIRM_DESTROY:-}" != "hpe-agentic-remediation-demo" ]]; then
  echo "Set CONFIRM_DESTROY=hpe-agentic-remediation-demo to destroy this project." >&2
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
if [[ ! -f "$var_file" ]]; then
  echo "Variable file not found: $var_file" >&2
  exit 1
fi
configured_region="$(awk -F'"' '/^[[:space:]]*aws_region[[:space:]]*=/{print $2; exit}' "$var_file")"
if [[ -n "$configured_region" && "$configured_region" != "$region" ]]; then
  echo "tfvars region $configured_region does not match authenticated region $region." >&2
  exit 1
fi
for command_name in aws terraform jq; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required teardown command: $command_name" >&2
    exit 1
  }
done

actual_account="$(aws sts get-caller-identity --query Account --output text)"
if [[ "$actual_account" != "$EXPECTED_AWS_ACCOUNT_ID" ]]; then
  echo "Authenticated account $actual_account does not match EXPECTED_AWS_ACCOUNT_ID." >&2
  exit 1
fi

echo "Destroy target verified: account=$actual_account region=$region"
if [[ -f "$repo_dir/infra/backend.hcl" ]]; then
  terraform -chdir="$repo_dir/infra" init -reconfigure -backend-config=backend.hcl
else
  terraform -chdir="$repo_dir/infra" init -reconfigure
fi
terraform -chdir="$repo_dir/infra" plan -destroy -var-file="$var_file" \
  -var="enable_model_endpoint=false" -out="$destroy_plan"
if [[ "${AUTO_APPROVE:-0}" == "1" ]]; then
  terraform -chdir="$repo_dir/infra" apply -auto-approve "$destroy_plan"
else
  terraform -chdir="$repo_dir/infra" apply "$destroy_plan"
fi

remaining_state="$(terraform -chdir="$repo_dir/infra" state list)"
if [[ -n "$remaining_state" ]]; then
  echo "Teardown state gate failed; Terraform still tracks resources:" >&2
  echo "$remaining_state" >&2
  exit 1
fi

# bootstrap_remote_state.sh creates this stack with its own local state. Destroy
# it last so the main stack can safely consume its remote state during teardown.
if [[ -s "$repo_dir/infra/bootstrap/terraform.tfstate" ]]; then
  terraform -chdir="$repo_dir/infra/bootstrap" init -backend=false
  if [[ "${AUTO_APPROVE:-0}" == "1" ]]; then
    terraform -chdir="$repo_dir/infra/bootstrap" destroy \
      -var="aws_region=$region" -auto-approve
  else
    terraform -chdir="$repo_dir/infra/bootstrap" destroy -var="aws_region=$region"
  fi
fi

# Remove only generated files that point at the destroyed backend or expose
# disposable browser configuration. Source examples remain intact.
rm -f -- "$repo_dir/infra/backend.tf" "$repo_dir/infra/backend.hcl" \
  "$repo_dir/ui/public/runtime-config.json"

tagged="$(aws resourcegroupstaggingapi get-resources \
  --region "$region" \
  --tag-filters Key=Project,Values=HPE-Agentic-Remediation-Demo \
  --output json)"
if [[ "$region" != "us-east-1" ]]; then
  global_tagged="$(aws resourcegroupstaggingapi get-resources \
    --region us-east-1 \
    --tag-filters Key=Project,Values=HPE-Agentic-Remediation-Demo \
    --output json)"
  tagged="$(jq -s '{ResourceTagMappingList: ([.[].ResourceTagMappingList[]] | unique_by(.ResourceARN))}' \
    <(printf '%s' "$tagged") <(printf '%s' "$global_tagged"))"
fi
non_kms_count="$(jq '[.ResourceTagMappingList[].ResourceARN | select(contains(":kms:") | not)] | length' <<<"$tagged")"
kms_count="$(jq '[.ResourceTagMappingList[].ResourceARN | select(contains(":kms:"))] | length' <<<"$tagged")"
if [[ "$non_kms_count" -ne 0 ]]; then
  echo "Teardown residual gate failed; tagged non-KMS resources remain:" >&2
  jq -r '.ResourceTagMappingList[].ResourceARN | select(contains(":kms:") | not)' <<<"$tagged" >&2
  exit 1
fi
if [[ "$kms_count" -gt 0 ]]; then
  echo "$kms_count KMS key(s) remain in AWS-mandated pending-deletion windows; they cannot be used."
fi
echo "Terraform-managed application, data, model, network, UI, and remote-state resources are destroyed."
