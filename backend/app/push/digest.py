"""The windowed digest: every filing and unusual move across the portfolio, ranked so the few
things that matter come first and the routine paperwork is folded away."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import cache
from ..config import FILINGS_ORDER
from ..portfolio import BY_SYMBOL, PORTFOLIO
from ..sources import bse, nse, prices, screener
from ..sources.prices import IST, now_ist
from .alerts import Thresholds, alert_score, evaluate, session_stats
from .classify import CATEGORIES, classify, informative_detail, materiality, tier
from .enrich import apply_refinement, cached_enrichment

log = logging.getLogger("finn.digest")

DEFAULT_ALWAYS = {"results", "m_and_a", "management_change", "fund_raise", "litigation", "pledging"}
_SCREENER_SEM = asyncio.Semaphore(3)
_FETCH_SEM = asyncio.Semaphore(6)


@dataclass
class Prefs:
    hours: float = 24.0
    thresholds: Thresholds = field(default_factory=Thresholds)
    always: set[str] = field(default_factory=lambda: set(DEFAULT_ALWAYS))
    symbols: list[str] | None = None


# ------------------------------------------------------------------ holdings

async def resolve_holdings(symbols: list[str] | None) -> list[dict]:
    if not symbols:
        return [h.to_dict() for h in PORTFOLIO]
    syms = [screener.normalise_symbol(s) for s in symbols if s.strip()][:40]
    out: list[dict] = []

    async def one(sym: str) -> dict | None:
        if sym in BY_SYMBOL:
            return BY_SYMBOL[sym].to_dict()
        try:
            async with _SCREENER_SEM:
                ids = await screener.company_ids(sym)
        except Exception as exc:  # noqa: BLE001
            log.info("could not resolve %s: %s", sym, exc)
            return {"symbol": sym, "name": sym, "bse_code": "", "sector": "", "size": "", "weight": 0, "unresolved": True}
        page = await screener.company_page(sym)
        return {
            "symbol": ids.get("nse_symbol") or sym,
            "name": (ids.get("name") or sym).replace(" Ltd", ""),
            "bse_code": ids.get("bse_code") or "",
            "sector": (page.get("sector") or {}).get("Sector", ""),
            "size": "",
            "weight": 0,
        }

    for h in await asyncio.gather(*[one(s) for s in syms]):
        if h:
            out.append(h)
    if out and not any(h.get("weight") for h in out):
        w = round(100 / len(out), 1)
        for h in out:
            h["weight"] = w
    return out


async def _market_cap(symbol: str) -> float | None:
    try:
        async with _SCREENER_SEM:
            page = await screener.company_page(symbol)
        return (page.get("ratios") or {}).get("market_cap_cr")
    except Exception:  # noqa: BLE001
        return None


async def _from_bse(h: dict, days: int) -> list[dict]:
    if not h.get("bse_code"):
        raise RuntimeError("no BSE code")
    return await bse.announcements(h["bse_code"], days)


async def _from_nse(h: dict, days: int) -> list[dict]:
    return await nse.announcements(h["symbol"], days)


async def _from_screener(h: dict, days: int) -> list[dict]:
    page = await screener.company_page(h["symbol"])
    out = []
    for a in (page.get("documents") or {}).get("announcements") or []:
        out.append({
            "id": f"SCR:{h['symbol']}:{a['url'][-40:]}", "source": "screener", "bse_code": "", "company": h["name"],
            "time": a.get("time"), "subject": a["title"], "raw_subject": a["title"], "detail": "",
            "exchange_category": "", "exchange_subcategory": "", "critical": False, "pdf_url": a["url"],
            "attachment": a["url"], "pdf_size": None,
        })
    return out


_SOURCES = {"nse": _from_nse, "bse": _from_bse, "screener": _from_screener}
_failures: dict[str, float] = {}


def _order() -> list[str]:
    """Configured order, but a source that failed in the last 10 minutes goes to the back."""
    base = [s for s in FILINGS_ORDER if s in _SOURCES] or ["nse", "bse"]
    if "screener" not in base:
        base.append("screener")
    now = time.time()
    healthy = [s for s in base if now - _failures.get(s, 0) > 600]
    return healthy + [s for s in base if s not in healthy]


async def _filings_for(h: dict, days: int) -> tuple[list[dict], str | None]:
    errors = []
    async with _FETCH_SEM:
        for name in _order():
            try:
                rows = await _SOURCES[name](h, days)
                if name != "screener" or rows:
                    return rows, ("; ".join(errors) or None)
            except Exception as exc:  # noqa: BLE001 - try the next source
                if name != "screener" and "no BSE code" not in str(exc):
                    _failures[name] = time.time()
                errors.append(f"{name}: {str(exc)[:120]}")
    return [], "; ".join(errors)


# ------------------------------------------------------------------ helpers

_DATE_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})")
_LONG_DATE_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december),?\s+(\d{4})", re.I)
_MONTHS = {m: i for i, m in enumerate(["", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}


_MON = {m: i for i, m in enumerate(["", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DMY_RE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})")


def meeting_date(text: str) -> str | None:
    m3 = _DMY_RE.search(text)
    if m3 and m3.group(2).lower() in _MON:
        try:
            return date(int(m3.group(3)), _MON[m3.group(2).lower()], int(m3.group(1))).isoformat()
        except ValueError:
            pass
    m = re.search(r"scheduled (?:on|for)\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.I) or _DATE_RE.search(text)
    try:
        if m:
            d, mo, y = (int(x) for x in re.split(r"[/\-.]", m.group(1) if m.lastindex == 1 else m.group(0)))
            return date(y, mo, d).isoformat()
        m2 = _LONG_DATE_RE.search(text)
        if m2:
            return date(int(m2.group(3)), _MONTHS[m2.group(2).lower()], int(m2.group(1))).isoformat()
    except ValueError:
        return None
    return None


def _parse_time(t: str | None) -> datetime | None:
    if not t:
        return None
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def short_label(item: dict) -> str:
    name = item.get("name") or item.get("symbol")
    if item["type"] == "move":
        return f"{name} {item['change_pct']:+.1f}%"
    cat = item["category"]
    if cat == "results":
        return f"{name} results"
    if cat == "management_change":
        role = {"top": "top-level", "auditor": "auditor"}.get(item.get("subtype") or "", "")
        return f"{name} {role + ' ' if role else ''}management change".replace("  ", " ")
    return f"{name}: {CATEGORIES.get(cat, cat).lower()}"


# ------------------------------------------------------------------ build

async def build(prefs: Prefs) -> dict:
    t = prefs.thresholds.clamp()
    hours = max(1.0, min(prefs.hours, 24 * 90))
    now = now_ist()
    start = now - timedelta(hours=hours)
    days = max(30, math.ceil(hours / 24) + 1)
    holdings = await resolve_holdings(prefs.symbols)

    filings_res, mcaps, hist = await asyncio.gather(
        asyncio.gather(*[_filings_for(h, days) for h in holdings]),
        asyncio.gather(*[_market_cap(h["symbol"]) for h in holdings]),
        asyncio.gather(*[_safe_history(h["symbol"]) for h in holdings]),
    )
    source_errors = {}
    items: list[dict] = []
    upcoming: list[dict] = []
    per_symbol_filings: dict[str, list[dict]] = defaultdict(list)

    for h, (filings, err), mcap in zip(holdings, filings_res, mcaps):
        if err and not filings:
            source_errors[h["symbol"]] = err
        seen: set[tuple] = set()
        for f in filings:
            ft = _parse_time(f.get("time"))
            if ft is None:
                continue
            c = classify(f)
            f2 = dict(f)
            score, why = materiality(f2, c, mcap)
            item = {
                "type": "filing",
                "id": f["id"],
                "symbol": h["symbol"],
                "name": h["name"],
                "weight": h.get("weight", 0),
                "sector": h.get("sector"),
                "time": ft.isoformat(),
                "subject": f.get("subject"),
                "detail": f.get("detail") if informative_detail(f.get("detail", "")) else None,
                "pdf_url": f.get("pdf_url"),
                "source": f.get("source"),
                "exchange_category": f.get("exchange_subcategory") or f.get("exchange_category"),
                "category": c.category,
                "category_label": CATEGORIES[c.category],
                "subtype": c.subtype,
                "needs_model": c.needs_model,
                "score": score,
                "why": why,
                "amount_cr": f2.get("amount_cr"),
                "amount_pct_mcap": f2.get("amount_pct_mcap"),
                "market_cap_cr": mcap,
            }
            # Upcoming results dates come from board-meeting intimations (often filed weeks earlier).
            if c.category == "results" and c.subtype == "date":
                md = meeting_date(f"{f.get('subject', '')} {f.get('detail', '')}")
                if md and md >= now.date().isoformat():
                    upcoming.append({"symbol": h["symbol"], "name": h["name"], "date": md, "filing_id": f["id"], "weight": h.get("weight", 0)})
            dedupe = (item["subject"], ft.strftime("%Y%m%d%H%M"))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            _register(item, f, h)
            card = cached_enrichment(item["id"])
            apply_refinement(item, card)
            if card:
                item["enrichment"] = card
            item["tier"] = tier(item["score"])
            per_symbol_filings[h["symbol"]].append(item)
            if ft >= start:
                items.append(item)

    # Watched categories: never let them be folded away (except their ancillary paperwork).
    for it in items:
        it["watched"] = it["category"] in prefs.always
        if it["watched"] and it["tier"] == "routine" and it.get("subtype") not in {
            "ancillary", "date", "newspaper", "cs", "record_date", "buyback_daily", "notice", "deck", "board_meeting", "allotment",
        }:
            it["score"] = max(it["score"], 40)
            it["tier"] = "notable"
        it["rank"] = round(it["score"] + it["weight"] * 0.8, 1)

    items = _bundle_management(_bundle_results(items))

    # Price & volume moves.
    moves = []
    for h, hx in zip(holdings, hist):
        if not hx:
            continue
        stats = session_stats(hx["bars"], t.lookback, hx.get("gaps"))
        sessions = _sessions_in_window(stats, start, hours)
        for s in sessions:
            hits = evaluate(s, t)
            if not hits:
                continue
            score = alert_score(s, hits, t, h.get("weight", 0))
            driver = _driver(per_symbol_filings.get(h["symbol"], []), s["date"])
            moves.append(
                {
                    "type": "move",
                    "id": f"MOVE:{h['symbol']}:{s['date']}",
                    "symbol": h["symbol"],
                    "name": h["name"],
                    "weight": h.get("weight", 0),
                    "time": f"{s['date']}T15:30:00+05:30",
                    "date": s["date"],
                    "close": s["close"],
                    "change_pct": s["change_pct"],
                    "volume": s["volume"],
                    "avg_volume": s["avg_volume"],
                    "vol_ratio": s["vol_ratio"],
                    "provisional": s["provisional"],
                    "hits": hits,
                    "score": score,
                    "tier": tier(score),
                    "rank": score,
                    "driver": driver,
                    "since": s.get("since"),
                    "spark": [b["close"] for b in hx["bars"][-30:]],
                    "impact_bps": round((s["change_pct"] or 0) * h.get("weight", 0), 1),
                }
            )

    moves = _collapse_52w(moves)
    try:
        cal = await nse.event_calendar(30)
        syms = {h["symbol"]: h for h in holdings}
        for ev in cal:
            h = syms.get(ev["symbol"])
            if h and ev["date"] >= now.date().isoformat():
                upcoming.append({"symbol": h["symbol"], "name": h["name"], "date": ev["date"], "filing_id": None, "weight": h.get("weight", 0),
                                 "purpose": ev["purpose"]})
    except Exception as exc:  # noqa: BLE001
        log.info("event calendar unavailable: %s", exc)

    everything = items + moves
    everything.sort(key=lambda x: (-{"high": 2, "notable": 1, "routine": 0}[x["tier"]], -x["rank"], x["time"]), reverse=False)
    top = [x for x in everything if x["tier"] == "high"]
    notable = [x for x in everything if x["tier"] == "notable"]
    routine_items = [x for x in items if x["tier"] == "routine"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for x in routine_items:
        groups[x["category_label"]].append(x)
    routine = sorted(
        ({"label": k, "count": len(v), "items": sorted(v, key=lambda x: x["time"], reverse=True)} for k, v in groups.items()),
        key=lambda g: -g["count"],
    )
    active = {x["symbol"] for x in everything}
    quiet = [h["name"] for h in holdings if h["symbol"] not in active]
    upcoming = sorted({(u["symbol"], u["date"]): u for u in upcoming}.values(), key=lambda u: (u["date"], -u["weight"]))[:12]
    alerts = [x for x in top + notable if x["type"] == "move" or (x.get("watched") and x["tier"] == "high")]

    headline = _headline(top, len(routine_items), len(items))
    return {
        "generated_at": now.isoformat(),
        "window": {"hours": hours, "from": start.isoformat(), "to": now.isoformat()},
        "thresholds": t.__dict__,
        "always": sorted(prefs.always),
        "headline": headline,
        "counts": {
            "filings": len(items),
            "high": sum(1 for x in top if x["type"] == "filing"),
            "notable": sum(1 for x in notable if x["type"] == "filing"),
            "routine": len(routine_items),
            "moves": len(moves),
            "holdings": len(holdings),
        },
        "top": top,
        "notable": notable,
        "routine": routine,
        "upcoming": upcoming,
        "alerts": [a["id"] for a in alerts],
        "quiet": quiet,
        "source_errors": source_errors,
    }


def _collapse_52w(moves: list[dict]) -> list[dict]:
    """A stock sitting at its 52-week low all week is one fact, not five alerts: keep the latest."""
    only_52w = lambda m: all(h["kind"] in ("high_52w", "low_52w") for h in m["hits"])  # noqa: E731
    latest: dict[tuple, dict] = {}
    counts: dict[tuple, int] = defaultdict(int)
    rest = []
    for m in moves:
        if only_52w(m):
            key = (m["symbol"], m["hits"][0]["kind"])
            counts[key] += 1
            if key not in latest or m["date"] > latest[key]["date"]:
                latest[key] = m
        else:
            rest.append(m)
    for key, m in latest.items():
        if counts[key] > 1:
            m["hits"] = [{**m["hits"][0], "label": f"{m['hits'][0]['label']} ({counts[key]} sessions in this window)"}]
        rest.append(m)
    return rest


def _bundle_management(items: list[dict]) -> list[dict]:
    """A CFO's exit and their successor's appointment are one event: one card per company."""
    by_sym: dict[str, list[dict]] = defaultdict(list)
    rest = []
    for it in items:
        if it["category"] == "management_change" and it["tier"] != "routine":
            by_sym[it["symbol"]].append(it)
        else:
            rest.append(it)
    for sym, group in by_sym.items():
        group.sort(key=lambda g: (-g["score"], g["time"]))
        main = group[0]
        if len(group) > 1:
            main["related"] = [
                {"id": g["id"], "subject": g.get("detail") or g["subject"], "time": g["time"], "pdf_url": g.get("pdf_url"), "category_label": g["category_label"]}
                for g in group[1:]
            ]
            main["score"] = min(100, main["score"] + 2)
            main["rank"] = round(main["score"] + main["weight"] * 0.8, 1)
        rest.append(main)
    return rest


def _bundle_results(items: list[dict]) -> list[dict]:
    """One results card per company per window; the deck/press release/newspaper ad hang off it."""
    by_sym: dict[str, list[dict]] = defaultdict(list)
    rest = []
    for it in items:
        if it["category"] == "results" and it.get("subtype") in ("filing", "outcome", "ancillary", "deck"):
            by_sym[it["symbol"]].append(it)
        else:
            rest.append(it)
    for sym, group in by_sym.items():
        primaries = [g for g in group if g.get("subtype") in ("filing", "outcome")]
        if not primaries:
            rest.extend(group)
            continue
        primaries.sort(key=lambda g: (0 if g.get("subtype") == "filing" else 1, g["time"]))
        main = primaries[0]
        main["related"] = [
            {"id": g["id"], "subject": g["subject"], "time": g["time"], "pdf_url": g.get("pdf_url"), "category_label": g["category_label"]}
            for g in group if g is not main
        ]
        rest.append(main)
    return rest


def _sessions_in_window(stats: list[dict], start: datetime, hours: float) -> list[dict]:
    if not stats:
        return []
    if hours <= 72:
        # Short windows always show the latest completed session (so a Monday-morning 24h view still
        # reports Friday's close), plus today's provisional bar if the market is open.
        complete = [s for s in stats if not s["provisional"]]
        # ...but never a session older than three days (a stale bar from a data hole is not news).
        oldest = (start - timedelta(hours=max(0, 72 - hours))).date().isoformat()
        out = [s for s in complete[-1:] if s["date"] >= oldest]
        if stats[-1]["provisional"]:
            out.append(stats[-1])
        return out
    d0 = start.date().isoformat()
    return [s for s in stats if s["date"] >= d0]


def _driver(filings: list[dict], session_date: str) -> dict | None:
    """The most material filing between the previous close and this session's close."""
    try:
        d = date.fromisoformat(session_date)
    except ValueError:
        return None
    lo = datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST) - timedelta(days=1)
    # Walk back over weekends: a Monday move can be driven by a Saturday filing.
    while lo.weekday() >= 5:
        lo -= timedelta(days=1)
    hi = datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST)
    cands = [f for f in filings if (ft := _parse_time(f["time"])) and lo <= ft <= hi and f["score"] >= 30]
    if not cands:
        return None
    best = max(cands, key=lambda f: f["score"])
    return {"id": best["id"], "subject": best["subject"], "category_label": best["category_label"], "time": best["time"]}


def _headline(top: list[dict], routine_count: int, total: int) -> str:
    if not top:
        if total == 0:
            return "Quiet window: no filings from your holdings."
        return f"Nothing needs you. {routine_count} routine filing{'s' if routine_count != 1 else ''} folded away."
    labels = [short_label(x) for x in top[:3]]
    more = f" +{len(top) - 3} more" if len(top) > 3 else ""
    n = len(top)
    return f"{n} thing{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you: " + " · ".join(labels) + more


async def _safe_history(symbol: str) -> dict | None:
    try:
        return await prices.history(symbol, "1y")
    except Exception as exc:  # noqa: BLE001
        log.info("no prices for %s: %s", symbol, exc)
        return None


def _register(item: dict, raw: dict, holding: dict) -> None:
    """Remember enough about each filing to enrich it later by id."""
    cache.put(
        f"filing:{item['id']}",
        {
            "filing": {**raw, "category": item["category"], "subtype": item["subtype"]},
            "ctx": {"symbol": holding["symbol"], "name": holding["name"], "market_cap_cr": item.get("market_cap_cr"), "weight": holding.get("weight")},
        },
    )


def registered(filing_id: str) -> dict | None:
    return cache.get(f"filing:{filing_id}", cache.FOREVER)
