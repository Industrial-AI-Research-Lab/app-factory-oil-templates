import asyncio
import hashlib
import json

import httpx
import pytest


def export(tmp_path):
    original = b"source"
    (tmp_path / "report-data.json").write_bytes(original)
    (tmp_path / "report.txt").write_bytes(original)
    data = {
        "export_id": "export-one",
        "project_id": "p1",
        "run_id": "run1",
        "result": {
            "id": "r",
            "version": "1",
            "sha256": hashlib.sha256(original).hexdigest(),
        },
        "formats": {
            "txt": {
                "filename": "report.txt",
                "mime_type": "text/plain",
                "size_bytes": len(original),
                "sha256": hashlib.sha256(original).hexdigest(),
                "status": "generated",
            }
        },
    }
    path = tmp_path / "export-manifest.json"
    path.write_text(json.dumps(data))
    return path


def execute(path, respond):
    from reporting.publish import publish_report

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="https://AppFactory.test"
        ) as client:
            return await publish_report(path, client)

    return asyncio.run(run())


def test_corrupt_download_does_not_become_ready(tmp_path):
    path = export(tmp_path)

    def respond(request):
        if request.url.path.endswith("/attachments"):
            return httpx.Response(
                200,
                json={
                    "attachments": [{"id": "a1", "filename": "export-one-report.txt"}],
                    "truncated": False,
                },
            )
        return httpx.Response(
            200, content=b"wrong", headers={"Content-Type": "text/plain"}
        )

    manifest = execute(path, respond)
    assert manifest["formats"]["txt"]["status"] == "failed"
    assert manifest["formats"]["txt"]["error"]["code"] == "DOWNLOAD_CHECKSUM_MISMATCH"


def test_lost_upload_response_is_recovered_from_project_attachments(tmp_path):
    path = export(tmp_path)
    state = {"rows": [], "uploads": 0}

    def respond(request):
        if request.method == "POST":
            state["uploads"] += 1
            state["rows"] = [{"id": "a1", "filename": "export-one-report.txt"}]
            raise httpx.ReadTimeout("response lost", request=request)
        if request.url.path.endswith("/attachments"):
            return httpx.Response(
                200, json={"attachments": state["rows"], "truncated": False}
            )
        return httpx.Response(
            200, content=b"source", headers={"Content-Type": "text/plain"}
        )

    assert execute(path, respond)["formats"]["txt"]["status"] == "failed"
    assert execute(path, respond)["formats"]["txt"]["status"] == "ready"
    assert state["uploads"] == 1


def test_changed_original_is_rejected_before_network(tmp_path):
    from reporting.publish import PublicationError

    path = export(tmp_path)
    (tmp_path / "report-data.json").write_bytes(b"changed")

    def respond(request):
        raise AssertionError("No request allowed for changed original")

    with pytest.raises(PublicationError, match="SOURCE_CHECKSUM_MISMATCH"):
        execute(path, respond)
    assert json.loads(path.read_text())["formats"]["txt"]["status"] == "generated"


def test_successful_second_upload_is_verified_in_the_same_retry(tmp_path):
    path = export(tmp_path)
    state = {"rows": [], "uploads": 0}

    def respond(request):
        if request.method == "POST":
            state["uploads"] += 1
            if state["uploads"] == 1:
                return httpx.Response(503)
            state["rows"] = [{"id": "a1", "filename": "export-one-report.txt"}]
            return httpx.Response(200, json={})
        if request.url.path.endswith("/attachments"):
            return httpx.Response(
                200, json={"attachments": state["rows"], "truncated": False}
            )
        return httpx.Response(
            200, content=b"source", headers={"Content-Type": "text/plain"}
        )

    first = execute(path, respond)
    assert first["formats"]["txt"]["error"]["code"] == "UPLOAD_OUTCOME_UNKNOWN"
    second = execute(path, respond)
    assert second["formats"]["txt"]["status"] == "ready"
    assert second["formats"]["txt"]["attachment_id"] == "a1"
    assert "error" not in second["formats"]["txt"]
    assert state["uploads"] == 2


def test_export_lock_prevents_concurrent_publication(tmp_path):
    from reporting.state import export_lock
    from reporting.schema import ReportError

    path = export(tmp_path)

    def respond(request):
        raise AssertionError("No request allowed while another writer holds the export")

    with export_lock(tmp_path):
        with pytest.raises(ReportError):
            execute(path, respond)
    assert json.loads(path.read_text())["formats"]["txt"]["status"] == "generated"


def test_failed_upload_does_not_block_existing_format_verification(tmp_path):
    path = export(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["formats"]["csv"] = {
        **manifest["formats"]["txt"],
        "filename": "report.csv",
        "mime_type": "text/csv",
    }
    (tmp_path / "report.csv").write_bytes(b"source")
    path.write_text(json.dumps(manifest))

    def respond(request):
        if request.method == "POST":
            return httpx.Response(503)
        if request.url.path.endswith("/attachments"):
            return httpx.Response(
                200,
                json={
                    "attachments": [{"id": "a1", "filename": "export-one-report.txt"}],
                    "truncated": False,
                },
            )
        return httpx.Response(
            200, content=b"source", headers={"Content-Type": "text/plain"}
        )

    result = execute(path, respond)
    assert result["formats"]["txt"]["status"] == "ready"
    assert result["formats"]["csv"]["status"] == "failed"
    assert result["formats"]["csv"]["error"]["code"] == "UPLOAD_OUTCOME_UNKNOWN"


def test_chat_summary_uses_the_answer_and_authenticated_ui_route(tmp_path):
    from reporting.publish import chat_summary

    manifest = json.loads(export(tmp_path).read_text())
    manifest["formats"]["txt"].update(
        status="ready", download_path="/api/projects/p1/attachments/a1?raw=1"
    )
    summary = chat_summary(
        manifest, {"title": "Контроль", "answer": "Измерение отсутствует"}
    )
    assert "Измерение отсутствует" in summary
    assert "/monitor/p1" in summary
    assert "export-one-report.txt" in summary
    assert "/api/projects" not in summary
