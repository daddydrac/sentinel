#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
users_file="${1:-${USERS_FILE:-}}"

if [[ -z "$users_file" || ! -f "$users_file" ]]; then
  echo "Usage: $0 path/to/users.csv" >&2
  echo "CSV format: email,groups where groups is a comma-separated role list." >&2
  exit 1
fi

line_number=0
provisioned=0
while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
  line_number=$((line_number + 1))
  line="${raw_line%$'\r'}"
  [[ -z "$line" || "$line" == \#* ]] && continue
  if [[ "$line" == "email,groups" ]]; then
    continue
  fi
  email="${line%%,*}"
  groups="${line#*,}"
  if [[ "$groups" == "$line" ]]; then
    echo "Invalid users CSV at line $line_number: expected email,group[,group]." >&2
    exit 1
  fi
  "$repo_dir/scripts/provision_cognito_user.sh" "$email" "$groups"
  provisioned=$((provisioned + 1))
done < "$users_file"

if [[ "$provisioned" -eq 0 ]]; then
  echo "Users CSV did not contain any assignments." >&2
  exit 1
fi
echo "Reconciled $provisioned Cognito user assignment(s) from $users_file."
