import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from jinja2 import Environment, StrictUndefined

from .content import MISSING, display, metadata
from .formats import write_format
from .schema import ReportError, validate
from .state import export_lock, save_manifest

logger = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"
MIMES = {
    "txt": "text/plain",
    "json": "application/json",
    "csv": "text/csv",
    "html": "text/html",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def pending_format(fmt):
    return {
        "status": "failed",
        "filename": f"report.{fmt}",
        "mime_type": MIMES[fmt],
        "size_bytes": 0,
        "sha256": None,
        "error": {
            "code": "NOT_GENERATED",
            "stage": "render",
            "message": "Format generation has not completed",
        },
    }


def load_input(path, family):
    try:
        original = Path(path).read_bytes()
        data = validate(json.loads(original), family)
        json.dumps(data, allow_nan=False)
    except ReportError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ReportError("INVALID_INPUT", "validate", str(exc)) from exc
    return original, data


def load_template(family, version):
    if family not in ("news", "knowledge") or version not in ("1.0.0", "1.1.0"):
        raise ReportError(
            "UNKNOWN_TEMPLATE", "validate", {"id": family, "version": version}
        )
    try:
        style = (TEMPLATES / family / f"{version}.json").read_bytes()
        html = (TEMPLATES / "report.html.j2").read_bytes()
        json.loads(style)
    except (OSError, ValueError) as exc:
        raise ReportError("INVALID_TEMPLATE", "validate", str(exc)) from exc
    return style, html


def render_report(input_path, template_id, template_version, formats, output_dir):
    if (
        not isinstance(formats, (list, tuple))
        or not formats
        or any(not isinstance(fmt, str) or fmt not in MIMES for fmt in formats)
        or len(formats) != len(set(formats))
    ):
        raise ReportError("INVALID_FORMATS", "validate", formats)
    style, html = load_template(template_id, template_version)
    original, data = load_input(input_path, template_id)
    export_id = str(uuid4())
    root = Path(output_dir).resolve()
    folder = root / data["project_id"] / data["run_id"] / export_id
    if not folder.resolve().is_relative_to(root):
        raise ReportError(
            "INVALID_OUTPUT_PATH", "validate", "Export directory escapes output root"
        )
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "report-data.json").write_bytes(original)
    (folder / "template.json").write_bytes(style)
    (folder / "template.html.j2").write_bytes(html)
    manifest = {
        "export_id": export_id,
        "project_id": data["project_id"],
        "run_id": data["run_id"],
        "result": {
            "id": data["result_id"],
            "version": data["result_version"],
            "sha256": digest(original),
        },
        "template": {"id": template_id, "version": template_version},
        "snapshot": {"template.json": digest(style), "template.html.j2": digest(html)},
        "manifest_path": str(folder / "export-manifest.json"),
        "formats": {fmt: pending_format(fmt) for fmt in formats},
    }
    with export_lock(folder):
        save_manifest(manifest)
        generate(manifest, folder, data, original, style, html, formats)
    return manifest


def generate(manifest, folder, data, original, style_bytes, html_bytes, formats):
    family = manifest["template"]["id"]
    style = json.loads(style_bytes)
    cached_html = None

    def html():
        nonlocal cached_html
        if cached_html is None:
            environment = Environment(undefined=StrictUndefined, autoescape=True)
            environment.filters["display"] = display
            template = environment.from_string(html_bytes.decode("utf-8"))
            cached_html = template.render(
                data=data,
                style=style,
                family=family,
                metadata=metadata(data, family),
                missing=MISSING,
            )
        return cached_html

    for fmt in formats:
        path = folder / f"report.{fmt}"
        temporary = folder / f".report.{uuid4()}.{fmt}"
        item = pending_format(fmt)
        try:
            write_format(fmt, temporary, data, style, family, html, original)
            payload = temporary.read_bytes()
            if not payload:
                raise ValueError("Renderer produced an empty file")
            temporary.replace(path)
            item.update(
                status="generated", size_bytes=len(payload), sha256=digest(payload)
            )
            item.pop("error")
            logger.info(
                "[REPORT] export_id=%s format=%s — generated",
                manifest["export_id"],
                fmt,
            )
        except Exception as exc:
            item["error"] = {
                "code": "RENDER_FAILED",
                "stage": "render",
                "message": str(exc),
            }
            logger.warning(
                "[REPORT] export_id=%s format=%s — rendering failed: %s",
                manifest["export_id"],
                fmt,
                exc,
            )
        finally:
            temporary.unlink(missing_ok=True)
        manifest["formats"][fmt] = item
        save_manifest(manifest, folder / "export-manifest.json")


def retry_report(manifest_path):
    path = Path(manifest_path).resolve()
    folder = path.parent
    with export_lock(folder):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        family = manifest["template"]["id"]
        original, data = load_input(folder / "report-data.json", family)
        if digest(original) != manifest["result"]["sha256"]:
            raise ReportError("SNAPSHOT_CHANGED", "validate", "Source checksum differs")
        snapshots = {}
        for name in ("template.json", "template.html.j2"):
            snapshots[name] = (folder / name).read_bytes()
            if digest(snapshots[name]) != manifest["snapshot"][name]:
                raise ReportError("SNAPSHOT_CHANGED", "validate", name)
        failed = [
            fmt
            for fmt, item in manifest["formats"].items()
            if item["status"] == "failed"
            and item.get("error", {}).get("stage") == "render"
        ]
        if not failed:
            logger.warning(
                "[REPORT] export_id=%s — no failed render formats to retry",
                manifest["export_id"],
            )
        manifest["manifest_path"] = str(path)
        generate(
            manifest,
            folder,
            data,
            original,
            snapshots["template.json"],
            snapshots["template.html.j2"],
            failed,
        )
        return manifest
