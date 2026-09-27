import asyncio
import logging

import httpx

from app.config import UPLOAD_DEADLINE_SECONDS


def redact_httpx_log_value(value):
    if isinstance(value, httpx.URL):
        return value.copy_with(query=None, fragment=None)
    return value


class HttpxQueryRedactionFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(redact_httpx_log_value(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_httpx_log_value(value) for key, value in record.args.items()
            }
        return True


logging.getLogger("httpx").addFilter(HttpxQueryRedactionFilter())


class UploadError(RuntimeError):
    def __init__(self, code, error_type):
        self.code = code
        self.error_type = error_type
        super().__init__(code)


async def put_bytes(
    upload_url,
    payload,
    content_type,
    client,
    deadline_seconds=UPLOAD_DEADLINE_SECONDS,
):
    try:
        async with asyncio.timeout(deadline_seconds):
            response = await client.put(
                str(upload_url),
                content=payload,
                headers={"Content-Type": content_type},
                follow_redirects=False,
            )
            response.raise_for_status()
    except (
        TimeoutError,
        httpx.ReadError,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
        httpx.WriteError,
        httpx.WriteTimeout,
        httpx.CloseError,
    ) as exc:
        raise UploadError("UPLOAD_OUTCOME_UNKNOWN", type(exc).__name__) from None
    except httpx.HTTPError as exc:
        raise UploadError("UPLOAD_FAILED", type(exc).__name__) from None
