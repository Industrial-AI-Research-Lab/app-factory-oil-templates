import asyncio
import hashlib
import json

import httpx


def test_retry_verifies_existing_attachment_without_duplicate_upload(tmp_path):
    from reporting.publish import publish_report

    original = b"original PDF bytes"
    (tmp_path / "report.pdf").write_bytes(original)
    source = b'{"result_id":"r"}'
    (tmp_path / "report-data.json").write_bytes(source)
    manifest = {
        "export_id": "export-one",
        "project_id": "p1",
        "run_id": "run1",
        "result": {
            "id": "r",
            "version": "1",
            "sha256": hashlib.sha256(source).hexdigest(),
        },
        "template": {"id": "knowledge", "version": "1.0.0"},
        "formats": {
            "pdf": {
                "filename": "report.pdf",
                "mime_type": "application/pdf",
                "size_bytes": len(original),
                "sha256": hashlib.sha256(original).hexdigest(),
                "status": "generated",
            }
        },
    }
    path = tmp_path / "export-manifest.json"
    path.write_text(json.dumps(manifest))
    state = {"attachments": [], "upload_count": 0, "download_fails": True}

    def respond(request):
        if request.method == "GET" and request.url.path.endswith("/attachments"):
            return httpx.Response(
                200, json={"attachments": state["attachments"], "truncated": False}
            )
        if request.method == "POST":
            state["upload_count"] += 1
            state["attachments"] = [
                {
                    "attachment_id": "file-1",
                    "filename": "export-one-report.pdf",
                    "size_bytes": len(original),
                }
            ]
            return httpx.Response(200, json={"attachments": state["attachments"]})
        if state["download_fails"]:
            return httpx.Response(503)
        return httpx.Response(
            200, content=original, headers={"Content-Type": "application/pdf"}
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="https://AppFactory.test"
        ) as client:
            first = await publish_report(path, client)
            assert first["formats"]["pdf"]["status"] == "failed"
            assert first["formats"]["pdf"]["error"]["stage"] == "publish"
            assert first["formats"]["pdf"]["attachment_id"] == "file-1"
            state["download_fails"] = False
            second = await publish_report(path, client)
            assert second["formats"]["pdf"]["status"] == "ready"
            assert (
                second["formats"]["pdf"]["download_path"]
                == "/api/projects/p1/attachments/file-1?raw=1"
            )
            assert state["upload_count"] == 1
            assert json.loads(path.read_text()) == second

    asyncio.run(run())
