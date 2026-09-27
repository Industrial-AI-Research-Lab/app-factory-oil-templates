import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from jinja2 import Environment, StrictUndefined

from app.config import (
    MAX_FORMAT_BYTES,
    MAX_REPORT_DATA_BYTES,
    MAX_TOTAL_OUTPUT_BYTES,
    TEMPLATE_IDS,
    TEMPLATE_VERSIONS,
    reader_variant,
)
from app.errors import FORMAT_ERROR_MESSAGES, ReportError

from .content import MISSING, display, metadata
from .formats import write_format
from .graph import graph_view
from .news_dates import add_date_table
from .reader_render import reader_files, reader_html
from .schema import validate

logger = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"
CONTENT_TYPES = {
    "txt": "text/plain",
    "json": "application/json",
    "csv": "text/csv",
    "html": "text/html",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@dataclass(frozen=True)
class RenderedFormat:
    status: Literal["generated", "failed"]
    filename: str
    content_type: str
    payload: bytes | None
    size_bytes: int
    sha256: str | None
    error: dict[str, str] | None


@dataclass(frozen=True)
class RenderedReport:
    result_sha256: str
    template: dict[str, str]
    template_snapshot: dict[str, str]
    formats: dict[str, RenderedFormat]


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def validate_template_selection(template_id, template_version):
    if (
        template_id not in TEMPLATE_IDS
        or template_version not in TEMPLATE_VERSIONS[template_id]
    ):
        raise ReportError(
            "UNKNOWN_TEMPLATE",
            "validate",
            {"id": template_id, "version": template_version},
        )


def template_files(template_id, template_version):
    if reader_variant(template_id, template_version) is not None:
        return reader_files(template_id, template_version)
    legacy = (
        "report-graph.html.j2"
        if template_id == "knowledge" and template_version == "1.2.0"
        else "report.html.j2"
    )
    return {"template.html.j2": legacy}


def load_template(template_id, template_version):
    validate_template_selection(template_id, template_version)
    try:
        style = (TEMPLATES / template_id / f"{template_version}.json").read_bytes()
        files = {
            name: (TEMPLATES / path).read_bytes()
            for name, path in template_files(template_id, template_version).items()
        }
        json.loads(style)
    except (OSError, ValueError) as exc:
        raise ReportError("INVALID_TEMPLATE", "validate", str(exc)) from exc
    return style, files


def template_snapshot(style_bytes, files):
    return {
        "template.json": digest(style_bytes),
        **{name: digest(payload) for name, payload in files.items()},
    }


def normalize_input(report_data, template_id):
    try:
        original = json.dumps(
            report_data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(original) > MAX_REPORT_DATA_BYTES:
            raise ReportError(
                "INPUT_TOO_LARGE",
                "validate",
                {
                    "max_bytes": MAX_REPORT_DATA_BYTES,
                    "actual_bytes": len(original),
                },
            )
        data = validate(json.loads(original), template_id)
        if template_id == "news":
            add_date_table(data)
    except ReportError:
        raise
    except (ValueError, TypeError) as exc:
        raise ReportError("INVALID_INPUT", "validate", str(exc)) from exc
    return original, data


def validate_formats(formats):
    if (
        not isinstance(formats, (list, tuple))
        or not formats
        or any(not isinstance(fmt, str) or fmt not in CONTENT_TYPES for fmt in formats)
        or len(formats) != len(set(formats))
    ):
        raise ReportError("INVALID_FORMATS", "validate", formats)


def render_bytes(report_data, template_id, template_version, formats):
    validate_formats(formats)
    validate_template_selection(template_id, template_version)
    original, data = normalize_input(report_data, template_id)
    return _render_prepared_bytes(
        original, data, template_id, template_version, formats
    )


def render_prepared_bytes(original, data, template_id, template_version, formats):
    validate_formats(formats)
    return _render_prepared_bytes(
        original, data, template_id, template_version, formats
    )


def render_metadata(original, data, template_id, template_version):
    style_bytes, files = load_template(template_id, template_version)
    validate_graph_template(data, template_version)
    return RenderedReport(
        result_sha256=digest(original),
        template={"id": template_id, "version": template_version},
        template_snapshot=template_snapshot(style_bytes, files),
        formats={},
    )


def validate_graph_template(data, template_version):
    graph = data.get("graph")
    if graph and (graph["nodes"] or graph["edges"]) and template_version != "1.2.0":
        raise ReportError("GRAPH_TEMPLATE_UNSUPPORTED", "validate", {})


def legacy_html(files, data, style, template_id):
    environment = Environment(undefined=StrictUndefined, autoescape=True)
    environment.filters["display"] = display
    template = environment.from_string(files["template.html.j2"].decode("utf-8"))
    return template.render(
        data=data,
        style=style,
        family=template_id,
        metadata=metadata(data, template_id),
        graph=(graph_view(data.get("graph")) if template_id == "knowledge" else None),
        missing=MISSING,
    )


def _render_prepared_bytes(original, data, template_id, template_version, formats):
    style_bytes, files = load_template(template_id, template_version)
    validate_graph_template(data, template_version)
    style = json.loads(style_bytes)
    variant = reader_variant(template_id, template_version)
    reader = variant is not None
    cached = {}

    def html(medium="screen"):
        key = medium if reader else "screen"
        if key not in cached:
            cached[key] = (
                reader_html(files, data, style, template_id, medium, variant)
                if reader
                else legacy_html(files, data, style, template_id)
            )
        return cached[key]

    rendered = {}
    total_size = 0
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for fmt in formats:
            path = root / f"report.{fmt}"
            try:
                write_format(
                    fmt, path, data, style, template_id, html, original, reader
                )
                size = path.stat().st_size
                if (
                    size > MAX_FORMAT_BYTES
                    or total_size + size > MAX_TOTAL_OUTPUT_BYTES
                ):
                    raise ReportError(
                        "OUTPUT_TOO_LARGE",
                        "render",
                        {
                            "format_bytes": size,
                            "max_format_bytes": MAX_FORMAT_BYTES,
                            "max_total_output_bytes": MAX_TOTAL_OUTPUT_BYTES,
                        },
                    )
                payload = path.read_bytes()
                if not payload:
                    raise ValueError("Renderer produced an empty file")
                total_size += size
                rendered[fmt] = RenderedFormat(
                    status="generated",
                    filename=f"report.{fmt}",
                    content_type=CONTENT_TYPES[fmt],
                    payload=payload,
                    size_bytes=len(payload),
                    sha256=digest(payload),
                    error=None,
                )
                logger.info(
                    "[REPORT] template_id=%s template_version=%s format=%s — generated",
                    template_id,
                    template_version,
                    fmt,
                )
            except Exception as exc:
                error_code = (
                    exc.code
                    if isinstance(exc, ReportError) and exc.stage == "render"
                    else "RENDER_FAILED"
                )
                rendered[fmt] = RenderedFormat(
                    status="failed",
                    filename=f"report.{fmt}",
                    content_type=CONTENT_TYPES[fmt],
                    payload=None,
                    size_bytes=0,
                    sha256=None,
                    error={
                        "code": error_code,
                        "stage": "render",
                        "message": FORMAT_ERROR_MESSAGES[
                            (
                                "OUTPUT_TOO_LARGE"
                                if error_code == "OUTPUT_TOO_LARGE"
                                else "RENDER_FAILED"
                            )
                        ],
                    },
                )
                logger.warning(
                    "[REPORT] template_id=%s template_version=%s format=%s error_type=%s — rendering failed",
                    template_id,
                    template_version,
                    fmt,
                    type(exc).__name__,
                )

    return RenderedReport(
        result_sha256=digest(original),
        template={"id": template_id, "version": template_version},
        template_snapshot=template_snapshot(style_bytes, files),
        formats=rendered,
    )
