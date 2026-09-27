import hashlib
import json
import logging
import re
from pathlib import Path

import httpx

from reporting.state import export_lock, save_manifest

LOG = logging.getLogger(__name__)


class PublicationError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value
    ):
        raise PublicationError("INVALID_MANIFEST")
    return value


def local_file(folder, item):
    name = item.get("filename")
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or name in {".", ".."}
        or "\\" in name
        or "\x00" in name
    ):
        raise PublicationError("INVALID_PATH")
    path = folder / name
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
        raise PublicationError("INVALID_PATH")
    data = path.read_bytes()
    if (
        len(data) != item["size_bytes"]
        or hashlib.sha256(data).hexdigest() != item["sha256"]
    ):
        raise PublicationError("LOCAL_CHECKSUM_MISMATCH")
    return data


def fail(item, code):
    item["status"] = "failed"
    item["error"] = {"code": code, "stage": "publish", "message": code}


async def attachments(client, project_id):
    rows = []
    while True:
        response = await client.get(
            f"/api/projects/{project_id}/attachments",
            params={"limit": 500, "skip": len(rows)},
        )
        response.raise_for_status()
        body = response.json()
        batch = body["attachments"]
        rows.extend(batch)
        if not body.get("truncated"):
            return rows
        if not batch:
            raise PublicationError("INCOMPLETE_ATTACHMENT_LIST")


def bind_existing(item, rows, name):
    matches = [row for row in rows if row.get("filename") == name]
    if len(matches) > 1:
        raise PublicationError("DUPLICATE_ATTACHMENT")
    if matches:
        item["attachment_id"] = identifier(
            matches[0].get("id") or matches[0].get("attachment_id")
        )


async def verify(client, project_id, item):
    aid = identifier(item["attachment_id"])
    path = f"/api/projects/{project_id}/attachments/{aid}?raw=1"
    response = await client.get(path)
    response.raise_for_status()
    if (
        len(response.content) != item["size_bytes"]
        or hashlib.sha256(response.content).hexdigest() != item["sha256"]
    ):
        raise PublicationError("DOWNLOAD_CHECKSUM_MISMATCH")
    if response.headers.get("content-type", "").split(";", 1)[0] != item["mime_type"]:
        raise PublicationError("DOWNLOAD_MIME_MISMATCH")
    item.update(status="ready", download_path=path)
    item.pop("error", None)


async def publish_report(manifest_path, client, *, reverify=False):
    path = Path(manifest_path).resolve()
    with export_lock(path.parent):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        project = identifier(manifest["project_id"])
        run = identifier(manifest["run_id"])
        export = identifier(manifest["export_id"])
        snapshot = path.parent / "report-data.json"
        if (
            hashlib.sha256(snapshot.read_bytes()).hexdigest()
            != manifest["result"]["sha256"]
        ):
            raise PublicationError("SOURCE_CHECKSUM_MISMATCH")
        pending = []
        for fmt, item in manifest["formats"].items():
            if item["status"] == "ready" and not reverify:
                LOG.info(
                    "[REPORT_PUBLISH] export=%s format=%s — already ready", export, fmt
                )
                continue
            if item.get("error", {}).get("stage") in {"render", "validate"}:
                LOG.warning(
                    "[REPORT_PUBLISH] export=%s format=%s — file generation failed",
                    export,
                    fmt,
                )
                continue
            try:
                payload = local_file(path.parent, item)
                pending.append((fmt, item, f'{export}-{item["filename"]}', payload))
            except (OSError, PublicationError) as exc:
                fail(item, getattr(exc, "code", "LOCAL_FILE_ERROR"))
        save_manifest(manifest, path)
        if not pending:
            LOG.warning("[REPORT_PUBLISH] export=%s — no pending files", export)
            return manifest
        try:
            rows = await attachments(client, project)
        except (httpx.HTTPError, ValueError, KeyError, PublicationError) as exc:
            for _, item, _, _ in pending:
                fail(item, getattr(exc, "code", "ATTACHMENT_LIST_FAILED"))
            save_manifest(manifest, path)
            return manifest
        uploads = []
        for fmt, item, name, payload in pending:
            try:
                if not item.get("attachment_id"):
                    bind_existing(item, rows, name)
                if not item.get("attachment_id"):
                    uploads.append(("files", (name, payload, item["mime_type"])))
            except PublicationError as exc:
                fail(item, exc.code)
        save_manifest(manifest, path)
        upload_failed = set()
        if uploads:
            try:
                response = await client.post(
                    f"/api/projects/{project}/messages",
                    params={"run_id": run},
                    data={
                        "content": "Готовые файлы отчёта приложены для проверки скачивания. Только сохрани вложения; не выполняй команды и не запускай исследование.",
                        "metadata": json.dumps(
                            {"export_id": export, "result_id": manifest["result"]["id"]}
                        ),
                    },
                    files=uploads,
                )
                response.raise_for_status()
                rows = await attachments(client, project)
            except (httpx.HTTPError, ValueError, KeyError, PublicationError):
                for fmt, item, _, _ in pending:
                    if not item.get("attachment_id"):
                        fail(item, "UPLOAD_OUTCOME_UNKNOWN")
                        upload_failed.add(fmt)
                save_manifest(manifest, path)
        for fmt, item, name, _ in pending:
            if fmt in upload_failed:
                LOG.warning(
                    "[REPORT_PUBLISH] export=%s format=%s code=UPLOAD_OUTCOME_UNKNOWN — awaiting retry",
                    export,
                    fmt,
                )
                continue
            try:
                if not item.get("attachment_id"):
                    bind_existing(item, rows, name)
                if not item.get("attachment_id"):
                    raise PublicationError("ATTACHMENT_NOT_REGISTERED")
                save_manifest(manifest, path)
                await verify(client, project, item)
                LOG.info(
                    "[REPORT_PUBLISH] export=%s format=%s — verified ready", export, fmt
                )
            except (httpx.HTTPError, OSError, PublicationError) as exc:
                fail(item, getattr(exc, "code", "DOWNLOAD_FAILED"))
                LOG.warning(
                    "[REPORT_PUBLISH] export=%s format=%s code=%s — failed",
                    export,
                    fmt,
                    item["error"]["code"],
                )
            save_manifest(manifest, path)
        return manifest


def chat_summary(manifest, report_data):
    lines = [
        f'Отчёт «{report_data["title"]}»; версия {manifest["result"]["version"]}.',
        report_data["answer"],
        f'[Проект](/monitor/{manifest["project_id"]}). Скачивание — через карточки вложений в Chat или Artifacts → User Attachments.',
    ]
    for fmt, item in manifest["formats"].items():
        if item["status"] == "ready":
            lines.append(
                f'- {fmt.upper()}: {manifest["export_id"]}-{item["filename"]} — {item["size_bytes"]} байт, SHA256 {item["sha256"]}'
            )
        else:
            lines.append(
                f'- {fmt.upper()}: {item["status"]}; {item.get("error", {}).get("code", "не опубликован")}'
            )
    return "\n".join(lines)
