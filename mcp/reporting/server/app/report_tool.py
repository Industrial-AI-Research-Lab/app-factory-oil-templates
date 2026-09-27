import asyncio
import hashlib
import json
import logging
from dataclasses import replace

from app.config import (
    FACET_ROLES,
    RENDER_DEADLINE_SECONDS,
    UPLOAD_DEADLINE_SECONDS,
)
from app.errors import FORMAT_ERROR_MESSAGES, ReportError
from app.models import (
    FormatResult,
    RenderReportRequest,
    RenderReportResponse,
    ResultSnapshot,
)
from app.render_workers import render_formats_in_workers
from app.rendering.render import normalize_input, render_metadata
from app.source_artifacts import resolve_source_report
from app.uploads import UploadError, put_bytes

logger = logging.getLogger(__name__)


def canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def manifest_bytes(response):
    return canonical_json_bytes(response.model_dump(mode="json", exclude_none=True))


def role_checksum(request, role):
    return next(
        (s.expected_sha256 for s in request.source_artifacts or [] if s.role == role),
        None,
    )


def facet_checksums(request):
    checksums = {role: role_checksum(request, role) for role in FACET_ROLES}
    return {role: sha for role, sha in checksums.items() if sha is not None} or None


def calculator_checksums(report):
    views = report.get("presentation", {}).get("calculators") or []
    return {view["calculator_id"]: view["sha256"] for view in views} or None


def snapshot_request(request):
    return RenderReportRequest.model_validate(request.model_dump(mode="json"))


async def render_report_impl(
    request,
    client,
    *,
    upload_deadline_seconds=UPLOAD_DEADLINE_SECONDS,
    render_deadline_seconds=RENDER_DEADLINE_SECONDS,
):
    loop = asyncio.get_running_loop()
    render_deadline = loop.time() + render_deadline_seconds
    try:
        async with asyncio.timeout(render_deadline_seconds):
            request = await asyncio.to_thread(snapshot_request, request)
            source_data = await resolve_source_report(request, client)
            report_data_bytes, report_data = await asyncio.to_thread(
                normalize_input,
                source_data,
                request.template_id,
            )
            if (
                request.expected_result_sha256 is not None
                and request.expected_result_sha256
                != hashlib.sha256(report_data_bytes).hexdigest()
            ):
                logger.warning(
                    "[REPORT] project_id=%s run_id=%s code=SNAPSHOT_CHANGED — snapshot checksum mismatch",
                    report_data["project_id"],
                    report_data["run_id"],
                )
                raise ReportError("SNAPSHOT_CHANGED", "validate", {})
            rendered = await asyncio.to_thread(
                render_metadata,
                report_data_bytes,
                report_data,
                request.template_id,
                request.template_version,
            )
    except TimeoutError:
        raise ReportError("RENDER_TIMEOUT", "render", {}) from None
    rendered = replace(
        rendered,
        formats=await render_formats_in_workers(
            report_data_bytes,
            request.template_id,
            request.template_version,
            [target.format for target in request.outputs],
            deadline_seconds=max(0, render_deadline - loop.time()),
        ),
    )
    formats = {
        target.format: FormatResult(
            status="failed",
            filename=target.filename,
            content_type=target.content_type,
            size_bytes=0,
            sha256=None,
            error={
                "code": "NOT_GENERATED",
                "stage": "render",
                "message": FORMAT_ERROR_MESSAGES["NOT_GENERATED"],
            },
        )
        for target in request.outputs
    }
    for target in request.outputs:
        item = rendered.formats[target.format]
        if item.status == "failed":
            formats[target.format] = FormatResult(
                status="failed",
                filename=target.filename,
                content_type=target.content_type,
                size_bytes=0,
                sha256=None,
                error=item.error,
            )
            logger.warning(
                "[REPORT] project_id=%s run_id=%s format=%s code=%s — upload skipped",
                request.report_data["project_id"],
                request.report_data["run_id"],
                target.format,
                item.error["code"],
            )
            continue

        rendered_fields = {
            "filename": target.filename,
            "content_type": target.content_type,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
        }
        formats[target.format] = FormatResult(
            status="generated",
            **rendered_fields,
        )
        try:
            await put_bytes(
                target.upload_url,
                item.payload,
                target.content_type,
                client,
                upload_deadline_seconds,
            )
        except UploadError as exc:
            formats[target.format] = FormatResult(
                status="failed",
                error={
                    "code": exc.code,
                    "stage": "upload",
                    "message": FORMAT_ERROR_MESSAGES["UPLOAD_FAILED"],
                },
                **rendered_fields,
            )
            logger.warning(
                "[REPORT] project_id=%s run_id=%s format=%s code=%s error_type=%s — upload failed",
                request.report_data["project_id"],
                request.report_data["run_id"],
                target.format,
                exc.code,
                exc.error_type,
            )
        else:
            formats[target.format] = FormatResult(
                status="ready",
                **rendered_fields,
            )
            logger.info(
                "[REPORT] project_id=%s run_id=%s format=%s size_bytes=%s — upload ready",
                request.report_data["project_id"],
                request.report_data["run_id"],
                target.format,
                item.size_bytes,
            )

    response = RenderReportResponse(
        project_id=request.report_data["project_id"],
        run_id=request.report_data["run_id"],
        result=ResultSnapshot(
            id=request.report_data["result_id"],
            version=request.report_data["result_version"],
            sha256=rendered.result_sha256,
        ),
        template=rendered.template,
        template_snapshot=rendered.template_snapshot,
        formats=formats,
        source_artifacts=[
            {"sha256": s.expected_sha256}
            for s in request.source_artifacts or []
            if s.role is None
        ],
        collection_sha256=request.expected_collection_sha256,
        ontology_sha256=role_checksum(request, "ontology"),
        fact_graph_sha256=role_checksum(request, "fact_graph"),
        facet_sha256s=facet_checksums(request),
        calculator_sha256s=calculator_checksums(report_data),
    )
    try:
        await put_bytes(
            request.manifest_upload_url,
            manifest_bytes(response),
            "application/json",
            client,
            upload_deadline_seconds,
        )
    except UploadError as exc:
        logger.warning(
            "[REPORT] project_id=%s run_id=%s code=%s error_type=%s — manifest upload failed",
            response.project_id,
            response.run_id,
            exc.code,
            exc.error_type,
        )
        raise ReportError(
            "MANIFEST_UPLOAD_FAILED",
            "upload",
            {"upload_code": exc.code},
        ) from None
    logger.info(
        "[REPORT] project_id=%s run_id=%s format_count=%s — manifest upload ready",
        response.project_id,
        response.run_id,
        len(response.formats),
    )
    return response
