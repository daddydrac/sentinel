#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

"$repo_dir/scripts/preflight.sh"
terraform -chdir="$repo_dir/infra/bootstrap" init
terraform -chdir="$repo_dir/infra/bootstrap" apply -var="aws_region=$region"

bucket="$(terraform -chdir="$repo_dir/infra/bootstrap" output -raw bucket)"
kms_key="$(terraform -chdir="$repo_dir/infra/bootstrap" output -raw kms_key_arn)"

cp "$repo_dir/infra/backend.tf.example" "$repo_dir/infra/backend.tf"
sed \
  -e "s|bucket       = \"REPLACE_WITH_BOOTSTRAP_OUTPUT\"|bucket       = \"$bucket\"|" \
  -e "s|region       = \"us-east-1\"|region       = \"$region\"|" \
  -e "s|kms_key_id   = \"REPLACE_WITH_BOOTSTRAP_OUTPUT\"|kms_key_id   = \"$kms_key\"|" \
  "$repo_dir/infra/backend.hcl.example" > "$repo_dir/infra/backend.hcl"

echo "Remote state configuration created at infra/backend.hcl."
echo "Migrate the current state only after reviewing it:"
echo "  terraform -chdir=infra init -migrate-state -backend-config=backend.hcl"
