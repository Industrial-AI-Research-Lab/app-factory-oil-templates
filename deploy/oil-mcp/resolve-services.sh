#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <all|docling|domain-research|reporting|falkordb>" >&2
  exit 2
fi

case "$1" in
  all)
    echo "docling-mcp docling-adapter-mcp domain-research-mcp reporting-mcp falkordb-mcp"
    ;;
  docling)
    echo "docling-mcp docling-adapter-mcp"
    ;;
  domain-research)
    echo "domain-research-mcp"
    ;;
  reporting)
    echo "reporting-mcp"
    ;;
  falkordb)
    echo "falkordb-mcp"
    ;;
  *)
    echo "Unknown oil MCP deployment target: $1" >&2
    exit 1
    ;;
esac
