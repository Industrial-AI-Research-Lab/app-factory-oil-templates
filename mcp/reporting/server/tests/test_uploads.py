import logging
import traceback

import httpx
import pytest

from app.uploads import HttpxQueryRedactionFilter, UploadError, put_bytes


@pytest.mark.asyncio
async def test_http_failure_uses_stable_code_without_signed_url():
    signed_url = "https://uploads.test/report?signature=top-secret"

    def respond(request):
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UploadError) as captured:
            await put_bytes(signed_url, b"report", "text/plain", client)

    rendered_error = "".join(
        traceback.format_exception(
            type(captured.value), captured.value, captured.value.__traceback__
        )
    )
    assert captured.value.code == "UPLOAD_FAILED"
    assert signed_url not in rendered_error
    assert "top-secret" not in rendered_error


@pytest.mark.asyncio
async def test_read_timeout_marks_upload_outcome_unknown():
    def respond(request):
        raise httpx.ReadTimeout("response lost", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UploadError) as captured:
            await put_bytes(
                "https://uploads.test/report?signature=top-secret",
                b"report",
                "text/plain",
                client,
            )

    assert captured.value.code == "UPLOAD_OUTCOME_UNKNOWN"


@pytest.mark.asyncio
async def test_write_timeout_marks_upload_outcome_unknown():
    def respond(request):
        raise httpx.WriteTimeout("request body interrupted", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UploadError) as captured:
            await put_bytes(
                "https://uploads.test/report?signature=top-secret",
                b"report",
                "text/plain",
                client,
            )

    assert captured.value.code == "UPLOAD_OUTCOME_UNKNOWN"


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ReadError,
        httpx.WriteError,
        httpx.CloseError,
        httpx.RemoteProtocolError,
    ],
)
@pytest.mark.asyncio
async def test_response_transport_failure_marks_upload_outcome_unknown(error_type):
    def respond(request):
        raise error_type("response lost", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UploadError) as captured:
            await put_bytes(
                "https://uploads.test/report?signature=top-secret",
                b"report",
                "text/plain",
                client,
            )

    assert captured.value.code == "UPLOAD_OUTCOME_UNKNOWN"


@pytest.mark.asyncio
async def test_connect_failure_marks_upload_failed():
    def respond(request):
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UploadError) as captured:
            await put_bytes(
                "https://uploads.test/report?signature=top-secret",
                b"report",
                "text/plain",
                client,
            )

    assert captured.value.code == "UPLOAD_FAILED"


@pytest.mark.asyncio
async def test_redirect_never_moves_put_away_from_signed_target():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.host == "uploads.test":
            return httpx.Response(
                307,
                headers={"Location": "https://redirected.test/capture"},
            )
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        with pytest.raises(UploadError) as captured:
            await put_bytes(
                "https://uploads.test/report?signature=top-secret",
                b"report",
                "text/plain",
                client,
            )

    assert captured.value.code == "UPLOAD_FAILED"
    assert [request.url.host for request in requests] == ["uploads.test"]


def test_httpx_log_filter_removes_signed_query():
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "HTTP Request: PUT %s",
        (httpx.URL("https://uploads.test/report?signature=top-secret"),),
        None,
    )

    assert HttpxQueryRedactionFilter().filter(record)
    assert record.getMessage() == "HTTP Request: PUT https://uploads.test/report"
