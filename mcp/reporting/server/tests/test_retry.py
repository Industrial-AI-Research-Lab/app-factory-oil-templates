import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app import render_workers
from app.errors import ReportError
from app.models import RenderReportRequest
from app.report_tool import canonical_json_bytes, render_report_impl


def request_data():
    report_data = json.loads(
        (Path(__file__).parent / "fixtures" / "small.json").read_text()
    )
    return {
        "report_data": report_data,
        "template_id": "knowledge",
        "template_version": "1.0.0",
        "outputs": [
            {
                "format": "pdf",
                "filename": "report.pdf",
                "content_type": "application/pdf",
                "upload_url": "https://uploads.test/pdf?signature=pdf",
            }
        ],
        "manifest_upload_url": "https://uploads.test/manifest?signature=manifest",
    }


def test_request_accepts_expected_result_checksum():
    request = RenderReportRequest.model_validate(
        {**request_data(), "expected_result_sha256": "a" * 64}
    )

    assert request.expected_result_sha256 == "a" * 64


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({}, id="omitted"),
        pytest.param({"expected_result_sha256": None}, id="explicit_none"),
    ],
)
def test_request_accepts_missing_or_none_expected_result_checksum(overrides):
    request = RenderReportRequest.model_validate({**request_data(), **overrides})

    assert request.expected_result_sha256 is None


@pytest.mark.parametrize(
    "checksum",
    [
        pytest.param("", id="empty"),
        pytest.param("A" * 64, id="uppercase"),
        pytest.param("a" * 63, id="wrong_length"),
        pytest.param(b"a" * 64, id="coercible_bytes"),
    ],
)
def test_request_rejects_malformed_expected_result_checksum(checksum):
    with pytest.raises(ValidationError):
        RenderReportRequest.model_validate(
            {**request_data(), "expected_result_sha256": checksum}
        )


@pytest.mark.asyncio
async def test_invalid_retry_input_is_rejected_before_snapshot_comparison():
    data = request_data()
    data["report_data"] = {}
    data["expected_result_sha256"] = "0" * 64
    uploads = []

    def respond(request):
        uploads.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ReportError) as captured:
            await render_report_impl(RenderReportRequest.model_validate(data), client)

    assert captured.value.code == "INVALID_INPUT"
    assert captured.value.stage == "validate"
    assert uploads == []


@pytest.mark.asyncio
async def test_invalid_retry_body_is_rejected_before_snapshot_comparison():
    data = request_data()
    data["report_data"]["tables"] = None
    data["expected_result_sha256"] = "0" * 64
    uploads = []

    def respond(request):
        uploads.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ReportError) as captured:
            await render_report_impl(RenderReportRequest.model_validate(data), client)

    assert captured.value.code == "INVALID_INPUT"
    assert captured.value.stage == "validate"
    assert uploads == []


@pytest.mark.asyncio
async def test_changed_retry_input_is_rejected_before_render_or_upload(monkeypatch):
    data = request_data()
    data["expected_result_sha256"] = hashlib.sha256(
        canonical_json_bytes(data["report_data"])
    ).hexdigest()
    data["report_data"]["answer"] = "Changed after the original run"
    uploads = []

    async def renderer_must_not_run(*args, **kwargs):
        pytest.fail("renderer process must not start for changed retry input")

    monkeypatch.setattr(
        render_workers.asyncio,
        "create_subprocess_exec",
        renderer_must_not_run,
    )

    def respond(request):
        uploads.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ReportError) as captured:
            await render_report_impl(RenderReportRequest.model_validate(data), client)

    assert captured.value.code == "SNAPSHOT_CHANGED"
    assert captured.value.stage == "validate"
    assert uploads == []


@pytest.mark.asyncio
async def test_identical_retry_publishes_only_requested_format_with_fresh_urls():
    original_data = request_data()
    original_data["outputs"] = [
        {
            "format": "pdf",
            "filename": "original.pdf",
            "content_type": "application/pdf",
            "upload_url": "https://uploads.test/original-pdf?signature=pdf",
        },
        {
            "format": "json",
            "filename": "original.json",
            "content_type": "application/json",
            "upload_url": "https://uploads.test/original-json?signature=json",
        },
    ]
    original_data["manifest_upload_url"] = (
        "https://uploads.test/original-manifest?signature=manifest"
    )
    original_uploads = []

    def original_respond(request):
        original_uploads.append(request)
        return httpx.Response(503 if request.url.path == "/original-pdf" else 200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(original_respond)
    ) as client:
        original = await render_report_impl(
            RenderReportRequest.model_validate(original_data), client
        )

    original_manifest = json.loads(original_uploads[-1].content)
    assert original.formats["pdf"].status == "failed"
    assert original.formats["pdf"].error.code == "UPLOAD_FAILED"
    assert original_manifest["formats"]["pdf"]["status"] == "failed"
    assert original_manifest["formats"]["pdf"]["error"]["code"] == "UPLOAD_FAILED"
    assert original.formats["json"].status == "ready"
    assert original_manifest["formats"]["json"]["status"] == "ready"

    retry_data = request_data()
    retry_data["report_data"] = dict(reversed(list(retry_data["report_data"].items())))
    retry_data["expected_result_sha256"] = original.result.sha256
    retry_data["outputs"][0]["filename"] = "retry.pdf"
    retry_data["outputs"][0][
        "upload_url"
    ] = "https://uploads.test/retry-pdf?signature=retry-pdf"
    retry_data["manifest_upload_url"] = (
        "https://uploads.test/retry-manifest?signature=retry-manifest"
    )
    retry_uploads = []

    def retry_respond(request):
        retry_uploads.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(retry_respond)
    ) as client:
        retry = await render_report_impl(
            RenderReportRequest.model_validate(retry_data), client
        )

    original_pdf = next(
        request.content
        for request in original_uploads
        if request.url.path == "/original-pdf"
    )
    retry_manifest = json.loads(retry_uploads[1].content)
    assert [request.url.path for request in retry_uploads] == [
        "/retry-pdf",
        "/retry-manifest",
    ]
    assert retry_uploads[0].content == original_pdf
    assert retry.result.sha256 == original.result.sha256
    assert retry_manifest["result"]["sha256"] == original.result.sha256
    assert retry_manifest["result"] == original_manifest["result"]
    assert retry_manifest["template"] == original.template.model_dump(mode="json")
    assert retry_manifest["template"] == original_manifest["template"]
    assert retry_manifest["template_snapshot"] == original.template_snapshot
    assert retry_manifest["template_snapshot"] == original_manifest["template_snapshot"]
    assert set(retry_manifest["formats"]) == {"pdf"}
    assert retry_manifest["formats"]["pdf"]["filename"] == "retry.pdf"
    assert (
        retry_manifest["formats"]["pdf"]["sha256"]
        == hashlib.sha256(original_pdf).hexdigest()
    )
    assert retry_manifest["formats"]["pdf"]["size_bytes"] == len(original_pdf)


@pytest.mark.asyncio
async def test_template_switch_keeps_result_checksum_and_records_new_snapshot():
    original_data = request_data()
    original_data["outputs"][0] = {
        "format": "html",
        "filename": "original.html",
        "content_type": "text/html",
        "upload_url": "https://uploads.test/original-html?signature=html",
    }
    original_data["manifest_upload_url"] = (
        "https://uploads.test/original-manifest?signature=manifest"
    )

    async def render(data):
        uploads = []

        def respond(request):
            uploads.append(request)
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            response = await render_report_impl(
                RenderReportRequest.model_validate(data), client
            )
        return response, uploads

    original, _ = await render(original_data)
    switched_data = request_data()
    switched_data["template_version"] = "1.1.0"
    switched_data["expected_result_sha256"] = original.result.sha256
    switched_data["outputs"][0] = {
        "format": "html",
        "filename": "switched.html",
        "content_type": "text/html",
        "upload_url": "https://uploads.test/switched-html?signature=html",
    }
    switched_data["manifest_upload_url"] = (
        "https://uploads.test/switched-manifest?signature=manifest"
    )
    switched, uploads = await render(switched_data)

    manifest = json.loads(uploads[-1].content)
    assert switched.result.sha256 == original.result.sha256
    assert switched.template.version == "1.1.0"
    assert switched.template_snapshot != original.template_snapshot
    assert manifest["result"]["sha256"] == original.result.sha256
    assert manifest["template"]["version"] == "1.1.0"
    assert manifest["template_snapshot"] == switched.template_snapshot


@pytest.mark.asyncio
async def test_concurrent_retries_publish_to_their_own_targets():
    checksum = hashlib.sha256(
        canonical_json_bytes(request_data()["report_data"])
    ).hexdigest()
    requests_by_target = []

    def retry_data(target):
        data = request_data()
        data["expected_result_sha256"] = checksum
        data["outputs"][0][
            "upload_url"
        ] = f"https://uploads.test/{target}-pdf?signature={target}-pdf"
        data["manifest_upload_url"] = (
            f"https://uploads.test/{target}-manifest?signature={target}-manifest"
        )
        return RenderReportRequest.model_validate(data)

    async def respond(request):
        requests_by_target.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        responses = await asyncio.gather(
            render_report_impl(retry_data("first"), client),
            render_report_impl(retry_data("second"), client),
        )

    uploads = {request.url.path: request.content for request in requests_by_target}
    first_manifest = json.loads(uploads["/first-manifest"])
    second_manifest = json.loads(uploads["/second-manifest"])
    assert set(uploads) == {
        "/first-pdf",
        "/first-manifest",
        "/second-pdf",
        "/second-manifest",
    }
    assert uploads["/first-pdf"] == uploads["/second-pdf"]
    assert [response.result.sha256 for response in responses] == [checksum, checksum]
    assert set(first_manifest["formats"]) == {"pdf"}
    assert set(second_manifest["formats"]) == {"pdf"}
