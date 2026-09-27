import json
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from app.server import mcp


def test_production_image_disables_fastmcp_update_checks():
    dockerfile = (
        Path(__file__).resolve().parents[4]
        / "deploy"
        / "oil-mcp"
        / "reporting.Dockerfile"
    ).read_text()

    assert "FASTMCP_CHECK_FOR_UPDATES=off" in dockerfile


def test_tool_timeout_reserves_time_after_all_upload_deadlines():
    from app.config import (
        MAX_OUTPUTS,
        RENDER_DEADLINE_SECONDS,
        TOOL_TIMEOUT_SECONDS,
        UPLOAD_DEADLINE_SECONDS,
    )

    required = RENDER_DEADLINE_SECONDS + (MAX_OUTPUTS + 1) * UPLOAD_DEADLINE_SECONDS

    assert TOOL_TIMEOUT_SECONDS - required >= 10


@pytest.mark.asyncio
async def test_mcp_lists_only_render_report():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["render_report"]
    schema = tools[0].inputSchema
    assert set(schema["required"]) == {
        "report_data",
        "template_id",
        "template_version",
        "outputs",
        "manifest_upload_url",
    }
    assert schema["properties"]["expected_result_sha256"]["default"] is None


@pytest.mark.asyncio
async def test_mcp_discovery_describes_nested_request_and_response_contract():
    async with Client(mcp) as client:
        tool = (await client.list_tools())[0]

    outputs = tool.inputSchema["properties"]["outputs"]
    assert outputs["minItems"] == 1
    assert outputs["maxItems"] == 7
    assert set(outputs["items"]["required"]) == {
        "format",
        "filename",
        "content_type",
        "upload_url",
    }
    assert outputs["items"]["properties"]["format"]["enum"] == [
        "txt",
        "json",
        "csv",
        "html",
        "pdf",
        "docx",
        "xlsx",
    ]
    assert outputs["items"]["properties"]["filename"]["pattern"]
    assert outputs["items"]["properties"]["upload_url"]["pattern"] == "^https?://"
    assert tool.inputSchema["properties"]["manifest_upload_url"]["format"] == "uri"
    assert set(tool.outputSchema["required"]) == {
        "project_id",
        "run_id",
        "result",
        "template",
        "template_snapshot",
        "formats",
    }
    format_result = tool.outputSchema["properties"]["formats"]["additionalProperties"]
    assert set(format_result["required"]) == {
        "status",
        "filename",
        "content_type",
        "size_bytes",
        "sha256",
    }


@pytest.mark.asyncio
async def test_mcp_tool_returns_ready_manifest(monkeypatch):
    import httpx
    from app import server

    uploads = []

    def respond(request):
        uploads.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(respond)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(server.httpx, "AsyncClient", client_factory)
    report_data = json.loads(
        (Path(__file__).parent / "fixtures" / "small.json").read_text()
    )
    arguments = {
        "report_data": report_data,
        "template_id": "knowledge",
        "template_version": "1.0.0",
        "outputs": [
            {
                "format": "html",
                "filename": "Отчёт.html",
                "content_type": "text/html",
                "upload_url": "https://uploads.test/report?signature=report",
            }
        ],
        "manifest_upload_url": ("https://uploads.test/manifest?signature=manifest"),
    }

    async with Client(mcp) as client:
        result = await client.call_tool("render_report", arguments)

    assert not result.is_error
    assert result.structured_content["formats"]["html"]["status"] == "ready"
    assert [request.url.path for request in uploads] == ["/report", "/manifest"]


@pytest.mark.asyncio
async def test_mcp_error_does_not_expose_signed_urls():
    arguments = {
        "report_data": {},
        "template_id": "knowledge",
        "template_version": "1.0.0",
        "outputs": [
            {
                "format": "html",
                "filename": "report.html",
                "content_type": "text/html",
                "upload_url": "https://attacker.test/report?signature=secret-report",
            }
        ],
        "manifest_upload_url": (
            "https://uploads.test/manifest?signature=secret-manifest"
        ),
    }

    async with Client(mcp) as client:
        with pytest.raises(ToolError) as captured:
            await client.call_tool("render_report", arguments)

    assert str(captured.value) == "INVALID_INPUT:validate"
    assert "signature=" not in str(captured.value)


def test_server_rejects_invalid_graph_limits_before_listening(monkeypatch):
    from app import server

    monkeypatch.setenv("REPORT_GRAPH_MAX_NODES", "0")

    def server_must_not_start(*args, **kwargs):
        pytest.fail("MCP server must not start with invalid resource limits")

    monkeypatch.setattr(server.mcp, "run", server_must_not_start)

    with pytest.raises(RuntimeError, match="graph limits must be positive integers"):
        server.main()


def test_server_rejects_invalid_upload_origin_before_listening(monkeypatch):
    from app import server

    monkeypatch.setenv(
        "REPORTING_UPLOAD_ALLOWED_ORIGINS",
        "https://uploads.test/not-an-origin",
    )

    def server_must_not_start(*args, **kwargs):
        pytest.fail("MCP server must not start with invalid upload configuration")

    monkeypatch.setattr(server.mcp, "run", server_must_not_start)

    with pytest.raises(
        RuntimeError,
        match="REPORTING_UPLOAD_ALLOWED_ORIGINS contains an invalid origin",
    ):
        server.main()


def test_server_enables_application_info_logs():
    import logging

    from app import server

    root = logging.getLogger()
    original_level = root.level
    noisy_loggers = [logging.getLogger("httpx"), logging.getLogger("httpcore")]
    original_noisy_levels = [item.level for item in noisy_loggers]
    root.setLevel(logging.WARNING)
    try:
        configure_logging = getattr(server, "configure_logging", lambda: None)
        configure_logging()

        assert logging.getLogger("app.report_tool").getEffectiveLevel() == logging.INFO
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    finally:
        root.setLevel(original_level)
        for item, level in zip(noisy_loggers, original_noisy_levels):
            item.setLevel(level)
