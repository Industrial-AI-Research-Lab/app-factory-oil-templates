import asyncio
import hashlib
import importlib
import json
import pickle
import sys
from pathlib import Path

import pytest

from app.rendering.render import CONTENT_TYPES, RenderedFormat


def worker_module():
    try:
        return importlib.import_module("app.render_workers")
    except ImportError:
        return None


def report_bytes():
    data = json.loads((Path(__file__).parent / "fixtures" / "small.json").read_text())
    payload = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return payload


def generated_format(fmt):
    payload = f"rendered-{fmt}".encode()
    return RenderedFormat(
        status="generated",
        filename=f"report.{fmt}",
        content_type=CONTENT_TYPES[fmt],
        payload=payload,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        error=None,
    )


class FakeProcess:
    def __init__(self, fmt, result_path, release):
        self.fmt = fmt
        self.result_path = Path(result_path)
        self.release = release
        self.returncode = None

    async def wait(self):
        await self.release.wait()
        if self.returncode is None:
            self.result_path.write_bytes(pickle.dumps(generated_format(self.fmt)))
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = -15
        self.release.set()

    def kill(self):
        self.terminate()


@pytest.mark.asyncio
async def test_timed_out_process_is_terminated_before_it_can_write(tmp_path):
    workers = worker_module()
    assert workers is not None
    marker = tmp_path / "late.txt"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        (
            "import pathlib,time; time.sleep(0.2); "
            f"pathlib.Path({str(marker)!r}).write_text('late')"
        ),
    )

    with pytest.raises(TimeoutError):
        await workers.wait_for_process(process, timeout_seconds=0.01)
    await asyncio.sleep(0.25)

    assert not marker.exists()


@pytest.mark.asyncio
async def test_completed_format_survives_later_worker_timeout(monkeypatch):
    workers = worker_module()
    assert workers is not None
    hanging_release = asyncio.Event()

    async def create_process(*args, **kwargs):
        fmt = args[-1]
        release = asyncio.Event() if fmt == "txt" else hanging_release
        if fmt == "txt":
            release.set()
        return FakeProcess(fmt, args[-4], release)

    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", create_process)

    rendered = await workers.render_formats_in_workers(
        report_bytes(),
        "knowledge",
        "1.0.0",
        ["txt", "pdf"],
        deadline_seconds=0.05,
    )

    assert rendered["txt"].status == "generated"
    assert rendered["pdf"].status == "failed"
    assert rendered["pdf"].error["code"] == "RENDER_TIMEOUT"


@pytest.mark.asyncio
async def test_render_jobs_have_bounded_concurrency(monkeypatch):
    workers = worker_module()
    assert workers is not None
    release = asyncio.Event()
    launched = []

    async def create_process(*args, **kwargs):
        launched.append(args[-1])
        return FakeProcess(args[-1], args[-4], release)

    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", create_process)
    tasks = [
        asyncio.create_task(
            workers.render_formats_in_workers(
                report_bytes(),
                "knowledge",
                "1.0.0",
                ["json"],
                deadline_seconds=1,
            )
        )
        for _ in range(3)
    ]
    while len(launched) < 2:
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)

    assert len(launched) == 2
    release.set()
    await asyncio.gather(*tasks)
    assert len(launched) == 3
