"""Turn a material filing into something an investor can act on in five seconds.

* Results            -> headline numbers (Revenue, EBITDA, PBT, PAT) QoQ and YoY, read from the
                        actual results filing and cross-checked against screener's table.
* Management change  -> who is coming/going, plus a short professional background of the
                        incoming person (filing's own disclosed profile + web search).
* Everything else    -> one specific line, key facts and a "so what".
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from urllib.parse import quote_plus

from .. import cache
from ..llm import prompts
from ..llm.router import LLMUnavailable, available, complete
from ..sources import pdfs, screener
from .classify import CATEGORIES

log = logging.getLogger("finn.enrich")

UNIT_TO_CRORE = {
    "crore": 1.0, "crores": 1.0, "cr": 1.0,
    "lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01,
    "million": 0.1, "millions": 0.1, "mn": 0.1,
    "billion": 100.0, "bn": 100.0,
    "thousand": 0.0001, "thousands": 0.0001,
    "rupees": 1e-7, "rs": 1e-7, "inr": 1e-7,
}


def unit_factor(unit: str | None) -> float | None:
    if not unit:
        return None
    u = unit.lower()
    for k in sorted(UNIT_TO_CRORE, key=len, reverse=True):
        if re.search(rf"\b{k}\b", u):
            return UNIT_TO_CRORE[k]
    return None


def pct_change(cur: float | None, base: float | None) -> float | None:
    if cur is None or base is None or base <= 0:
        return None
    return round((cur / base - 1) * 100, 1)


def headline_from_columns(col: dict, factor: float, is_bank: bool) -> dict:
    """Revenue / EBITDA (or pre-provision profit for lenders) / PBT / PAT in ₹ crore."""
    def g(k):
        v = col.get(k)
        return None if v is None else float(v) * factor

    pbt = g("profit_before_tax")
    pat = g("net_profit_owners") if col.get("net_profit_owners") is not None else g("net_profit")
    if is_bank:
        revenue = g("total_income") or ((g("interest_earned") or 0) + (g("other_income") or 0)) or None
        ebitda = g("operating_profit_before_provisions")
        if ebitda is None and pbt is not None and g("provisions") is not None:
            ebitda = pbt + g("provisions")
    else:
        revenue = g("revenue_from_operations")
        ebitda = None
        if pbt is not None and g("finance_costs") is not None and g("depreciation") is not None:
            pbt_pre_exc = pbt - (g("exceptional_items") or 0.0)
            ebitda = pbt_pre_exc + g("finance_costs") + g("depreciation") - (g("other_income") or 0.0)
    return {"revenue": revenue, "ebitda": ebitda, "pbt": pbt, "pat": pat}


def build_metrics(cur: dict, prev: dict, yoy: dict, is_bank: bool) -> tuple[list[dict], dict]:
    labels = [
        ("revenue", "Revenue" if not is_bank else "Total income"),
        ("ebitda", "EBITDA" if not is_bank else "Pre-provision profit"),
        ("pbt", "PBT"),
        ("pat", "PAT"),
    ]
    out = []
    for key, label in labels:
        c = cur.get(key)
        out.append(
            {
                "key": key,
                "label": label,
                "value": None if c is None else round(c, 1),
                "prev": None if prev.get(key) is None else round(prev[key], 1),
                "year_ago": None if yoy.get(key) is None else round(yoy[key], 1),
                "qoq": pct_change(c, prev.get(key)),
                "yoy": pct_change(c, yoy.get(key)),
            }
        )
    rev = cur.get("revenue")
    margins = {}
    if rev and cur.get("ebitda") is not None:
        m = cur["ebitda"] / rev * 100
        margins["ebitda_margin"] = round(m, 1)
        if prev.get("revenue") and prev.get("ebitda") is not None:
            margins["ebitda_margin_qoq_bps"] = round((m - prev["ebitda"] / prev["revenue"] * 100) * 100)
        if yoy.get("revenue") and yoy.get("ebitda") is not None:
            margins["ebitda_margin_yoy_bps"] = round((m - yoy["ebitda"] / yoy["revenue"] * 100) * 100)
    if rev and cur.get("pat") is not None:
        margins["pat_margin"] = round(cur["pat"] / rev * 100, 1)
    return out, margins


def expected_quarter_end(filed_at: str | None) -> str:
    """The quarter a results filing most likely reports: the last quarter-end before the filing date."""
    try:
        d = datetime.fromisoformat(filed_at).date() if filed_at else date.today()
    except ValueError:
        d = date.today()
    for y, m, dd in ((d.year, 12, 31), (d.year, 9, 30), (d.year, 6, 30), (d.year, 3, 31), (d.year - 1, 12, 31)):
        qe = date(y, m, dd)
        if qe < d:
            return qe.isoformat()
    return date(d.year - 1, 12, 31).isoformat()


def screener_headline(page: dict, quarter_end: str) -> dict | None:
    """Headline numbers from screener's standardised quarterly table (used to verify / fall back)."""
    q = page.get("quarters") or {}
    keys = q.get("keys") or []
    if quarter_end not in keys:
        return None
    i = keys.index(quarter_end)
    rows = q.get("rows") or {}
    bank = page.get("is_bank")

    def col(j):
        if j < 0:
            return {}
        rev = (rows.get("Sales") or rows.get("Revenue") or [None] * len(keys))[j]
        ebitda = (rows.get("Financing Profit") if bank else rows.get("Operating Profit")) or [None] * len(keys)
        return {
            "revenue": rev,
            "ebitda": ebitda[j],
            "pbt": (rows.get("Profit before tax") or [None] * len(keys))[j],
            "pat": (rows.get("Net Profit") or [None] * len(keys))[j],
        }

    return {"current": col(i), "prev": col(i - 1), "yoy": col(i - 4), "period": quarter_end}


async def _filing_material(filing: dict, max_pages: int, pick=None) -> tuple[str | None, bytes | None, list[int]]:
    """(text with page markers, or trimmed PDF for vision, pages used)."""
    url = filing.get("pdf_url")
    if not url:
        return None, None, []
    data = await pdfs.fetch_pdf(url)
    pages = await asyncio.to_thread(pdfs._extract, data, 60)
    if pdfs.has_text_layer(pages[: max(6, max_pages)]):
        idx = pick(pages) if pick else list(range(min(max_pages, len(pages))))
        return pdfs.numbered_text(pages, idx, 40_000), None, idx
    n = len(pages) or pdfs.page_count(data)
    idx = list(range(min(max_pages, n)))
    return None, await asyncio.to_thread(pdfs.trim_pdf, data, idx), idx


# --------------------------------------------------------------------------- results

def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def check_identities(col: dict) -> list[str]:
    """Accounting identities a correctly-read P&L column satisfies. A read that mixes lines from
    different statements (standalone vs consolidated, quarter vs half-year) almost always breaks one."""
    problems = []
    rev, oi, ti = _num(col.get("revenue_from_operations")), _num(col.get("other_income")), _num(col.get("total_income"))
    te, pbt, exc = _num(col.get("total_expenses")), _num(col.get("profit_before_tax")), _num(col.get("exceptional_items")) or 0.0
    assoc = _num(col.get("share_of_associates")) or 0.0
    pat = _num(col.get("net_profit"))
    if rev is not None and oi is not None and ti and ti > 0 and abs(rev + oi - ti) / ti > 0.015:
        problems.append("revenue + other income != total income")
    if ti and te is not None and pbt is not None and ti > 0:
        # Exceptional items and associates' share sit between total expenses and PBT; their signs vary
        # by presentation, so accept any of the four combinations.
        gaps = [abs(ti - te + a * exc + b * assoc - pbt) for a in (1, -1) for b in (1, -1)]
        if min(gaps) / ti > 0.02:
            problems.append("total income - expenses != PBT")
    if pbt is not None and pat is not None and pbt > 0 and pat > pbt * 1.25 + abs(exc):
        problems.append("PAT far above PBT")
    if rev is not None and pbt is not None and rev > 0 and pbt > rev:
        problems.append("PBT above revenue")
    return problems


def _validate_results(d):
    cols = (d or {}).get("columns") or {}
    cur = cols.get("current") or {}
    if not any(isinstance(cur.get(k), (int, float)) for k in ("revenue_from_operations", "interest_earned", "total_income")):
        raise ValueError("no revenue in current column")
    if not any(isinstance(cur.get(k), (int, float)) for k in ("profit_before_tax", "net_profit")):
        raise ValueError("no profit in current column")
    for name in ("current", "previous_quarter", "year_ago_quarter"):
        probs = check_identities(cols.get(name) or {})
        if probs:
            raise ValueError(f"{name} column fails accounting identities: {', '.join(probs)}")


async def results_card(filing: dict, ctx: dict) -> dict:
    symbol = ctx.get("symbol")
    page = None
    try:
        page = await screener.company_page(symbol) if symbol else None
    except Exception as exc:  # noqa: BLE001
        log.info("screener unavailable for %s: %s", symbol, exc)
    qe = expected_quarter_end(filing.get("time"))
    card: dict = {"kind": "results", "quarter": screener.fy_quarter(qe), "period_end": qe, "source": None}
    extracted = None
    if available() and filing.get("pdf_url"):
        try:
            text, pdf_bytes, used = await _filing_material(
                filing, 10, pick=lambda pages: pdfs.pick_results_pages(pages, 2)
            )
            res = await complete(
                "results_extract",
                system=prompts.RESULTS_SYSTEM,
                prompt=prompts.RESULTS_PROMPT,
                document=text or "",
                pdf=pdf_bytes,
                tier="fast",
                max_tokens=4096,
                validate=_validate_results,
            )
            extracted = res.data
            card["llm"] = res.to_meta()
            card["pages"] = [p + 1 for p in used]
        except (LLMUnavailable, pdfs.PdfError) as exc:
            card["note"] = f"Couldn't read the filing ({str(exc)[:120]})."
        except Exception as exc:  # noqa: BLE001
            log.warning("results extraction failed for %s: %s", filing.get("id"), exc)
            card["note"] = "Couldn't read the filing."
    if extracted:
        factor = unit_factor(extracted.get("unit")) or 1.0
        bank = bool(extracted.get("is_bank")) or bool(page and page.get("is_bank"))
        cols = extracted.get("columns") or {}
        cur = headline_from_columns(cols.get("current") or {}, factor, bank)
        prev = headline_from_columns(cols.get("previous_quarter") or {}, factor, bank)
        yoy = headline_from_columns(cols.get("year_ago_quarter") or {}, factor, bank)
        period = ((extracted.get("periods") or {}).get("current")) or qe
        if re.match(r"\d{4}-\d{2}-\d{2}$", str(period)):
            card["period_end"] = period
            card["quarter"] = screener.fy_quarter(period)
        metrics, margins = build_metrics(cur, prev, yoy, bank)
        card.update(
            {"metrics": metrics, "margins": margins, "basis": extracted.get("basis"), "is_bank": bank,
             "source": "results filing", "unit_reported": extracted.get("unit")}
        )
        # Cross-check against screener's standardised table when it already has the quarter.
        if page:
            sh = screener_headline(page, card["period_end"])
            if sh and sh["current"].get("revenue") and cur.get("revenue"):
                diff = abs(sh["current"]["revenue"] - cur["revenue"]) / sh["current"]["revenue"] * 100
                pdiff = None
                if sh["current"].get("pbt") and cur.get("pbt"):
                    pdiff = abs(sh["current"]["pbt"] - cur["pbt"]) / abs(sh["current"]["pbt"]) * 100
                ok = diff <= 2.5 and (pdiff is None or pdiff <= 3)
                card["verified"] = {"against": "screener.in", "revenue_diff_pct": round(diff, 2), "ok": ok}
                if not ok:
                    # Our read of the filing disagrees with the exchange-sourced standardised numbers:
                    # show those instead, and say so.
                    metrics, margins = build_metrics(sh["current"], sh["prev"], sh["yoy"], bank)
                    if bank:
                        for m in metrics:
                            if m["key"] == "ebitda":
                                m["label"] = "Financing profit"
                    card.update({"metrics": metrics, "margins": margins, "source": "screener.in standardised table",
                                 "filing_read_rejected": f"FINN's read of the filing differed from screener by {diff:.1f}% on revenue"})
                    card["verified"] = {"against": "results filing", "revenue_diff_pct": round(diff, 2), "ok": False}
        return card
    # Fallback: screener's table, if it already carries the quarter this filing reports.
    if page:
        sh = screener_headline(page, qe)
        if sh:
            bank = bool(page.get("is_bank"))
            metrics, margins = build_metrics(sh["current"], sh["prev"], sh["yoy"], bank)
            card.update({"metrics": metrics, "margins": margins, "basis": page.get("basis"), "is_bank": bank,
                         "source": "screener.in standardised table"})
            if bank:  # screener's "operating profit" for non-banks is sales minus expenses, i.e. EBITDA
                for m in metrics:
                    if m["key"] == "ebitda":
                        m["label"] = "Financing profit"
            return card
    card["source"] = None
    card.setdefault("note", "Numbers not available yet.")
    return card


# --------------------------------------------------------------------------- management change

TOP_BUCKETS = {"ceo_md": 0, "cfo": 1, "chair": 2, "executive_director": 3, "independent_director": 5, "non_executive_director": 6, "other_kmp": 4}


def linkedin_search(name: str, company: str) -> str:
    return f"https://www.linkedin.com/search/results/all/?keywords={quote_plus(f'{name} {company}')}"


def _bio_result(d: dict, sources: list) -> dict:
    if not d.get("found"):
        return {"found": False, "sources": sources[:3]}
    li = str(d.get("linkedin_url") or "")
    return {
        "found": True,
        "headline": d.get("headline"),
        "background": d.get("background"),
        "previous_roles": [r for r in (d.get("previous_roles") or []) if isinstance(r, dict)][:4],
        "linkedin_url": li if li.startswith("https://") and "linkedin.com/" in li else None,
        "confidence": d.get("confidence"),
        "sources": sources[:4],
    }


async def person_background(name: str, role: str, company: str, profile: str | None) -> dict | None:
    """Professional background of an incoming executive.

    1. Gemini with Google Search grounding (when the key's quota allows it).
    2. Otherwise: a keyless web search (LinkedIn-first) whose results are summarised by the fast model,
       which may only use results that clearly match the person.
    The company's own disclosed profile (SEBI requires one for appointments) is always shown alongside.
    """
    if not available():
        return None
    try:
        res = await complete(
            "person_bio",
            system=prompts.BIO_SYSTEM,
            prompt=prompts.BIO_PROMPT.format(name=name, role=role, company=company, profile=profile or "(none)"),
            tier="fast",
            search=True,
            max_tokens=1200,
            temperature=0.2,
        )
        d = res.data if isinstance(res.data, dict) else {}
        if d:
            return _bio_result(d, res.sources)
    except LLMUnavailable as exc:
        log.info("grounded search unavailable (%s); falling back to web results", str(exc)[:120])
    from ..sources import websearch

    try:
        results = await websearch.search(f'"{name}" {company} linkedin', 6)
        if len(results) < 3:
            results += [r for r in await websearch.search(f"{name} {role} {company}", 6) if r not in results]
    except Exception as exc:  # noqa: BLE001
        log.info("web search failed: %s", exc)
        return None
    if not results:
        return {"found": False, "sources": []}
    listing = "\n".join(f"[{i}] {r['title']} | {r['url']} | {r['snippet']}" for i, r in enumerate(results[:8]))
    try:
        res = await complete(
            "person_bio_from_results",
            system=prompts.BIO_FROM_RESULTS_SYSTEM,
            prompt=prompts.BIO_FROM_RESULTS_PROMPT.format(name=name, role=role, company=company, profile=profile or "(none)", results=listing),
            tier="fast",
            max_tokens=900,
            temperature=0.1,
        )
    except LLMUnavailable:
        return None
    d = res.data if isinstance(res.data, dict) else {}
    used = [results[i] for i in (d.get("used_results") or []) if isinstance(i, int) and 0 <= i < len(results)]
    return _bio_result(d, [{"title": _domain(r["url"]), "url": r["url"]} for r in used])


def _domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else "source"


async def management_card(filing: dict, ctx: dict) -> dict:
    company = ctx.get("name") or filing.get("company")
    card: dict = {"kind": "management", "changes": [], "summary": None}
    if not available():
        card["summary"] = filing.get("detail") or filing.get("subject")
        return card
    try:
        text, pdf_bytes, _ = await _filing_material(filing, 6)
    except pdfs.PdfError as exc:
        text, pdf_bytes = None, None
        card["note"] = str(exc)[:120]
    try:
        res = await complete(
            "mgmt_extract",
            system=prompts.MGMT_SYSTEM,
            prompt=prompts.MGMT_PROMPT.format(company=company, subject=filing.get("subject"), detail=filing.get("detail")),
            document=text or (filing.get("detail") or ""),
            pdf=pdf_bytes,
            tier="fast",
            max_tokens=2048,
        )
    except LLMUnavailable as exc:
        card["summary"] = filing.get("detail") or filing.get("subject")
        card["note"] = f"Model unavailable ({str(exc)[:80]})"
        return card
    d = res.data if isinstance(res.data, dict) else {}
    card["llm"] = res.to_meta()
    card["summary"] = d.get("summary")
    changes = [c for c in (d.get("changes") or []) if isinstance(c, dict) and c.get("name")]
    incoming = [c for c in changes if c.get("change") in ("appointed", "re-designated") and c.get("role_bucket") not in ("company_secretary", "auditor")]
    incoming.sort(key=lambda c: TOP_BUCKETS.get(c.get("role_bucket"), 9))
    bios = await asyncio.gather(
        *[person_background(c["name"], c.get("role") or "", company, c.get("profile_from_filing")) for c in incoming[:2]]
    )
    bio_by_name = {c["name"]: b for c, b in zip(incoming[:2], bios)}
    for c in changes:
        c["linkedin_search"] = linkedin_search(c["name"], company)
        c["background"] = bio_by_name.get(c["name"])
    card["changes"] = changes[:6]
    return card


# --------------------------------------------------------------------------- everything else

def _validate_summary(d):
    if not isinstance(d, dict) or not str(d.get("one_liner") or "").strip():
        raise ValueError("missing one_liner")


async def summary_card(filing: dict, ctx: dict) -> dict:
    card: dict = {"kind": "summary"}
    if not available():
        card["one_liner"] = filing.get("detail") or filing.get("subject")
        return card
    text, pdf_bytes = None, None
    try:
        text, pdf_bytes, _ = await _filing_material(filing, 3)
    except pdfs.PdfError:
        pass
    mcap = ctx.get("market_cap_cr")
    try:
        res = await complete(
            "filing_summary",
            system=prompts.SUMMARY_SYSTEM,
            prompt=prompts.SUMMARY_PROMPT.format(
                company=ctx.get("name") or filing.get("company"),
                mcap=f"{mcap:,.0f}" if mcap else "unknown",
                subject=filing.get("subject"),
                detail=filing.get("detail"),
                category=filing.get("category"),
                categories=prompts.CATEGORY_LIST,
            ),
            document=text or "",
            pdf=pdf_bytes,
            tier="fast",
            max_tokens=1024,
            validate=_validate_summary,
        )
    except LLMUnavailable as exc:
        card["one_liner"] = filing.get("detail") or filing.get("subject")
        card["note"] = f"Model unavailable ({str(exc)[:80]})"
        return card
    d = res.data
    card.update(
        {
            "one_liner": d.get("one_liner"),
            "key_facts": [kf for kf in (d.get("key_facts") or []) if isinstance(kf, dict)][:3],
            "so_what": d.get("so_what"),
            "category": d.get("category") if d.get("category") in CATEGORIES else None,
            "materiality": d.get("materiality") if isinstance(d.get("materiality"), int) else None,
            "llm": res.to_meta(),
        }
    )
    return card


# --------------------------------------------------------------------------- dispatcher

def enrichment_key(filing_id: str) -> str:
    return f"enrich:v2:{filing_id}"


_inflight: dict[str, asyncio.Task] = {}


async def _compute(filing: dict, ctx: dict) -> dict:
    cat, sub = filing.get("category"), filing.get("subtype")
    if cat == "results" and sub in ("filing", "outcome"):
        card = await results_card(filing, ctx)
    elif cat == "management_change":
        card = await management_card(filing, ctx)
    else:
        card = await summary_card(filing, ctx)
        if card.get("category") == "results" and sub in ("board_outcome", None) and filing.get("pdf_url"):
            # A bare "outcome of board meeting" that turns out to carry the results: read the numbers.
            res = await results_card({**filing, "category": "results", "subtype": "outcome"}, ctx)
            if res.get("metrics"):
                res["one_liner"] = card.get("one_liner")
                res["promoted_from"] = "board outcome"
                card = res
    card["filing_id"] = filing["id"]
    return card


def cached_enrichment(filing_id: str) -> dict | None:
    key = enrichment_key(filing_id)
    return cache.get(key, cache.FOREVER) or cache.get(key + ":degraded", 600)


async def enrich(filing: dict, ctx: dict) -> dict:
    """Enrichment for one filing. Good answers are cached forever (filings never change);
    degraded answers (model or PDF unavailable) are kept for 10 minutes, then retried."""
    key = enrichment_key(filing["id"])
    hit = cached_enrichment(filing["id"])
    if hit is not None:
        return hit
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(_compute(filing, ctx))
        _inflight[key] = task
        task.add_done_callback(lambda _t: _inflight.pop(key, None))
    card = await asyncio.shield(task)
    degraded = bool(card.get("note")) or (card["kind"] == "results" and not card.get("metrics"))
    cache.put(key + ":degraded" if degraded else key, card)
    return card


def apply_refinement(item: dict, card: dict | None) -> None:
    """Let the model's reading of the filing adjust category/materiality of rule-based guesses."""
    if not card:
        return
    if card.get("kind") == "management" and card.get("llm") and not card.get("changes"):
        # The rules saw a title; the filing itself says nobody's position changed.
        item["category"] = "press_release"
        item["category_label"] = CATEGORIES["press_release"]
        item["score"] = min(item["score"], 30)
        item["why"] = item.get("why", []) + ["filing shows no change in position"]
        return
    if card.get("kind") == "results" and card.get("promoted_from") and item.get("category") != "results":
        item["category"] = "results"
        item["category_label"] = CATEGORIES["results"]
        item["subtype"] = "outcome"
        item["score"] = max(item["score"], 80)
        return
    if card.get("kind") != "summary":
        return
    m = card.get("materiality")
    if card.get("category") and item.get("needs_model"):
        item["category"] = card["category"]
        item["category_label"] = CATEGORIES.get(card["category"], item.get("category_label"))
    if isinstance(m, int):
        if item.get("needs_model"):
            item["score"] = {1: 12, 2: 32, 3: 50, 4: 66, 5: 80}.get(m, item["score"])
        elif m >= 5:
            item["score"] = min(100, item["score"] + 8)
        elif m <= 1:
            item["score"] = max(0, item["score"] - 12)
