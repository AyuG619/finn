"""End-of-day price & volume.

Primary: Yahoo Finance's public chart API (the same data yfinance wraps, without the heavy
pandas dependency). Fallback: screener.in's chart API (NSE close + volume).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .. import cache, http

log = logging.getLogger("finn.prices")

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_CLOSE_BUFFER = (15, 40)  # treat today's bar as provisional until 15:40 IST


def now_ist() -> datetime:
    return datetime.now(IST)


def _is_provisional(date_str: str, now: datetime | None = None) -> bool:
    now = now or now_ist()
    if date_str != now.strftime("%Y-%m-%d"):
        return False
    return (now.hour, now.minute) < MARKET_CLOSE_BUFFER


def parse_yahoo_chart(payload: dict, now: datetime | None = None) -> dict:
    """Turn a Yahoo v8 chart payload into {meta, bars}. Raises ValueError if unusable."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        err = (payload.get("chart") or {}).get("error")
        raise ValueError(f"no chart result: {err}")
    r = result[0]
    meta = r.get("meta") or {}
    ts = r.get("timestamp") or []
    quote_ = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    offset = int(meta.get("gmtoffset") or 19800)
    bars: list[dict] = []
    seen: set[str] = set()
    for i, t in enumerate(ts):
        try:
            close = quote_["close"][i]
            vol = quote_["volume"][i]
        except (KeyError, IndexError, TypeError):
            continue
        if close is None:
            continue
        d = datetime.fromtimestamp(t + offset, tz=timezone.utc).strftime("%Y-%m-%d")
        if d in seen:  # Yahoo occasionally repeats the live bar; keep the latest.
            bars = [b for b in bars if b["date"] != d]
        seen.add(d)
        bars.append(
            {
                "date": d,
                "open": _num((quote_.get("open") or [None] * len(ts))[i]),
                "high": _num((quote_.get("high") or [None] * len(ts))[i]),
                "low": _num((quote_.get("low") or [None] * len(ts))[i]),
                "close": round(float(close), 2),
                "volume": int(vol or 0),
            }
        )
    if len(bars) < 2:
        raise ValueError("not enough bars")
    for b in bars:
        b["provisional"] = _is_provisional(b["date"], now)
    return {
        "source": "Yahoo Finance",
        "currency": meta.get("currency", "INR"),
        "name": meta.get("longName") or meta.get("shortName"),
        "fifty_two_week_high": _num(meta.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low": _num(meta.get("fiftyTwoWeekLow")),
        "bars": bars,
    }


def parse_screener_chart(payload: dict, now: datetime | None = None) -> dict:
    price, volume = {}, {}
    for ds in payload.get("datasets") or []:
        if ds.get("metric") == "Price":
            price = {v[0]: float(v[1]) for v in ds.get("values") or [] if v and v[1] is not None}
        elif ds.get("metric") == "Volume":
            volume = {v[0]: int(v[1] or 0) for v in ds.get("values") or [] if v}
    bars = [
        {"date": d, "open": None, "high": None, "low": None, "close": round(c, 2), "volume": volume.get(d, 0)}
        for d, c in sorted(price.items())
    ]
    if len(bars) < 2:
        raise ValueError("not enough bars")
    for b in bars:
        b["provisional"] = _is_provisional(b["date"], now)
    closes = [b["close"] for b in bars[-250:]]
    return {
        "source": "screener.in (NSE)",
        "currency": "INR",
        "name": None,
        "fifty_two_week_high": max(closes),
        "fifty_two_week_low": min(closes),
        "bars": bars,
    }


def _num(v):
    try:
        return None if v is None else round(float(v), 2)
    except (TypeError, ValueError):
        return None


async def _yahoo(symbol: str, range_: str) -> dict:
    ticker = quote(f"{symbol}.NS", safe="")
    last_exc: Exception | None = None
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}?range={range_}&interval=1d&includePrePost=false"
        try:
            payload = await http.get_json(url, retries=1, headers={"Accept": "application/json"})
            return parse_yahoo_chart(payload)
        except Exception as exc:  # noqa: BLE001 - try next host
            last_exc = exc
    raise RuntimeError(f"yahoo failed for {symbol}: {last_exc}")


async def _screener(symbol: str, days: int) -> dict:
    from . import screener  # local import to avoid a cycle

    meta = await screener.company_ids(symbol)
    url = f"https://www.screener.in/api/company/{meta['company_id']}/chart/?q=Price-Volume&days={days}&consolidated=true"
    payload = await http.get_json(url, retries=1, headers={"Accept": "application/json"})
    return parse_screener_chart(payload)


async def history(symbol: str, range_: str = "1y") -> dict:
    """Daily bars for an NSE symbol, cached for 10 minutes."""
    symbol = symbol.upper().strip()
    days = {"3mo": 100, "6mo": 190, "1y": 370, "2y": 740}.get(range_, 370)

    async def fetch() -> dict:
        try:
            return await _yahoo(symbol, range_)
        except Exception as exc:  # noqa: BLE001
            log.warning("Yahoo failed for %s (%s); falling back to screener", symbol, exc)
            return await _screener(symbol, days)

    return await cache.cached(f"prices:{symbol}:{range_}", 600, fetch)
