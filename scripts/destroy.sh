#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Kept as the stable command name for existing users. The implementation now
# performs the complete, guarded project teardown.
exec "$repo_dir/scripts/destroy_all.sh" "$@"
