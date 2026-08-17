#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
var_file="${1:-$repo_dir/infra/environments/demo.tfvars}"
foundation_plan="$repo_dir/infra/foundation.tfplan"
complete_plan="$repo_dir/infra/complete.tfplan"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

if [[ ! -f "$var_file" ]]; then
  echo "Variable file not found: $var_file" >&2
  exit 1
fi
if [[ -z "${EXPECTED_AWS_ACCOUNT_ID:-}" ]]; then
  echo "Set EXPECTED_AWS_ACCOUNT_ID to the exact target account before deployment." >&2
  exit 1
fi
if [[ -z "${EXPECTED_AWS_REGION:-}" || "$region" != "$EXPECTED_AWS_REGION" ]]; then
  echo "Set EXPECTED_AWS_REGION to the exact target region; authenticated region is $region." >&2
  exit 1
fi
configured_region="$(awk -F'"' '/^[[:space:]]*aws_region[[:space:]]*=/{print $2; exit}' "$var_file")"
if [[ -n "$configured_region" && "$configured_region" != "$region" ]]; then
  echo "tfvars region $configured_region does not match authenticated region $region." >&2
  exit 1
fi
for command_name in aws terraform curl jq node npm zip; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required deployment command: $command_name" >&2
    exit 1
  }
done

configured_agent_model="$(awk -F'"' '/^[[:space:]]*bedrock_model_id[[:space:]]*=/{print $2; exit}' "$var_file")"
model_id="$(awk -F'"' '/^[[:space:]]*sagemaker_model_id[[:space:]]*=/{print $2; exit}' "$var_file")"
model_revision="$(awk -F'"' '/^[[:space:]]*sagemaker_model_revision[[:space:]]*=/{print $2; exit}' "$var_file")"
model_id="${model_id:-Qwen/Qwen3-8B}"
model_revision="${model_revision:-b968826d9c46dd6066d109eabc6255188de91218}"
export BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-${configured_agent_model:-amazon.nova-lite-v1:0}}"

model_metadata="$(curl --fail --silent --show-error "https://huggingface.co/api/models/$model_id")"
revision_metadata="$(curl --fail --silent --show-error \
  "https://huggingface.co/api/models/$model_id/revision/$model_revision")"
actual_revision="$(jq -r '.sha // empty' <<<"$revision_metadata")"
license="$(jq -r '.cardData.license // empty' <<<"$model_metadata")"
gated="$(jq -r '.gated // true' <<<"$model_metadata")"
private="$(jq -r '.private // true' <<<"$model_metadata")"
if [[ "$license" != "apache-2.0" || "$gated" != "false" || "$private" != "false" ]]; then
  echo "Refusing model deployment: $model_id must be public, non-gated, and Apache-2.0." >&2
  exit 1
fi
if [[ "$actual_revision" != "$model_revision" ]]; then
  echo "Refusing model deployment: configured revision $model_revision does not match $actual_revision." >&2
  exit 1
fi

"$repo_dir/scripts/preflight.sh"
if [[ -f "$repo_dir/infra/backend.hcl" ]]; then
  terraform -chdir="$repo_dir/infra" init -backend-config=backend.hcl
else
  terraform -chdir="$repo_dir/infra" init
fi
terraform -chdir="$repo_dir/infra" fmt -recursive
terraform -chdir="$repo_dir/infra" fmt -check -recursive
terraform -chdir="$repo_dir/infra" validate

# Provision the dependencies needed to build the account-owned inference image,
# without creating an endpoint that references an image that does not yet exist.
terraform -chdir="$repo_dir/infra" plan \
  -var-file="$var_file" -var="enable_model_endpoint=false" -out="$foundation_plan"
if [[ "${AUTO_APPROVE:-0}" == "1" ]]; then
  terraform -chdir="$repo_dir/infra" apply -auto-approve "$foundation_plan"
else
  terraform -chdir="$repo_dir/infra" apply "$foundation_plan"
fi

build_project="$(terraform -chdir="$repo_dir/infra" output -raw model_build_project)"
build_id="$(aws codebuild start-build --region "$region" --project-name "$build_project" --query 'build.id' --output text)"
echo "Building the pinned open-weight inference image: $build_id"
while true; do
  build_status="$(aws codebuild batch-get-builds --region "$region" --ids "$build_id" --query 'builds[0].buildStatus' --output text)"
  case "$build_status" in
    SUCCEEDED) break ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      aws codebuild batch-get-builds --region "$region" --ids "$build_id" --output json >&2
      exit 1
      ;;
    *) sleep 10 ;;
  esac
done

# Create the SageMaker endpoint and chat worker only after resolving the built
# image to an immutable digest. The mutable build tag is never used at runtime.
repository_name="$(terraform -chdir="$repo_dir/infra" output -raw model_repository_name)"
image_digest="$(aws ecr describe-images --region "$region" \
  --repository-name "$repository_name" --image-ids imageTag=latest \
  --query 'imageDetails[0].imageDigest' --output text)"
if [[ ! "$image_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "CodeBuild did not publish a valid immutable ECR digest." >&2
  exit 1
fi
terraform -chdir="$repo_dir/infra" plan -var-file="$var_file" \
  -var="sagemaker_image_digest=$image_digest" -out="$complete_plan"
if [[ "${AUTO_APPROVE:-0}" == "1" ]]; then
  terraform -chdir="$repo_dir/infra" apply -auto-approve "$complete_plan"
else
  terraform -chdir="$repo_dir/infra" apply "$complete_plan"
fi

if [[ "${SKIP_UI:-0}" != "1" ]]; then
  "$repo_dir/scripts/deploy_ui.sh"
fi

echo
echo "Complete GraphRAG deployment finished."
echo "Amplify UI: $(terraform -chdir="$repo_dir/infra" output -raw graphrag_ui_url)"
echo "Preserved remediation UI: $(terraform -chdir="$repo_dir/infra" output -raw open_demo_command)"
if [[ "${SKIP_UI:-0}" == "1" ]]; then
  echo "Infrastructure phase complete; the end-to-end wrapper will now load GraphRAG data."
fi
