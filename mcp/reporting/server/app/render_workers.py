import asyncio
import logging
import pickle
import sys
import weakref
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import MAX_CONCURRENT_RENDERS, MAX_TOTAL_OUTPUT_BYTES
from app.errors import FORMAT_ERROR_MESSAGES
from app.rendering.render import CONTENT_TYPES, RenderedFormat

logger = logging.getLogger(__name__)
_slots_by_loop = weakref.WeakKeyDictionary()


def render_slots():
    loop = asyncio.get_running_loop()
    slots = _slots_by_loop.get(loop)
    if slots is None:
        slots = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
        _slots_by_loop[loop] = slots
    return slots


def failed_format(fmt, code):
    return RenderedFormat(
        status="failed",
        filename=f"report.{fmt}",
        content_type=CONTENT_TYPES[fmt],
        payload=None,
        size_bytes=0,
        sha256=None,
        error={"code": code, "stage": "render", "message": FORMAT_ERROR_MESSAGES[code]},
    )


def timed_out_formats(formats):
    return {fmt: failed_format(fmt, "RENDER_TIMEOUT") for fmt in formats}


async def stop_process(process):
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        await process.wait()
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def wait_for_process(process, timeout_seconds):
    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except BaseException:
        await asyncio.shield(stop_process(process))
        raise


async def load_worker_result(path):
    payload = await asyncio.to_thread(path.read_bytes)
    await asyncio.to_thread(path.unlink)
    result = pickle.loads(payload)
    if not isinstance(result, RenderedFormat):
        raise TypeError("Renderer returned an invalid result")
    return result


async def render_formats_in_workers(
    original,
    template_id,
    template_version,
    formats,
    *,
    deadline_seconds,
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    rendered = {}
    total_size = 0
    slots = render_slots()
    try:
        async with asyncio.timeout(max(0, deadline - loop.time())):
            await slots.acquire()
    except TimeoutError:
        return timed_out_formats(formats)

    try:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            remaining = deadline - loop.time()
            if remaining <= 0:
                return timed_out_formats(formats)
            try:
                async with asyncio.timeout(remaining):
                    await asyncio.to_thread(report_path.write_bytes, original)
            except TimeoutError:
                return timed_out_formats(formats)
            for index, fmt in enumerate(formats):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    rendered.update(timed_out_formats(formats[index:]))
                    break
                result_path = root / f"result-{index}.pickle"
                try:
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "app.render_worker",
                        str(report_path),
                        str(result_path),
                        template_id,
                        template_version,
                        fmt,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    returncode = await wait_for_process(
                        process,
                        max(0, deadline - loop.time()),
                    )
                    if returncode != 0 or not result_path.is_file():
                        raise RuntimeError("Renderer process failed")
                    async with asyncio.timeout(max(0, deadline - loop.time())):
                        item = await load_worker_result(result_path)
                    if item.status == "generated" and (
                        total_size + item.size_bytes > MAX_TOTAL_OUTPUT_BYTES
                    ):
                        item = failed_format(fmt, "OUTPUT_TOO_LARGE")
                    total_size += item.size_bytes
                    rendered[fmt] = item
                except TimeoutError:
                    rendered[fmt] = failed_format(fmt, "RENDER_TIMEOUT")
                    rendered.update(timed_out_formats(formats[index + 1 :]))
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    rendered[fmt] = failed_format(fmt, "RENDER_FAILED")
                    logger.warning(
                        "[REPORT] template_id=%s template_version=%s format=%s error_type=%s — renderer process failed",
                        template_id,
                        template_version,
                        fmt,
                        type(exc).__name__,
                    )
    finally:
        slots.release()
    return rendered
