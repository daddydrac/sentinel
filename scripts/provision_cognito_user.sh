#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
email="${1:-${USER_EMAIL:-}}"
groups_csv="${2:-${USER_GROUPS:-approver}}"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

if [[ -z "$email" || ! "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "Usage: $0 user@example.com approver[,admin]" >&2
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
for command_name in aws terraform; do
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

IFS=',' read -r -a requested_groups <<<"$groups_csv"
if [[ "${#requested_groups[@]}" -eq 0 ]]; then
  echo "At least one Cognito group is required." >&2
  exit 1
fi
# macOS ships bash 3.2 as /bin/bash, which has no associative arrays, so the
# desired set is tracked as a space-delimited string instead of `declare -A`.
desired_groups=""
contains_group() { [[ " $1 " == *" $2 "* ]]; }
for group in "${requested_groups[@]}"; do
  if [[ "$group" != "investigator" && "$group" != "approver" && "$group" != "admin" ]]; then
    echo "Unsupported group '$group'; use investigator, approver, or admin." >&2
    exit 1
  fi
  contains_group "$desired_groups" "$group" || desired_groups="$desired_groups $group"
done
desired_groups="${desired_groups# }"

user_pool_id="$(terraform -chdir="$repo_dir/infra" output -raw cognito_user_pool_id)"
if ! aws cognito-idp admin-get-user --region "$region" --user-pool-id "$user_pool_id" \
  --username "$email" >/dev/null 2>&1; then
  aws cognito-idp admin-create-user --region "$region" --user-pool-id "$user_pool_id" \
    --username "$email" \
    --user-attributes "Name=email,Value=$email" \
    --desired-delivery-mediums EMAIL >/dev/null
  echo "Created $email and requested a Cognito temporary-password invitation."
else
  echo "Cognito user $email already exists; reconciling group membership."
fi

current_membership="$(aws cognito-idp admin-list-groups-for-user --region "$region" \
  --user-pool-id "$user_pool_id" --username "$email" \
  --query 'Groups[].GroupName' --output text)"
for group in $current_membership; do
  if [[ "$group" == "investigator" || "$group" == "approver" || "$group" == "admin" ]] \
    && ! contains_group "$desired_groups" "$group"; then
    aws cognito-idp admin-remove-user-from-group --region "$region" \
      --user-pool-id "$user_pool_id" --username "$email" --group-name "$group"
  fi
done
for group in $desired_groups; do
  aws cognito-idp admin-add-user-to-group --region "$region" --user-pool-id "$user_pool_id" \
    --username "$email" --group-name "$group"
done

membership="$(aws cognito-idp admin-list-groups-for-user --region "$region" \
  --user-pool-id "$user_pool_id" --username "$email" \
  --query 'Groups[].GroupName' --output text)"
echo "Cognito access reconciled: user=$email groups=${membership//$'\t'/,}"
