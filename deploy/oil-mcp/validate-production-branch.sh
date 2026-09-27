#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <branch-name> <trusted-branches-file>" >&2
  exit 2
fi

branch_name=$1
trusted_branches_file=$2

if [ ! -f "$trusted_branches_file" ]; then
  echo "Trusted branches file does not exist: $trusted_branches_file" >&2
  exit 2
fi

if ! git check-ref-format --branch "$branch_name" >/dev/null; then
  echo "Invalid deployment branch name" >&2
  exit 1
fi

if ! grep -Fx -- "$branch_name" "$trusted_branches_file" >/dev/null; then
  echo "Branch is not approved for oil MCP deployment: $branch_name" >&2
  exit 1
fi
