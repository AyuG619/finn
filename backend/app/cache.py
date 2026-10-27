"""Small two-level cache (memory + disk) with TTLs and single-flight de-duplication.

Every expensive thing FINN does (HTTP fetches, PDF text extraction, LLM calls) goes through here,
so a repeated question costs nothing and concurrent identical requests share one in-flight call.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import DATA_DIR

_CACHE_DIR = DATA_DIR / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_BLOB_DIR = DATA_DIR / "blobs"
_BLOB_DIR.mkdir(parents=True, exist_ok=True)

_mem: dict[str, tuple[float, Any]] = {}
_MEM_MAX = 500
_inflight: dict[str, asyncio.Future] = {}

FOREVER = 10 * 365 * 24 * 3600

stats = {"hits": 0, "misses": 0}


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _path(key: str) -> Path:
    d = _digest(key)
    return _CACHE_DIR / d[:2] / f"{d}.json"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get(key: str, ttl: float, mem: bool = True) -> Any | None:
    """Return a cached value younger than ``ttl`` seconds, else None. ``mem=False`` keeps large
    values (e.g. annual-report text) on disk only, so the 512 MB host never holds them in RAM."""
    now = time.time()
    hit = _mem.get(key)
    if hit and now - hit[0] <= ttl:
        stats["hits"] += 1
        return hit[1]
    p = _path(key)
    try:
        st = p.stat()
    except FileNotFoundError:
        stats["misses"] += 1
        return None
    if now - st.st_mtime > ttl:
        stats["misses"] += 1
        return None
    try:
        payload = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        stats["misses"] += 1
        return None
    if mem:
        _remember(key, payload["v"], payload.get("t", st.st_mtime))
    stats["hits"] += 1
    return payload["v"]


def age(key: str) -> float | None:
    """Seconds since the value was stored, or None if absent."""
    hit = _mem.get(key)
    if hit:
        return time.time() - hit[0]
    try:
        return time.time() - _path(key).stat().st_mtime
    except FileNotFoundError:
        return None


def put(key: str, value: Any, mem: bool = True) -> Any:
    now = time.time()
    if mem:
        _remember(key, value, now)
    try:
        _atomic_write(_path(key), json.dumps({"t": now, "v": value}, ensure_ascii=False, default=str).encode("utf-8"))
    except OSError:
        pass  # Disk is a best-effort second level; memory still works.
    return value


def delete(key: str) -> None:
    _mem.pop(key, None)
    try:
        _path(key).unlink()
    except FileNotFoundError:
        pass


def _remember(key: str, value: Any, t: float) -> None:
    if len(_mem) >= _MEM_MAX:
        # Drop the oldest ~10% (cheap approximate LRU).
        for k, _ in sorted(_mem.items(), key=lambda kv: kv[1][0])[: _MEM_MAX // 10]:
            _mem.pop(k, None)
    _mem[key] = (t, value)


async def cached(
    key: str, ttl: float, fn: Callable[[], Awaitable[Any]], *, stale_ok_on_error: bool = True, mem: bool = True
) -> Any:
    """Return cached value or compute it once (concurrent callers share the same computation).

    If computing fails and an expired value exists, the expired value is returned instead of raising
    (``stale_ok_on_error``): a slightly old filing list beats an error screen.
    """
    val = get(key, ttl, mem)
    if val is not None:
        return val
    fut = _inflight.get(key)
    if fut is not None:
        return await asyncio.shield(fut)
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _inflight[key] = fut
    try:
        val = await fn()
        if val is not None:
            put(key, val, mem)
        fut.set_result(val)
        return val
    except BaseException as exc:
        stale = get(key, FOREVER, mem) if stale_ok_on_error else None
        if stale is not None:
            fut.set_result(stale)
            return stale
        fut.set_exception(exc)
        # Mark the exception as retrieved so asyncio doesn't log "never retrieved" for lone callers.
        fut.exception()
        raise
    finally:
        _inflight.pop(key, None)


def blob_path(key: str, suffix: str = ".bin") -> Path:
    d = _digest(key)
    return _BLOB_DIR / d[:2] / f"{d}{suffix}"


def get_blob(key: str, suffix: str = ".bin") -> bytes | None:
    p = blob_path(key, suffix)
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return None


def put_blob(key: str, data: bytes, suffix: str = ".bin") -> Path:
    p = blob_path(key, suffix)
    _atomic_write(p, data)
    return p
