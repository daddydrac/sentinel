#!/usr/bin/env bash
set -euo pipefail

for command_name in terraform aws python3 curl jq; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

terraform_version="$(terraform version -json | jq -r .terraform_version)"
python3 - "$terraform_version" <<'PY'
import sys
version = tuple(int(part) for part in sys.argv[1].split(".")[:2])
# backend.hcl.example uses use_lockfile (S3 native state locking), added in 1.10.
if version < (1, 10):
    raise SystemExit(f"Terraform 1.10 or newer is required; found {sys.argv[1]}")
PY

region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
identity="$(aws sts get-caller-identity --output json)"
account_id="$(jq -r .Account <<<"$identity")"
principal_arn="$(jq -r .Arn <<<"$identity")"

if [[ -n "${EXPECTED_AWS_ACCOUNT_ID:-}" && "$account_id" != "$EXPECTED_AWS_ACCOUNT_ID" ]]; then
  echo "Refusing to continue: authenticated account $account_id does not match EXPECTED_AWS_ACCOUNT_ID." >&2
  exit 1
fi

model_id="${BEDROCK_MODEL_ID:-amazon.nova-lite-v1:0}"
embedding_model_id="${NOVA_EMBEDDING_MODEL_ID:-amazon.nova-2-multimodal-embeddings-v1:0}"
aws bedrock get-foundation-model --region "$region" --model-identifier "$model_id" >/dev/null
aws bedrock get-foundation-model --region "$region" --model-identifier "$embedding_model_id" >/dev/null
aws bedrock list-guardrails --region "$region" --max-results 1 >/dev/null
aws bedrock-agent list-agents --region "$region" --max-results 1 >/dev/null
aws bedrock-agentcore-control list-gateways --region "$region" --max-results 1 >/dev/null
aws emr-serverless list-applications --region "$region" --max-results 1 >/dev/null
aws opensearch list-domain-names --region "$region" >/dev/null
aws neptune describe-db-clusters --region "$region" --max-records 20 >/dev/null
aws sagemaker list-endpoints --region "$region" --max-results 1 >/dev/null
aws appsync list-apis --region "$region" --max-results 1 >/dev/null
aws amplify list-apps --region "$region" --max-results 1 >/dev/null
aws cognito-idp list-user-pools --region "$region" --max-results 1 >/dev/null
aws codebuild list-projects --region "$region" --sort-by NAME --sort-order ASCENDING >/dev/null
aws stepfunctions list-state-machines --region "$region" --max-results 1 >/dev/null
aws wafv2 list-web-acls --region us-east-1 --scope CLOUDFRONT --limit 1 >/dev/null
aws budgets describe-budgets --account-id "$account_id" --max-results 1 >/dev/null

echo "Preflight passed"
echo "  Account:   $account_id"
echo "  Principal: $principal_arn"
echo "  Region:    $region"
echo "  Model:     $model_id"
echo "  Embedding: $embedding_model_id"
