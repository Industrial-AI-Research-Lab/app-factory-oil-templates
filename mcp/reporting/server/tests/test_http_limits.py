import httpx
import pytest
from starlette.responses import Response


def test_request_body_budget_includes_mcp_envelope():
    from app.config import MAX_REPORT_DATA_BYTES, MAX_REQUEST_BODY_BYTES

    assert MAX_REQUEST_BODY_BYTES > MAX_REPORT_DATA_BYTES


class PassthroughMiddleware:
    def __init__(self, app, max_bytes):
        self.app = app

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


def limited_app(app, max_bytes):
    from app import server

    middleware = getattr(server, "RequestBodyLimitMiddleware", PassthroughMiddleware)
    return middleware(app, max_bytes=max_bytes)


@pytest.mark.asyncio
async def test_content_length_over_limit_is_rejected_before_downstream():
    calls = 0

    async def app(scope, receive, send):
        nonlocal calls
        calls += 1
        await Response(status_code=204)(scope, receive, send)

    transport = httpx.ASGITransport(app=limited_app(app, max_bytes=4))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", content=b"12345")

    assert response.status_code == 413
    assert calls == 0


@pytest.mark.asyncio
async def test_body_at_limit_is_replayed_to_downstream():
    body = None

    async def app(scope, receive, send):
        nonlocal body
        chunks = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        await Response(status_code=204)(scope, receive, send)

    transport = httpx.ASGITransport(app=limited_app(app, max_bytes=4))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", content=b"1234")

    assert response.status_code == 204
    assert body == b"1234"


@pytest.mark.asyncio
async def test_chunked_body_stops_when_limit_is_crossed():
    calls = 0

    async def app(scope, receive, send):
        nonlocal calls
        calls += 1
        await Response(status_code=204)(scope, receive, send)

    async def chunks():
        yield b"123"
        yield b"45"
        yield b"6789"

    transport = httpx.ASGITransport(app=limited_app(app, max_bytes=4))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mcp", content=chunks())

    assert response.status_code == 413
    assert calls == 0
