# Reporting MCP

`reporting-mcp` renders immutable research-result snapshots and uploads the
requested files plus `export-manifest.json` to caller-provided signed HTTPS
URLs. The service has one public MCP tool, `render_report`, and keeps no
server-side report state.

## Configuration

- `REPORTING_UPLOAD_ALLOWED_ORIGINS` — comma-separated HTTPS origins allowed
  for output and manifest uploads. Empty configuration fails closed when a
  request is validated.
- `REPORT_GRAPH_MAX_NODES` — maximum nodes in a knowledge subgraph, default
  `500`.
- `REPORT_GRAPH_MAX_EDGES` — maximum edges in a knowledge subgraph, default
  `2000`.

Knowledge reports may include an optional graph when `template_version` is
`1.2.0`:

```json
{
  "graph": {
    "nodes": [
      {"id": "n1", "label": "Объект", "type": "entity", "source_ids": ["s1"]}
    ],
    "edges": [
      {"id": "e1", "source": "n1", "target": "n1", "label": "связан", "source_ids": ["s1"]}
    ]
  }
}
```

Node and edge identifiers must be unique. Edge endpoints and every
`source_ids` entry must refer to objects present in the same result snapshot.
Oversized graphs are rejected; they are never silently truncated.
Published template versions `1.0.0` and `1.1.0` remain byte-stable and reject
non-empty graphs instead of changing their existing output.

Reader templates `news` `1.2.0` and `knowledge` `1.3.0`/`1.4.0` are built only
from `source_artifacts`. PDF uses a print layout (overview, event or answer
blocks, appendices with internal links); HTML is an interactive page with
filters, charts and a graph whose CSS, script and data are inline. Knowledge
sources may add one `role: "ontology"` document and one `role: "fact_graph"`
query over `relations`; the envelope may carry the analyst's `answers`.
Knowledge `1.4.0` additionally accepts one `role: "observations"`, one
`role: "coverage"` and one `role: "schema_counts"` query result each, adding
comparability groups, per-source coverage bars, schema counts and
value-vs-condition charts to the same page. It also accepts up to four
`role: "calculator"` bundles — the output of Domain MCP's
`validate_calculator_spec` tool — rendered as a «Калькуляторы» section: a row
picker and live formula result per bundle in HTML (inline `calc_formula.js`
and `calc.js`, no network), static tables and a server-rendered sensitivity
chart in PDF. The canonical `report.json` then includes the structured
`presentation` both layouts render. The full contract is
`docs/contracts/research-presentation.md`.

## Run

Prepare the runtime files as described in `deploy/oil-mcp/README.md`, including
`OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS`, then start the service:

```powershell
docker compose -f deploy/oil-mcp/docker-compose.yml up --build reporting-mcp
```

The Streamable HTTP endpoint is `http://localhost:8004/mcp`. The service does
not require AppFactory, MongoDB, S3, Tavily, or other provider credentials.
Rendering has a 180-second deadline. Every signed PUT gets a separate 10-second
deadline, and the tool-level budget reserves time for all seven outputs plus
the final manifest. The HTTP request body is limited to 12 MiB, including the
JSON-RPC envelope; canonical `report_data` is limited to 10 MiB. A generated
format is limited to 64 MiB, all formats in one request are limited to 192 MiB,
and at most two render jobs run concurrently.
Formats are rendered in isolated child processes, so a timed-out renderer is
terminated without discarding formats that already completed.

## Verify

Build the Dockerfile's test stage and mount the repository read-only:

```powershell
docker build --target test -f deploy/oil-mcp/reporting.Dockerfile -t reporting-test .
docker run --rm --mount "type=bind,source=$PWD,target=/workspace,readonly" reporting-test `
  pytest -q mcp/reporting/server/tests
```

`tests/test_calc_js_mirror.py` drives `calc_formula.js` through `node` to
check it against the Python formula grammar; it self-skips when `node` is not
on `PATH` and otherwise must run, not skip.

With the service running, discovery can be checked from the Compose network:

```powershell
docker run --rm --network oil-mcp_default `
  --mount "type=bind,source=$PWD,target=/workspace,readonly" reporting-test `
  fastmcp list http://reporting-mcp:8004/mcp --json --input-schema
```

Supported outputs are TXT, JSON, CSV, HTML, PDF, DOCX, and XLSX. HTML reports
are self-contained and do not load scripts, fonts, styles, or graph libraries
from the network.
