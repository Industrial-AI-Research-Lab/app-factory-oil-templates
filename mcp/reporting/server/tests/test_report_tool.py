import asyncio
import hashlib
import json
import traceback
from pathlib import Path

import httpx
import pytest

from app.config import MAX_REPORT_DATA_BYTES
from app.errors import ReportError
from app.models import RenderReportRequest
from app.report_tool import (
    canonical_json_bytes,
    render_report_impl,
)


def fixture(name="small.json"):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def request_fixture():
    return RenderReportRequest.model_validate(
        {
            "report_data": fixture(),
            "template_id": "knowledge",
            "template_version": "1.0.0",
            "outputs": [
                {
                    "format": "pdf",
                    "filename": "Отчёт.pdf",
                    "content_type": "application/pdf",
                    "upload_url": "https://uploads.test/pdf?signature=secret-pdf",
                }
            ],
            "manifest_upload_url": (
                "https://uploads.test/manifest?signature=secret-manifest"
            ),
        }
    )


@pytest.mark.asyncio
async def test_rendering_yields_event_loop_while_renderer_runs():
    def respond(request):
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        render_task = asyncio.create_task(render_report_impl(request_fixture(), client))
        await asyncio.sleep(0)
        yielded_before_completion = not render_task.done()
        response = await render_task

    assert yielded_before_completion
    assert response.formats["pdf"].status == "ready"


@pytest.mark.asyncio
async def test_snapshot_validation_yields_event_loop():
    data = request_fixture().model_dump(mode="json")
    data["report_data"] = {"padding": "x" * (8 * 1024 * 1024)}
    request = RenderReportRequest.model_validate(data)

    def respond(request):
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        render_task = asyncio.create_task(render_report_impl(request, client))
        await asyncio.sleep(0)
        yielded_before_completion = not render_task.done()
        with pytest.raises(ReportError) as captured:
            await render_task

    assert yielded_before_completion
    assert captured.value.code == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_render_report_uploads_exact_bytes_and_ready_manifest():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await render_report_impl(request_fixture(), client)

    assert [request.url.path for request in requests] == ["/pdf", "/manifest"]
    pdf = requests[0].content
    manifest = json.loads(requests[1].content)
    assert response.formats["pdf"].status == "ready"
    assert manifest["formats"]["pdf"]["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert manifest["formats"]["pdf"]["size_bytes"] == len(pdf)
    assert manifest["formats"]["pdf"]["filename"] == "Отчёт.pdf"
    assert requests[0].headers["Content-Type"] == "application/pdf"
    assert requests[1].headers["Content-Type"] == "application/json"
    assert requests[1].content == json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert "upload_url" not in json.dumps(manifest)
    assert "secret-" not in requests[1].content.decode()


@pytest.mark.asyncio
async def test_upload_failure_stays_failed_and_manifest_is_last():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503 if request.url.path == "/pdf" else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await render_report_impl(request_fixture(), client)

    manifest = json.loads(requests[-1].content)
    assert [request.url.path for request in requests] == ["/pdf", "/manifest"]
    assert response.formats["pdf"].status == "failed"
    assert response.formats["pdf"].error.model_dump() == {
        "code": "UPLOAD_FAILED",
        "stage": "upload",
        "message": "Не удалось загрузить файл",
    }
    assert manifest["formats"]["pdf"]["status"] == "failed"
    assert manifest["formats"]["pdf"]["error"]["code"] == "UPLOAD_FAILED"


@pytest.mark.asyncio
async def test_read_timeout_never_marks_format_ready():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/pdf":
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await render_report_impl(request_fixture(), client)

    manifest = json.loads(requests[-1].content)
    assert [request.url.path for request in requests] == ["/pdf", "/manifest"]
    assert response.formats["pdf"].status == "failed"
    assert response.formats["pdf"].error.code == "UPLOAD_OUTCOME_UNKNOWN"
    assert manifest["formats"]["pdf"]["status"] == "failed"


@pytest.mark.asyncio
async def test_render_failure_does_not_block_successful_format_upload():
    data = request_fixture().model_dump(mode="json")
    data["report_data"]["answer"] = "x" * 32768
    data["outputs"] = [
        {
            "format": "txt",
            "filename": "report.txt",
            "content_type": "text/plain",
            "upload_url": "https://uploads.test/txt?signature=secret-txt",
        },
        {
            "format": "xlsx",
            "filename": "report.xlsx",
            "content_type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            "upload_url": "https://uploads.test/xlsx?signature=secret-xlsx",
        },
    ]
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await render_report_impl(
            RenderReportRequest.model_validate(data), client
        )

    manifest = json.loads(requests[-1].content)
    assert [request.url.path for request in requests] == ["/txt", "/manifest"]
    assert response.formats["txt"].status == "ready"
    assert response.formats["xlsx"].status == "failed"
    assert response.formats["xlsx"].error.code == "RENDER_FAILED"
    assert manifest["formats"]["xlsx"]["status"] == "failed"


@pytest.mark.asyncio
async def test_manifest_failure_is_reported_after_successful_format_upload():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503 if request.url.path == "/manifest" else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ReportError) as captured:
            await render_report_impl(request_fixture(), client)

    rendered_error = "".join(
        traceback.format_exception(
            type(captured.value), captured.value, captured.value.__traceback__
        )
    )
    assert [request.url.path for request in requests] == ["/pdf", "/manifest"]
    assert captured.value.code == "MANIFEST_UPLOAD_FAILED"
    assert captured.value.stage == "upload"
    assert "secret-manifest" not in rendered_error


@pytest.mark.asyncio
async def test_oversized_report_is_rejected_before_render_or_upload():
    data = request_fixture().model_dump(mode="json")
    data["report_data"]["answer"] = ""
    base_size = len(canonical_json_bytes(data["report_data"]))
    data["report_data"]["answer"] = "x" * (MAX_REPORT_DATA_BYTES - base_size)
    assert len(canonical_json_bytes(data["report_data"])) == MAX_REPORT_DATA_BYTES
    data["report_data"]["answer"] += "x"
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ReportError) as captured:
            await render_report_impl(RenderReportRequest.model_validate(data), client)

    assert captured.value.code == "INPUT_TOO_LARGE"
    assert captured.value.stage == "validate"
    assert requests == []


@pytest.mark.asyncio
async def test_request_mutation_during_upload_cannot_change_later_targets():
    data = request_fixture().model_dump(mode="json")
    data["outputs"] = [
        {
            "format": "txt",
            "filename": "report.txt",
            "content_type": "text/plain",
            "upload_url": "https://uploads.test/txt?signature=secret-txt",
        },
        {
            "format": "json",
            "filename": "report.json",
            "content_type": "application/json",
            "upload_url": "https://uploads.test/json?signature=secret-json",
        },
    ]
    request = RenderReportRequest.model_validate(data)
    requests = []

    def respond(http_request):
        requests.append(http_request)
        if http_request.url.path == "/txt":
            request.outputs[1].upload_url = request.outputs[0].upload_url
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await render_report_impl(request, client)

    assert [item.url.path for item in requests] == ["/txt", "/json", "/manifest"]


@pytest.mark.asyncio
async def test_slow_outputs_cannot_consume_manifest_upload_budget():
    data = request_fixture().model_dump(mode="json")
    content_types = {
        "txt": "text/plain",
        "json": "application/json",
        "csv": "text/csv",
        "html": "text/html",
        "pdf": "application/pdf",
        "docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    data["outputs"] = [
        {
            "format": fmt,
            "filename": f"report.{fmt}",
            "content_type": content_type,
            "upload_url": f"https://uploads.test/{fmt}?signature={fmt}",
        }
        for fmt, content_type in content_types.items()
    ]
    attempts = []

    async def respond(request):
        attempts.append(request)
        if request.url.path != "/manifest":
            await asyncio.sleep(0.05)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await render_report_impl(
            RenderReportRequest.model_validate(data),
            client,
            upload_deadline_seconds=0.001,
        )

    assert [request.url.path for request in attempts] == [
        "/txt",
        "/json",
        "/csv",
        "/html",
        "/pdf",
        "/docx",
        "/xlsx",
        "/manifest",
    ]
    assert all(item.status == "failed" for item in response.formats.values())
    manifest = json.loads(attempts[-1].content)
    assert all(item["status"] == "failed" for item in manifest["formats"].values())
