import asyncio
import logging
from collections import deque
from typing import Annotated, Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import ValidationError, WithJsonSchema
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse

from app.config import (
    MAX_OUTPUTS,
    MAX_REQUEST_BODY_BYTES,
    SERVICE_ID,
    TOOL_TIMEOUT_SECONDS,
    UPLOAD_DEADLINE_SECONDS,
    graph_limits,
    upload_allowed_origins,
)
from app.errors import ReportError
from app.models import (
    OutputTarget,
    RenderReportRequest,
    RenderReportResponse,
    SourceArtifact,
    TemplateId,
    TemplateVersion,
)
from app.report_tool import render_report_impl

logger = logging.getLogger(__name__)
UPLOAD_TIMEOUT = httpx.Timeout(UPLOAD_DEADLINE_SECONDS, connect=5.0)
OutputTargetsInput = Annotated[
    list[dict[str, Any]],
    WithJsonSchema(
        {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_OUTPUTS,
            "items": OutputTarget.model_json_schema(),
        }
    ),
]
SourceArtifactsInput = Annotated[
    list[dict[str, Any]] | None,
    WithJsonSchema(
        {
            "anyOf": [
                {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": SourceArtifact.model_json_schema(),
                },
                {"type": "null"},
            ]
        }
    ),
]
UploadUrlInput = Annotated[
    str,
    WithJsonSchema({"type": "string", "format": "uri", "pattern": "^https?://"}),
]
Sha256Input = Annotated[
    str | None,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                {"type": "null"},
            ]
        }
    ),
]


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        try:
            declared_size = int(content_length) if content_length is not None else None
        except ValueError:
            await self._reject(scope, receive, send, status_code=400)
            return
        if declared_size is not None and declared_size < 0:
            await self._reject(scope, receive, send, status_code=400)
            return
        if declared_size is not None and declared_size > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        buffered = deque()
        received = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def validated_receive():
            if buffered:
                return buffered.popleft()
            return await receive()

        await self.app(scope, validated_receive, send)

    @staticmethod
    async def _reject(scope, receive, send, status_code=413):
        response = PlainTextResponse(
            "Request body too large" if status_code == 413 else "Invalid request body",
            status_code=status_code,
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


mcp = FastMCP(
    name=SERVICE_ID,
    mask_error_details=True,
    on_duplicate="error",
)


@mcp.tool(
    name="render_report",
    description="Render a validated report and upload outputs to signed object URLs.",
    output_schema=RenderReportResponse.model_json_schema(),
    timeout=TOOL_TIMEOUT_SECONDS,
)
async def render_report_tool(
    report_data: dict[str, Any],
    template_id: TemplateId,
    template_version: TemplateVersion,
    outputs: OutputTargetsInput,
    manifest_upload_url: UploadUrlInput,
    expected_result_sha256: Sha256Input = None,
    source_artifacts: SourceArtifactsInput = None,
    expected_collection_sha256: Sha256Input = None,
) -> dict[str, Any]:
    try:
        request = await asyncio.to_thread(
            RenderReportRequest.model_validate,
            {
                "report_data": report_data,
                "template_id": template_id,
                "template_version": template_version,
                "outputs": outputs,
                "manifest_upload_url": manifest_upload_url,
                "expected_result_sha256": expected_result_sha256,
                "source_artifacts": source_artifacts,
                "expected_collection_sha256": expected_collection_sha256,
            },
        )
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
            response = await render_report_impl(request, client)
    except ValidationError:
        logger.warning(
            "[REPORT] tool=render_report code=INVALID_INPUT — request rejected"
        )
        raise ToolError("INVALID_INPUT:validate") from None
    except ReportError as exc:
        logger.warning(
            "[REPORT] tool=render_report code=%s stage=%s — request failed",
            exc.code,
            exc.stage,
        )
        raise ToolError(f"{exc.code}:{exc.stage}") from None
    return response.model_dump(mode="json")


def main():
    configure_logging()
    graph_limits()
    upload_allowed_origins()
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8004,
        path="/mcp",
        middleware=[
            Middleware(
                RequestBodyLimitMiddleware,
                max_bytes=MAX_REQUEST_BODY_BYTES,
            )
        ],
    )


if __name__ == "__main__":
    main()
