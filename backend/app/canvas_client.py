from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx


class CanvasSecurityError(RuntimeError):
    """Raised when a Canvas request violates the read-only harness."""


class CanvasReadOnlyClient:
    ALLOWED_METHODS = {"GET", "HEAD"}
    ALLOWED_API_PREFIX = "/api/v1/"
    ALLOWED_DOWNLOAD_PREFIX = "/files/"

    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout_seconds: float = 60.0,
        download_timeout_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        logger: logging.Logger | None = None,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self._base = urlparse(self.base_url)
        self._token = token
        self._download_timeout_seconds = download_timeout_seconds
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )
        self._logger = logger or logging.getLogger("canvas_audit")

    async def __aenter__(self) -> "CanvasReadOnlyClient":
        return self

    async def __aexit__(self, *_exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise CanvasSecurityError("CANVAS_API_TOKEN is not configured")
        try:
            self._token.encode("ascii")
        except UnicodeEncodeError:
            raise CanvasSecurityError("CANVAS_API_TOKEN contains invalid non-ASCII characters")
        return {"Authorization": f"Bearer {self._token}"}

    def _headers_for_url(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        if parsed.netloc.lower() == self._base.netloc.lower():
            return self._headers()
        return {}

    def _is_canvas_download_url(self, parsed) -> bool:
        return (
            parsed.scheme == "https"
            and parsed.netloc.lower() == self._base.netloc.lower()
            and parsed.path.startswith(self.ALLOWED_DOWNLOAD_PREFIX)
            and parsed.path.endswith("/download")
        )

    def _safe_url(self, path_or_url: str) -> str:
        candidate = path_or_url.strip()
        parsed_candidate = urlparse(candidate)
        if parsed_candidate.scheme or parsed_candidate.netloc:
            parsed = parsed_candidate
            safe = candidate
        else:
            safe = urljoin(self.base_url, candidate.lstrip("/"))
            parsed = urlparse(safe)

        if parsed.scheme != "https":
            raise CanvasSecurityError("Only https Canvas URLs are allowed")
        if parsed.netloc.lower() != self._base.netloc.lower():
            raise CanvasSecurityError("Only the configured Canvas host is allowed")
        if parsed.fragment:
            raise CanvasSecurityError("URL fragments are not allowed")
        if not (
            parsed.path.startswith(self.ALLOWED_API_PREFIX)
            or (
                parsed.path.startswith(self.ALLOWED_DOWNLOAD_PREFIX)
                and parsed.path.endswith("/download")
            )
        ):
            raise CanvasSecurityError(f"Canvas path is not allowlisted: {parsed.path}")
        return safe

    def _safe_download_redirect_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise CanvasSecurityError("Canvas file redirects must use https")
        if not parsed.netloc:
            raise CanvasSecurityError("Canvas file redirect must include a host")
        if parsed.username or parsed.password:
            raise CanvasSecurityError("Canvas file redirects cannot include credentials")
        if parsed.fragment:
            raise CanvasSecurityError("Canvas file redirect fragments are not allowed")
        return url

    def _audit(
        self,
        method: str,
        url: str,
        *,
        status_code: int | None = None,
        byte_count: int | None = None,
        error: str | None = None,
    ) -> None:
        parsed = urlparse(url)
        # Intentionally omit the query string because Canvas file verifier query
        # values are bearer-like secrets.
        self._logger.info(
            "method=%s path=%s status=%s bytes=%s error=%s",
            method,
            parsed.path,
            status_code if status_code is not None else "-",
            byte_count if byte_count is not None else "-",
            error or "-",
        )

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict | list[tuple[str, str]] | None = None,
    ) -> httpx.Response:
        method = method.upper()
        if method not in self.ALLOWED_METHODS:
            raise CanvasSecurityError(f"Canvas method is not read-only: {method}")

        url = self._safe_url(path_or_url)
        allow_file_redirect = method == "GET" and self._is_canvas_download_url(urlparse(url))
        next_params = params
        for _ in range(4):
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=next_params,
                    headers=self._headers_for_url(url),
                )
            except Exception as exc:
                self._audit(method, url, error=exc.__class__.__name__)
                raise

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise CanvasSecurityError("Canvas redirect without Location")
                redirected = urljoin(str(response.url), location)
                redirected_parsed = urlparse(redirected)
                if redirected_parsed.netloc.lower() == self._base.netloc.lower():
                    url = self._safe_url(redirected)
                elif allow_file_redirect:
                    url = self._safe_download_redirect_url(redirected)
                else:
                    raise CanvasSecurityError("Canvas API redirects to non-Canvas hosts are not allowed")
                next_params = None
                continue

            self._audit(
                method,
                str(response.url),
                status_code=response.status_code,
                byte_count=len(response.content or b""),
            )
            response.raise_for_status()
            return response

        raise CanvasSecurityError("Too many Canvas redirects")

    async def get_json(
        self,
        path_or_url: str,
        *,
        params: dict | list[tuple[str, str]] | None = None,
    ):
        response = await self.request("GET", path_or_url, params=params)
        return response.json()

    async def paginate(
        self,
        path_or_url: str,
        *,
        params: dict | list[tuple[str, str]] | None = None,
        max_pages: int = 50,
    ) -> list:
        items: list = []
        url = path_or_url
        next_params = params
        for _ in range(max_pages):
            response = await self.request("GET", url, params=next_params)
            payload = response.json()
            if isinstance(payload, list):
                items.extend(payload)
            else:
                items.append(payload)
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return items
            url = next_url
            next_params = None
        raise CanvasSecurityError("Canvas pagination exceeded max_pages")

    async def download_to_file(
        self,
        url: str,
        destination: Path,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> tuple[str, int]:
        method = "GET"
        safe_url = self._safe_url(url)
        allow_file_redirect = self._is_canvas_download_url(urlparse(safe_url))
        next_params = None
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        digest = hashlib.sha256()
        stream_timeout = (
            self._download_timeout_seconds
            if self._download_timeout_seconds is not None
            else httpx.USE_CLIENT_DEFAULT
        )

        for _ in range(4):
            if check_cancelled:
                check_cancelled()
            try:
                async with self._client.stream(
                    method,
                    safe_url,
                    params=next_params,
                    headers=self._headers_for_url(safe_url),
                    timeout=stream_timeout,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise CanvasSecurityError("Canvas redirect without Location")
                        redirected = urljoin(str(response.url), location)
                        redirected_parsed = urlparse(redirected)
                        if redirected_parsed.netloc.lower() == self._base.netloc.lower():
                            safe_url = self._safe_url(redirected)
                        elif allow_file_redirect:
                            safe_url = self._safe_download_redirect_url(redirected)
                        else:
                            raise CanvasSecurityError("Canvas API redirects to non-Canvas hosts are not allowed")
                        next_params = None
                        continue

                    response.raise_for_status()
                    byte_count = 0
                    with tmp.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            if check_cancelled:
                                check_cancelled()
                            output.write(chunk)
                            digest.update(chunk)
                            byte_count += len(chunk)
                    self._audit(method, str(response.url), status_code=response.status_code, byte_count=byte_count)
                    tmp.replace(destination)
                    return digest.hexdigest(), byte_count
            except Exception as exc:
                if tmp.exists():
                    tmp.unlink()
                self._audit(method, safe_url, error=exc.__class__.__name__)
                raise

        raise CanvasSecurityError("Too many Canvas redirects")
