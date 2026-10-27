"""FINN API + static frontend."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import cache, http
from .config import REFRESH_MINUTES, STATIC_DIR, WARM_RESEARCH
from .llm import router as llm
from .portfolio import PORTFOLIO, RESEARCH_SET
from .push import digest as digest_mod
from .push import enrich as enrich_mod
from .push import notify
from .push.alerts import Thresholds, session_stats
from .push.classify import CATEGORIES
from .research import engine
from .sources import prices, screener

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("finn")

STARTED = time.time()
state = {"last_refresh": None, "last_refresh_error": None, "refreshes": 0}


# --------------------------------------------------------------------------- background work

async def refresh_once() -> None:
    """Rebuild the default digest, enrich what matters, and push alerts to subscribers."""
    d = await digest_mod.build(digest_mod.Prefs(hours=72))
    material = [x for x in d["top"] + d["notable"] if x["type"] == "filing" and (x["tier"] == "high" or x.get("watched"))]
    for it in material[:20]:
        reg = digest_mod.registered(it["id"])
        if not reg or enrich_mod.cached_enrichment(it["id"]):
            continue
        try:
            await enrich_mod.enrich(reg["filing"], reg["ctx"])
        except Exception as exc:  # noqa: BLE001
            log.info("enrich %s failed: %s", it["id"], exc)
    await push_alerts()
    state["last_refresh"] = time.time()
    state["refreshes"] += 1


async def push_alerts() -> None:
    subs = notify.subscriptions()
    for endpoint, entry in subs.items():
        p = entry.get("prefs") or {}
        try:
            d = await digest_mod.build(_prefs_from(p, hours=24))
        except Exception as exc:  # noqa: BLE001
            log.info("digest for push failed: %s", exc)
            continue
        by_id = {x["id"]: x for x in d["top"] + d["notable"]}
        fresh = [by_id[i] for i in d["alerts"] if i in by_id and i not in set(entry.get("sent", []))]
        fresh = [x for x in fresh if not (x["type"] == "move" and x.get("provisional"))]
        if not fresh:
            continue
        first = fresh[0]
        title = f"{first['name']}: " + (f"{first['change_pct']:+.1f}% · {', '.join(h['label'] for h in first['hits'])}" if first["type"] == "move" else first["category_label"])
        body = first.get("subject") if first["type"] == "filing" else (f"Likely driver: {first['driver']['subject']}" if first.get("driver") else "Unusual move in your portfolio")
        if len(fresh) > 1:
            body += f" (+{len(fresh) - 1} more)"
        status = await notify.send(endpoint, {"title": title, "body": body, "url": f"/?item={first['id']}", "tag": first["id"]})
        if status < 300:
            await notify.mark_sent(endpoint, [x["id"] for x in fresh])


async def background_loop() -> None:
    await asyncio.sleep(5)
    try:
        await llm.discover()
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM discovery failed: %s", exc)
    warmed = False
    while True:
        try:
            await refresh_once()
            state["last_refresh_error"] = None
        except Exception as exc:  # noqa: BLE001
            log.exception("refresh failed")
            state["last_refresh_error"] = f"{type(exc).__name__}: {exc}"
        if WARM_RESEARCH and not warmed:
            warmed = True
            for r in RESEARCH_SET:
                engine.get_or_start(r["symbol"])
                job = engine._jobs.get(r["symbol"])
                if job and job.task:
                    try:
                        await job.task
                    except Exception:  # noqa: BLE001
                        pass
        if REFRESH_MINUTES <= 0:
            return
        await asyncio.sleep(REFRESH_MINUTES * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_loop()) if REFRESH_MINUTES > 0 else None
    yield
    if task:
        task.cancel()
    await http.close()


app = FastAPI(title="FINN", version="1.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------- helpers

def _prefs_from(p: dict, hours: float | None = None) -> digest_mod.Prefs:
    t = Thresholds(
        price_pct=float(p.get("price_pct", 4.0)),
        vol_x=float(p.get("vol_x", 2.0)),
        lookback=int(p.get("lookback", 20)),
    )
    always = p.get("always")
    if isinstance(always, str):
        always = [a for a in always.split(",") if a]
    return digest_mod.Prefs(
        hours=float(hours if hours is not None else p.get("hours", 24)),
        thresholds=t,
        always=set(always) & set(CATEGORIES) if always is not None else set(digest_mod.DEFAULT_ALWAYS),
        symbols=p.get("symbols") or None,
    )


# --------------------------------------------------------------------------- routes

@app.get("/api/health")
async def health():
    return {"ok": True, "uptime_s": round(time.time() - STARTED), "last_refresh": state["last_refresh"], "refreshes": state["refreshes"],
            "last_refresh_error": state["last_refresh_error"]}


@app.get("/api/config")
async def config():
    return {
        "portfolio": [h.to_dict() for h in PORTFOLIO],
        "research_set": RESEARCH_SET,
        "categories": CATEGORIES,
        "default_always": sorted(digest_mod.DEFAULT_ALWAYS),
        "default_thresholds": Thresholds().__dict__,
        "llm": llm.status(),
        "push_public_key": notify.vapid_keys()["public"],
    }


@app.get("/api/digest")
async def get_digest(
    hours: float = Query(24, ge=1, le=24 * 90),
    price_pct: float = 4.0,
    vol_x: float = 2.0,
    lookback: int = 20,
    always: str | None = None,
    symbols: str | None = None,
):
    prefs = _prefs_from(
        {"price_pct": price_pct, "vol_x": vol_x, "lookback": lookback, "always": always,
         "symbols": [s for s in (symbols or "").split(",") if s.strip()] or None},
        hours=hours,
    )
    return await digest_mod.build(prefs)


@app.get("/api/enrich")
async def get_enrichment(id: str):
    reg = digest_mod.registered(id)
    if not reg:
        raise HTTPException(404, "unknown filing (open the digest first)")
    try:
        return await enrich_mod.enrich(reg["filing"], reg["ctx"])
    except Exception as exc:  # noqa: BLE001
        log.exception("enrich failed")
        return {"kind": "error", "filing_id": id, "note": f"Couldn't analyse this filing right now ({type(exc).__name__})."}


@app.get("/api/portfolio")
async def portfolio(symbols: str | None = None, lookback: int = 20):
    holdings = await digest_mod.resolve_holdings([s for s in (symbols or "").split(",") if s.strip()] or None)

    async def one(h):
        try:
            hx = await prices.history(h["symbol"], "1y")
        except Exception as exc:  # noqa: BLE001
            return {**h, "error": str(exc)[:120]}
        stats = session_stats(hx["bars"], lookback, hx.get("gaps"))
        last = stats[-1]
        complete = [s for s in stats if not s["provisional"]]
        lc = complete[-1] if complete else last
        closes = [b["close"] for b in hx["bars"]]
        hi, lo = max(closes[-250:]), min(closes[-250:])
        return {
            **h,
            "price": last["close"],
            "change_pct": last["change_pct"],
            "provisional": last["provisional"],
            "as_of": last["date"],
            "vol_ratio": lc["vol_ratio"],
            "spark": closes[-60:],
            "high_52w": hi,
            "low_52w": lo,
            "range_pos": round((last["close"] - lo) / (hi - lo) * 100) if hi > lo else None,
            "ret_1m": round((closes[-1] / closes[-22] - 1) * 100, 1) if len(closes) > 22 else None,
            "ret_1y": round((closes[-1] / closes[0] - 1) * 100, 1) if len(closes) > 200 else None,
            "price_source": hx["source"],
        }

    rows = await asyncio.gather(*[one(h) for h in holdings])
    day = sum((r.get("change_pct") or 0) * r.get("weight", 0) / 100 for r in rows if r.get("change_pct") is not None)
    return {"holdings": rows, "day_change_pct": round(day, 2)}


@app.get("/api/prices/{symbol}")
async def get_prices(symbol: str, range: str = "1y"):  # noqa: A002
    try:
        return await prices.history(symbol, range)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"price data unavailable: {exc}") from exc


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1, max_length=60)):
    try:
        return {"results": await screener.search(q)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"search unavailable: {exc}") from exc


@app.get("/api/research/{symbol}")
async def research(symbol: str, refresh: bool = False):
    if not symbol.strip() or len(symbol) > 30:
        raise HTTPException(400, "bad symbol")
    return engine.get_or_start(symbol, refresh=refresh)


class AskBody(BaseModel):
    symbol: str = Field(..., max_length=30)
    question: str = Field(..., min_length=3, max_length=400)


@app.post("/api/ask")
async def ask(body: AskBody):
    try:
        return await engine.ask(body.symbol, body.question)
    except llm.LLMUnavailable as exc:
        return {"answer": None, "note": f"Model unavailable right now: {str(exc)[:120]}"}
    except screener.CompanyNotFound:
        raise HTTPException(404, "company not found")


class SubscribeBody(BaseModel):
    subscription: dict
    prefs: dict = {}


@app.post("/api/push/subscribe")
async def push_subscribe(body: SubscribeBody):
    try:
        await notify.subscribe(body.subscription, body.prefs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


class EndpointBody(BaseModel):
    endpoint: str


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(body: EndpointBody):
    await notify.unsubscribe(body.endpoint)
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(body: EndpointBody):
    status = await notify.send(body.endpoint, {"title": "FINN is watching", "body": "You'll get a ping like this when something crosses your thresholds.", "url": "/", "tag": "finn-test"})
    if status >= 300:
        raise HTTPException(502, f"push service returned {status}")
    return {"ok": True}


@app.get("/api/usage")
async def usage():
    return {"llm": llm.usage_report(), "cache": cache.stats, "llm_status": llm.status()}


@app.get("/api/diag")
async def diag():
    """Which data sources and model providers this deployment can reach (for the README & debugging)."""
    async def probe(name, coro):
        t0 = time.time()
        try:
            r = await asyncio.wait_for(coro, 25)
            return name, {"ok": True, "ms": round((time.time() - t0) * 1000), "info": r}
        except Exception as exc:  # noqa: BLE001
            return name, {"ok": False, "ms": round((time.time() - t0) * 1000), "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    from .sources import bse, nse

    async def yahoo():
        h = await prices._yahoo("RELIANCE", "1mo")
        return f"{len(h['bars'])} bars, last {h['bars'][-1]['date']}"

    async def bse_ann():
        a = await bse._fetch_range("500325", prices.now_ist() - timedelta(days=10), prices.now_ist())
        return f"{len(a)} announcements"

    async def scr():
        p = await screener.company_page("INFY")
        return f"{p['name']}, {len(p['quarters']['keys'])} quarters, {len(p['documents']['concalls'])} concalls"

    async def nse_ann():
        a = await nse._fetch("RELIANCE", prices.now_ist() - timedelta(days=10), prices.now_ist())
        return f"{len(a)} announcements"

    async def models():
        return await llm.discover(force=True)

    async def bse_pdf():
        url = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/c9d3d96a-8b04-4b41-b454-3f98d1f90702.pdf"
        r = await http.request("GET", url, retries=0, timeout=30)
        return f"{len(r.content)} bytes, pdf={r.content[:4] == b'%PDF'}"

    async def bse_api_alt():
        r = await http.request("GET", "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w", retries=0,
                               params={"pageno": "1", "strCat": "-1", "strPrevDate": "20261020", "strScrip": "500325", "strSearch": "P", "strToDate": "20261027", "strType": "C", "subcategory": "-1"},
                               headers={"Accept": "application/json", "Referer": "https://www.bseindia.com/corporates/ann.html", "Origin": "https://www.bseindia.com",
                                        "sec-fetch-site": "same-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"})
        return r.text[:80]

    async def nse_pdf():
        rows = await nse._fetch("INFY", prices.now_ist() - timedelta(days=30), prices.now_ist())
        url = next((r["pdf_url"] for r in rows if r.get("pdf_url")), None)
        if not url:
            return "no pdf in sample"
        data = await nse.session.get_bytes(url)
        return f"{url[-50:]}: {len(data)} bytes, pdf={data[:4] == b'%PDF'}"

    async def nse_ar():
        ars = await nse.annual_reports("INFY")
        return f"{len(ars)} reports, latest {ars[0] if ars else None}"

    async def screener_quarter_pdf():
        r = await http.request("GET", "https://www.screener.in/company/source/quarter/1489/9/2026/", retries=0, timeout=30)
        return f"{r.url}"[:120] + f" {len(r.content)} bytes"

    results = await asyncio.gather(
        probe("yahoo", yahoo()), probe("bse", bse_ann()), probe("screener", scr()), probe("nse", nse_ann()), probe("llm", models()),
        probe("bse_pdf", bse_pdf()), probe("bse_api_alt", bse_api_alt()), probe("nse_pdf", nse_pdf()), probe("nse_annual_reports", nse_ar()),
        probe("screener_quarter_pdf", screener_quarter_pdf()),
    )
    return dict(results)


# --------------------------------------------------------------------------- static frontend

@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str, request: Request):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    root = Path(STATIC_DIR)
    target = (root / full_path).resolve()
    if full_path and target.is_file() and root.resolve() in target.parents:
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{full_path}" else {"Cache-Control": "no-cache"}
        return FileResponse(target, headers=headers)
    index = root / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse({"detail": "frontend not built"}, status_code=503, headers={"Cache-Control": "no-store"})
