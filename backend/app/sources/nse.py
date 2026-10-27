"""NSE corporate announcements - best effort.

NSE's API needs session cookies and often refuses data-centre IPs, so FINN only uses it when a
company has no BSE listing. Failures are expected and swallowed by the caller.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

from .. import cache
from ..config import USER_AGENT
from .prices import IST, now_ist

log = logging.getLogger("finn.nse")

HOME = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
API = "https://www.nseindia.com/api/corporate-announcements"


async def _fetch(symbol: str, start: datetime, end: datetime) -> list[dict]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": HOME,
    }
    async with httpx.AsyncClient(headers=headers, timeout=10.0, follow_redirects=True) as c:
        await c.get(HOME)  # sets the cookies the API requires
        r = await c.get(
            API,
            params={
                "index": "equities",
                "symbol": symbol,
                "from_date": start.strftime("%d-%m-%Y"),
                "to_date": end.strftime("%d-%m-%Y"),
            },
        )
        r.raise_for_status()
        rows = r.json() or []
    out = []
    for row in rows:
        try:
            dt = datetime.strptime(row.get("an_dt", ""), "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            continue
        out.append(
            {
                "id": f"NSE:{row.get('seq_id')}",
                "source": "NSE",
                "bse_code": "",
                "company": row.get("sm_name") or symbol,
                "time": dt.isoformat(),
                "subject": (row.get("desc") or "").strip(),
                "raw_subject": (row.get("desc") or "").strip(),
                "detail": (row.get("attchmntText") or "").strip(),
                "exchange_category": (row.get("desc") or "").strip(),
                "exchange_subcategory": "",
                "critical": False,
                "pdf_url": row.get("attchmntFile"),
                "attachment": row.get("attchmntFile"),
                "pdf_size": None,
            }
        )
    return out


async def announcements(symbol: str, days: int = 30) -> list[dict]:
    end = now_ist()
    start = end - timedelta(days=days)
    key = f"nse:ann:{symbol}:{days}:{end.strftime('%Y%m%d')}"
    return await cache.cached(key, 300, lambda: _fetch(symbol, start, end))
