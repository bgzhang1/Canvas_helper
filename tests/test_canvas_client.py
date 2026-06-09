from __future__ import annotations

import logging

import httpx
import pytest

from backend.app.canvas_client import CanvasReadOnlyClient, CanvasSecurityError


@pytest.mark.asyncio
async def test_get_and_head_are_allowed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json=[] if request.method == "GET" else None)

    transport = httpx.MockTransport(handler)
    async with CanvasReadOnlyClient(
        "https://cityu-dg.instructure.com/",
        "test-token",
        transport=transport,
        logger=logging.getLogger("test"),
    ) as client:
        response = await client.request("GET", "/api/v1/courses")
        assert response.status_code == 200
        response = await client.request("HEAD", "/api/v1/courses")
        assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_write_methods_are_rejected(method: str) -> None:
    async with CanvasReadOnlyClient(
        "https://cityu-dg.instructure.com/",
        "test-token",
        logger=logging.getLogger("test"),
    ) as client:
        with pytest.raises(CanvasSecurityError):
            await client.request(method, "/api/v1/courses/1")


@pytest.mark.asyncio
async def test_non_canvas_host_is_rejected() -> None:
    async with CanvasReadOnlyClient(
        "https://cityu-dg.instructure.com/",
        "test-token",
        logger=logging.getLogger("test"),
    ) as client:
        with pytest.raises(CanvasSecurityError):
            await client.request("GET", "https://evil.example.com/api/v1/courses")


@pytest.mark.asyncio
async def test_non_allowlisted_path_is_rejected() -> None:
    async with CanvasReadOnlyClient(
        "https://cityu-dg.instructure.com/",
        "test-token",
        logger=logging.getLogger("test"),
    ) as client:
        with pytest.raises(CanvasSecurityError):
            await client.request("GET", "/login/canvas")


@pytest.mark.asyncio
async def test_redirect_to_non_canvas_host_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.com/api/v1/courses"})

    transport = httpx.MockTransport(handler)
    async with CanvasReadOnlyClient(
        "https://cityu-dg.instructure.com/",
        "test-token",
        transport=transport,
        logger=logging.getLogger("test"),
    ) as client:
        with pytest.raises(CanvasSecurityError):
            await client.request("GET", "/api/v1/courses")


@pytest.mark.asyncio
async def test_file_download_redirect_to_https_storage_is_allowed_without_token() -> None:
    seen_authorization: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers.get("authorization"))
        if request.url.host == "cityu-dg.instructure.com":
            return httpx.Response(302, headers={"location": "https://canvas-files.example.net/object"})
        return httpx.Response(200, content=b"slides")

    transport = httpx.MockTransport(handler)
    async with CanvasReadOnlyClient(
        "https://cityu-dg.instructure.com/",
        "test-token",
        transport=transport,
        logger=logging.getLogger("test"),
    ) as client:
        response = await client.request(
            "GET",
            "https://cityu-dg.instructure.com/files/123/download?download_frd=1&verifier=secret",
        )
        assert response.content == b"slides"

    assert seen_authorization == ["Bearer test-token", None]


@pytest.mark.asyncio
async def test_file_download_redirect_to_http_storage_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://canvas-files.example.net/object"})

    transport = httpx.MockTransport(handler)
    async with CanvasReadOnlyClient(
        "https://cityu-dg.instructure.com/",
        "test-token",
        transport=transport,
        logger=logging.getLogger("test"),
    ) as client:
        with pytest.raises(CanvasSecurityError):
            await client.request(
                "GET",
                "https://cityu-dg.instructure.com/files/123/download?download_frd=1&verifier=secret",
            )


@pytest.mark.asyncio
async def test_download_path_is_allowed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"slides")

    transport = httpx.MockTransport(handler)
    async with CanvasReadOnlyClient(
        "https://cityu-dg.instructure.com/",
        "test-token",
        transport=transport,
        logger=logging.getLogger("test"),
    ) as client:
        response = await client.request(
            "GET",
            "https://cityu-dg.instructure.com/files/123/download?download_frd=1&verifier=secret",
        )
        assert response.content == b"slides"
