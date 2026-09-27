import os
from urllib.parse import urlsplit


SERVICE_ID = "reporting-mcp"
TEMPLATE_IDS = ("news", "knowledge")
TEMPLATE_VERSIONS = {
    "news": ("1.0.0", "1.1.0", "1.2.0", "1.3.0"),
    "knowledge": ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"),
}
READER_VERSIONS = {"news": ("1.2.0",), "knowledge": ("1.3.0", "1.4.0")}
BROAD_VERSIONS = {"news": "1.3.0"}
FACET_VERSIONS = {"knowledge": ("1.4.0",)}
FACET_ROLES = ("observations", "coverage", "schema_counts")
CALCULATOR_ROLE = "calculator"
MAX_CALCULATORS = 4


def is_reader(template_id, template_version):
    return template_version in READER_VERSIONS.get(template_id, ())


def reader_variant(template_id, template_version):
    if template_version == BROAD_VERSIONS.get(template_id):
        return "broad"
    if is_reader(template_id, template_version):
        return "reader"
    return None


def has_facets(template_id, template_version):
    return template_version in FACET_VERSIONS.get(template_id, ())


def has_calculators(template_id, template_version):
    return has_facets(template_id, template_version)


MAX_REPORT_DATA_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = 12 * 1024 * 1024
MAX_OUTPUTS = 7
MAX_FORMAT_BYTES = 64 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 192 * 1024 * 1024
MAX_CONCURRENT_RENDERS = 2
RENDER_DEADLINE_SECONDS = 180.0
UPLOAD_DEADLINE_SECONDS = 10.0
TOOL_TIMEOUT_SECONDS = (
    RENDER_DEADLINE_SECONDS + (MAX_OUTPUTS + 1) * UPLOAD_DEADLINE_SECONDS + 10.0
)
DEFAULT_GRAPH_MAX_NODES = 500
DEFAULT_GRAPH_MAX_EDGES = 2000


def allow_insecure_object_storage():
    return (
        os.environ.get("ALLOW_INSECURE_OBJECT_STORAGE", "false").strip().casefold()
        == "true"
    )


def graph_limits():
    values = []
    for name, default in (
        ("REPORT_GRAPH_MAX_NODES", DEFAULT_GRAPH_MAX_NODES),
        ("REPORT_GRAPH_MAX_EDGES", DEFAULT_GRAPH_MAX_EDGES),
    ):
        try:
            value = int(os.environ.get(name, default))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("graph limits must be positive integers") from exc
        if value <= 0:
            raise RuntimeError("graph limits must be positive integers")
        values.append(value)
    return tuple(values)


def upload_allowed_origins():
    origins = set()
    for value in os.environ.get("REPORTING_UPLOAD_ALLOWED_ORIGINS", "").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError(
                "REPORTING_UPLOAD_ALLOWED_ORIGINS contains an invalid origin"
            ) from exc
        scheme_is_allowed = parsed.scheme == "https" or (
            parsed.scheme == "http" and allow_insecure_object_storage()
        )
        if (
            not scheme_is_allowed
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "REPORTING_UPLOAD_ALLOWED_ORIGINS contains an invalid origin"
            )
        default_port = 443 if parsed.scheme == "https" else 80
        origins.add(
            (
                parsed.scheme,
                parsed.hostname.rstrip(".").lower(),
                port or default_port,
            )
        )
    return frozenset(origins)
