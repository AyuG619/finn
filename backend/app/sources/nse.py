"""NSE corporate announcements, annual reports and transcripts.

NSE's JSON API requires the session cookies its website sets, so FINN keeps one warm session and
re-warms it when NSE answers 401/403. From the deployed host (Render, Singapore) NSE answers while
BSE's API refuses, so NSE is the primary feed there and BSE the fallback.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta

import httpx

from .. import cache
from ..config import USER_AGENT
from .prices import IST, now_ist

log = logging.getLogger("finn.nse")

HOME = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
BASE = "https://www.nseindia.com"


class _Session:
    def __init__(self) -> None:
        self.client: httpx.AsyncClient | None = None
        self.warmed_at = 0.0
        self.lock = asyncio.Lock()
        self.sem = asyncio.Semaphore(3)

    def _new(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-IN,en;q=0.9",
                "Referer": HOME,
            },
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=True,
        )

    async def warm(self, force: bool = False) -> None:
        async with self.lock:
            if self.client and not force and time.time() - self.warmed_at < 600:
                return
            if self.client:
                await self.client.aclose()
            self.client = self._new()
            await self.client.get(HOME, headers={"Accept": "text/html,application/xhtml+xml"})
            self.warmed_at = time.time()

    async def get_json(self, path: str, params: dict | None = None):
        await self.warm()
        async with self.sem:
            for attempt in range(2):
                assert self.client is not None
                r = await self.client.get(f"{BASE}{path}", params=params)
                if r.status_code in (401, 403) and attempt == 0:
                    await self.warm(force=True)
                    continue
                r.raise_for_status()
                return r.json()
        raise RuntimeError("NSE refused the request")

    async def get_bytes(self, url: str) -> bytes:
        await self.warm()
        assert self.client is not None
        r = await self.client.get(url, headers={"Accept": "application/pdf,*/*"}, timeout=60.0)
        r.raise_for_status()
        return r.content


session = _Session()


_BOILER = re.compile(r"^.{2,120}?\bhas (?:informed|submitted to) the Exchange(?:,)?\s*(?:about|regarding|that)?\s*", re.I)


def clean_text(text: str, desc: str) -> str:
    """'X Limited has informed the Exchange regarding Appointment of Mr A as CFO' -> 'Appointment of Mr A as CFO'.
    Returns '' when the text only repeats the description."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = _BOILER.sub("", t)
    if t[:1].islower():
        t = t[0].upper() + t[1:]
    t = t.strip(" .'\"")
    if not t or t.lower().rstrip(".") == desc.lower().strip() or len(t) < 8:
        return ""
    return t


def normalise(row: dict, symbol: str) -> dict | None:
    try:
        dt = datetime.strptime(row.get("an_dt", ""), "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None
    desc = re.sub(r"\s+", " ", (row.get("desc") or "")).strip()
    text = clean_text(row.get("attchmntText") or "", desc)
    return {
        "id": f"NSE:{row.get('seq_id')}",
        "source": "NSE",
        "bse_code": "",
        "company": row.get("sm_name") or symbol,
        "time": dt.isoformat(),
        "subject": desc,
        "raw_subject": desc,
        "detail": text,
        "exchange_category": (row.get("desc") or "").strip(),
        "exchange_subcategory": "",
        "critical": False,
        "pdf_url": row.get("attchmntFile") or None,
        "attachment": row.get("attchmntFile") or None,
        "pdf_size": row.get("attFileSize") or row.get("fileSize"),
    }


async def _fetch(symbol: str, start: datetime, end: datetime) -> list[dict]:
    rows = await session.get_json(
        "/api/corporate-announcements",
        {"index": "equities", "symbol": symbol, "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")},
    )
    out, seen = [], set()
    for row in rows or []:
        n = normalise(row, symbol)
        if not n:
            continue
        key = n["attachment"] or n["id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


async def announcements(symbol: str, days: int = 30) -> list[dict]:
    end = now_ist()
    start = end - timedelta(days=days)
    key = f"nse:ann:{symbol}:{days}:{end.strftime('%Y%m%d')}"
    return await cache.cached(key, 300, lambda: _fetch(symbol, start, end))


async def annual_reports(symbol: str) -> list[dict]:
    async def fetch():
        payload = await session.get_json("/api/annual-reports", {"index": "equities", "symbol": symbol})
        rows = payload.get("data") if isinstance(payload, dict) else payload
        out = []
        for r in rows or []:
            url = r.get("fileName") or ""
            if not url:
                continue
            out.append({"from_year": r.get("fromYr"), "to_year": r.get("toYr"), "url": url, "is_pdf": url.lower().endswith(".pdf")})
        return out

    return await cache.cached(f"nse:ar:{symbol}", 24 * 3600, fetch)


async def event_calendar(days_ahead: int = 30) -> list[dict]:
    """Upcoming board meetings across the market (one request), e.g. results dates."""
    start = now_ist()
    end = start + timedelta(days=days_ahead)

    async def fetch():
        rows = await session.get_json(
            "/api/event-calendar", {"index": "equities", "from_date": start.strftime("%d-%m-%Y"), "to_date": end.strftime("%d-%m-%Y")}
        )
        out = []
        for r in rows or []:
            try:
                d = datetime.strptime(r.get("date", ""), "%d-%b-%Y").date().isoformat()
            except ValueError:
                continue
            out.append({"symbol": r.get("symbol"), "date": d, "purpose": r.get("purpose") or "", "desc": (r.get("bm_desc") or "")[:200]})
        return out

    return await cache.cached(f"nse:calendar:{start.strftime('%Y%m%d')}:{days_ahead}", 3 * 3600, fetch)
