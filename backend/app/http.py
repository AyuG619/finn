"""Shared HTTP client with sane headers, timeouts and retries."""
from __future__ import annotations

import asyncio
import logging
import random

import httpx

from .config import HTTP_TIMEOUT, USER_AGENT

log = logging.getLogger("finn.http")

_client: httpx.AsyncClient | None = None

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-IN,en;q=0.9",
}


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{message} ({status}) for {url}")
        self.url = url
        self.status = status


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers=BROWSER_HEADERS,
            timeout=httpx.Timeout(HTTP_TIMEOUT, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


async def request(
    method: str,
    url: str,
    *,
    retries: int = 2,
    headers: dict | None = None,
    params: dict | None = None,
    json: object | None = None,
    content: bytes | None = None,
    timeout: float | None = None,
    ok_statuses: tuple[int, ...] = (200,),
) -> httpx.Response:
    """Perform a request, retrying transient failures with jittered backoff."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await client().request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                content=content,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            log.info("transport error %s on %s (attempt %d)", type(exc).__name__, url, attempt + 1)
        else:
            if resp.status_code in ok_statuses:
                return resp
            if resp.status_code not in RETRY_STATUS or attempt == retries:
                raise FetchError(url, resp.status_code, "unexpected status")
            last_exc = FetchError(url, resp.status_code, "retryable status")
        if attempt < retries:
            await asyncio.sleep(0.6 * (2**attempt) + random.random() * 0.4)
    raise FetchError(url, None, f"request failed: {last_exc!r}")


async def get_text(url: str, **kw) -> str:
    resp = await request("GET", url, **kw)
    return resp.text


async def get_json(url: str, **kw):
    resp = await request("GET", url, **kw)
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(url, resp.status_code, "invalid JSON") from exc


async def get_bytes(url: str, **kw) -> bytes:
    resp = await request("GET", url, **kw)
    return resp.content
