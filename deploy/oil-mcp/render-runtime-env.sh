#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <runtime-env-directory> <all|docling|domain-research|reporting|falkordb>" >&2
  exit 2
fi

runtime_env_dir=$1
deployment_target=$2

case "$deployment_target" in
  all|docling|domain-research|reporting|falkordb) ;;
  *)
    echo "Unknown oil MCP deployment target: $deployment_target" >&2
    exit 1
    ;;
esac

require_scalar() {
  local name=$1
  local value=${!name:-}
  if [ -z "$value" ] || [[ "$value" =~ ^[[:space:]]*$ ]]; then
    echo "Missing required environment variable: $name" >&2
    exit 1
  fi
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Environment variable must contain one line: $name" >&2
    exit 1
  fi
}

validate_optional_scalar() {
  local name=$1
  local value=${!name:-}
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Environment variable must contain one line: $name" >&2
    exit 1
  fi
}

validate_positive_integer() {
  local name=$1
  local default_value=$2
  local value=${!name:-$default_value}
  if ! [[ "$value" =~ ^[0-9]+$ ]] || (( value < 1 )); then
    echo "$name must be a positive integer" >&2
    exit 1
  fi
}

validate_optional_limit() {
  local name=$1
  local value=${!name:-}
  if [ -n "$value" ] && ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "$name must be a positive integer without leading zeros" >&2
    exit 1
  fi
}

needs_domain=false
needs_reporting=false
needs_falkordb=false
needs_proxy=false
case "$deployment_target" in
  all)
    needs_domain=true
    needs_reporting=true
    needs_falkordb=true
    needs_proxy=true
    ;;
  domain-research)
    needs_domain=true
    needs_proxy=true
    ;;
  reporting) needs_reporting=true ;;
  falkordb) needs_falkordb=true ;;
esac

if [ "$needs_proxy" = true ]; then
  require_scalar HIDDIFY_SUBSCRIPTION_URL
  if [[ ! "$HIDDIFY_SUBSCRIPTION_URL" =~ ^https?://[^[:space:]]+$ ]]; then
    echo "HIDDIFY_SUBSCRIPTION_URL must be an HTTP or HTTPS URL without whitespace" >&2
    exit 1
  fi
  if [[ "$HIDDIFY_SUBSCRIPTION_URL" == *'"'* || "$HIDDIFY_SUBSCRIPTION_URL" == *'\'* ]]; then
    echo "HIDDIFY_SUBSCRIPTION_URL must percent-encode quotes and backslashes" >&2
    exit 1
  fi
fi

if [ "$needs_domain" = true ]; then
  require_scalar OIL_MCP_TAVILY_API_KEY
  require_scalar OIL_MCP_BUNDLE_RECEIPT_KEY
  require_scalar OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS
  if [ "${#OIL_MCP_BUNDLE_RECEIPT_KEY}" -lt 32 ]; then
    echo "OIL_MCP_BUNDLE_RECEIPT_KEY must contain at least 32 bytes" >&2
    exit 1
  fi
  validate_optional_limit OIL_MCP_MAX_DOWNLOAD_BYTES
  validate_optional_limit OIL_MCP_MAX_QUERY_ROWS
  validate_optional_limit OIL_MCP_SQLITE_OPERATION_BUDGET
fi

if [ "$needs_reporting" = true ]; then
  require_scalar OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS
  validate_optional_scalar OIL_MCP_REPORT_GRAPH_MAX_NODES
  validate_optional_scalar OIL_MCP_REPORT_GRAPH_MAX_EDGES
  validate_positive_integer OIL_MCP_REPORT_GRAPH_MAX_NODES 500
  validate_positive_integer OIL_MCP_REPORT_GRAPH_MAX_EDGES 2000
fi

if [ "$needs_falkordb" = true ]; then
  require_scalar OIL_MCP_FALKORDB_HOST
  require_scalar OIL_MCP_FALKORDB_PASSWORD
  validate_optional_scalar OIL_MCP_FALKORDB_USERNAME
  validate_optional_scalar OIL_MCP_FALKORDB_DATABASE_PORT
  if [[ "$OIL_MCP_FALKORDB_HOST" == *://* || "$OIL_MCP_FALKORDB_HOST" == */* || "$OIL_MCP_FALKORDB_HOST" =~ [[:space:]] ]]; then
    echo "OIL_MCP_FALKORDB_HOST must be a hostname or IP address, not a URL" >&2
    exit 1
  fi
  database_port=${OIL_MCP_FALKORDB_DATABASE_PORT:-6379}
  if ! [[ "$database_port" =~ ^[0-9]+$ ]] || (( database_port < 1 || database_port > 65535 )); then
    echo "OIL_MCP_FALKORDB_DATABASE_PORT must be an integer from 1 to 65535" >&2
    exit 1
  fi
  if [ "$database_port" = 13000 ]; then
    echo "Port 13000 is the FalkorDB Browser HTTP endpoint, not the database endpoint" >&2
    exit 1
  fi
fi

mkdir -p "$runtime_env_dir"
chmod 700 "$runtime_env_dir"
umask 077
domain_env="$runtime_env_dir/domain-research.env"
reporting_env="$runtime_env_dir/reporting.env"
falkordb_env="$runtime_env_dir/falkordb.env"
proxy_env="$runtime_env_dir/hiddify-proxy.env"
hiddify_runtime_dir="$runtime_env_dir/hiddify"
hiddify_subscription_file="$hiddify_runtime_dir/subscription-url"
: > "$domain_env"
: > "$reporting_env"
: > "$falkordb_env"
: > "$proxy_env"

if [ "$needs_proxy" = true ]; then
  mkdir -p "$hiddify_runtime_dir"
  chmod 700 "$hiddify_runtime_dir"
  printf '%s\n' "$HIDDIFY_SUBSCRIPTION_URL" > "$hiddify_subscription_file"
  chmod 600 "$hiddify_subscription_file"
  printf 'HIDDIFY_DIRECT_ORIGINS=%s\n' "$OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS" > "$proxy_env"
fi

if [ "$needs_domain" = true ]; then
  {
    printf 'TAVILY_API_KEY=%s\n' "$OIL_MCP_TAVILY_API_KEY"
    printf 'BUNDLE_RECEIPT_KEY=%s\n' "$OIL_MCP_BUNDLE_RECEIPT_KEY"
    printf 'OBJECT_STORAGE_ALLOWED_ORIGINS=%s\n' "$OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS"
    printf 'ALLOW_INSECURE_OBJECT_STORAGE=true\n'
    if [ -n "${OIL_MCP_MAX_DOWNLOAD_BYTES:-}" ]; then
      printf 'MAX_DOWNLOAD_BYTES=%s\n' "$OIL_MCP_MAX_DOWNLOAD_BYTES"
    fi
    if [ -n "${OIL_MCP_MAX_QUERY_ROWS:-}" ]; then
      printf 'MAX_QUERY_ROWS=%s\n' "$OIL_MCP_MAX_QUERY_ROWS"
    fi
    if [ -n "${OIL_MCP_SQLITE_OPERATION_BUDGET:-}" ]; then
      printf 'SQLITE_OPERATION_BUDGET=%s\n' "$OIL_MCP_SQLITE_OPERATION_BUDGET"
    fi
  } >> "$domain_env"
fi

if [ "$needs_reporting" = true ]; then
  {
    printf 'REPORTING_UPLOAD_ALLOWED_ORIGINS=%s\n' "$OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS"
    printf 'ALLOW_INSECURE_OBJECT_STORAGE=true\n'
    printf 'REPORT_GRAPH_MAX_NODES=%s\n' "${OIL_MCP_REPORT_GRAPH_MAX_NODES:-500}"
    printf 'REPORT_GRAPH_MAX_EDGES=%s\n' "${OIL_MCP_REPORT_GRAPH_MAX_EDGES:-2000}"
  } >> "$reporting_env"
fi

if [ "$needs_falkordb" = true ]; then
  {
    printf 'FALKORDB_HOST=%s\n' "$OIL_MCP_FALKORDB_HOST"
    printf 'FALKORDB_PORT=%s\n' "${OIL_MCP_FALKORDB_DATABASE_PORT:-6379}"
    printf 'FALKORDB_USERNAME=%s\n' "${OIL_MCP_FALKORDB_USERNAME:-default}"
    printf 'FALKORDB_PASSWORD=%s\n' "$OIL_MCP_FALKORDB_PASSWORD"
  } >> "$falkordb_env"
fi
