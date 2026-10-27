"""Turn a material filing into something an investor can act on in five seconds.

* Results            -> headline numbers (Revenue, EBITDA, PBT, PAT) QoQ and YoY, read from the
                        actual results filing and cross-checked against screener's table.
* Management change  -> who is coming/going, plus a short professional background of the
                        incoming person (filing's own disclosed profile + web search).
* Everything else    -> one specific line, key facts and a "so what".
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import re
from datetime import date, datetime
from urllib.parse import quote_plus

from .. import cache
from ..llm import prompts
from ..llm.router import LLMUnavailable, available, complete
from ..sources import pdfs, screener
from .classify import CATEGORIES, amounts_in_crore, share_text

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
    interest = g("interest_earned")
    if is_bank:
        revenue = g("total_income") or ((interest or 0) + (g("other_income") or 0)) or None
        ebitda = g("operating_profit_before_provisions")
        if ebitda is None and pbt is not None and g("provisions") is not None:
            ebitda = pbt + g("provisions")
    else:
        revenue = g("revenue_from_operations")
        ebitda = None
        if pbt is not None and g("finance_costs") is not None and g("depreciation") is not None:
            pbt_pre_exc = pbt - (g("exceptional_items") or 0.0)
            ebitda = pbt_pre_exc + g("finance_costs") + g("depreciation") - (g("other_income") or 0.0)
    return {"revenue": revenue, "ebitda": ebitda, "pbt": pbt, "pat": pat, "interest": interest}


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


_UNIT_RE = re.compile(
    r"\bin\s+(?:\S{1,5}\s+)?(crores?|lakhs?|lacs?|millions?|thousands?|billions?)\b", re.I)


def sniff_unit(text: str | None) -> str | None:
    """The unit printed in a statement header ("₹ in crore", "(INR in million)", "Rs. in Lakhs")."""
    m = _UNIT_RE.search(text or "")
    return m.group(1).lower() if m else None


def shift_quarter(qe: str, n: int) -> str:
    """Quarter-end ``n`` quarters away from ``qe`` (YYYY-MM-DD)."""
    d = date.fromisoformat(qe)
    months = d.year * 12 + (d.month - 1) + 3 * n
    y, m = divmod(months, 12)
    return date(y, m + 1, calendar.monthrange(y, m + 1)[1]).isoformat()


COLUMNS = ("current", "previous_quarter", "year_ago_quarter")
_SCALES = (1.0, 0.1, 0.01, 10.0, 100.0, 1e-3, 1e-4, 1e-7, 1000.0)


def _scale_hint(ours: float | None, ref: float | None) -> float | None:
    """The clean power of ten that maps our figure onto the reference (1.0 if they already agree)."""
    if not ours or not ref or ours <= 0 or ref <= 0:
        return None
    r = ref / ours
    for f in _SCALES:
        if abs(r / f - 1) <= 0.15:
            return f
    return None


def _diff(ours: float | None, ref: float | None) -> float | None:
    if ours is None or ref is None or not ref:
        return None
    return abs(ours - ref) / abs(ref) * 100


def _ref_columns(ref_page: dict | None, period_end: str) -> dict[str, dict]:
    """Screener's headline numbers for each of the three columns of the filing, where it has them."""
    keys = ((ref_page or {}).get("quarters") or {}).get("keys") or []
    out = {}
    for name, qe in zip(COLUMNS, (period_end, shift_quarter(period_end, -1), shift_quarter(period_end, -4))):
        if qe in keys:
            out[name] = {**screener_headline(ref_page, qe)["current"], "quarter": qe}
    return out


def _rev_for_check(h: dict, bank: bool) -> float | None:
    # Screener's "Revenue" for lenders is interest earned; for everyone else it is revenue from operations.
    return h.get("interest") if bank else h.get("revenue")


def fix_scale(heads: dict[str, dict], refs: dict[str, dict], bank: bool) -> float | None:
    """If every column we can compare is off by the same power of ten, the unit was misread."""
    hints = []
    for name, ref in refs.items():
        h = heads.get(name) or {}
        f = _scale_hint(_rev_for_check(h, bank), ref.get("revenue"))
        if f is None:
            f = _scale_hint(h.get("pbt"), ref.get("pbt"))
        if f is not None:
            hints.append(f)
    if hints and all(f == hints[0] for f in hints) and hints[0] != 1.0:
        return hints[0]
    return None


def cross_check(heads: dict[str, dict], refs: dict[str, dict], bank: bool) -> dict | None:
    """Compare our read of each column with screener's standardised table (revenue within 2.5%, PBT within 5%)."""
    checks = []
    for name, ref in refs.items():
        h = heads.get(name) or {}
        rd = _diff(_rev_for_check(h, bank), ref.get("revenue"))
        pd = _diff(h.get("pbt"), ref.get("pbt"))
        if rd is None and pd is None:
            continue
        rev_ok = rd is None or rd <= 2.5
        scale = abs(ref.get("revenue") or 0) or abs(ref.get("pbt") or 0)
        pbt_ok = pd is None or pd <= 5 or abs((h.get("pbt") or 0) - (ref.get("pbt") or 0)) <= 0.01 * scale
        checks.append({"column": name, "quarter": ref["quarter"], "revenue_diff_pct": None if rd is None else round(rd, 2),
                       "pbt_diff_pct": None if pd is None else round(pd, 2), "ok": rev_ok and pbt_ok})
    if not checks:
        return None
    worst = max((c["revenue_diff_pct"] for c in checks if c["revenue_diff_pct"] is not None), default=0.0)
    return {"against": "screener.in", "ok": all(c["ok"] for c in checks), "revenue_diff_pct": worst,
            "current_checked": any(c["column"] == "current" for c in checks), "checks": checks}


def read_results(extracted: dict, page_bank: bool, sniffed_unit: str | None, qe: str) -> dict:
    """The model's raw extraction -> headline numbers in ₹ crore for the three columns."""
    unit = extracted.get("unit") or sniffed_unit
    factor = unit_factor(unit) or 1.0
    bank = bool(extracted.get("is_bank")) or page_bank
    cols = extracted.get("columns") or {}
    heads = {n: headline_from_columns(cols.get(n) or {}, factor, bank) for n in COLUMNS}
    period = ((extracted.get("periods") or {}).get("current")) or qe
    if not re.match(r"\d{4}-\d{2}-\d{2}$", str(period)) or str(period)[5:] not in ("03-31", "06-30", "09-30", "12-31"):
        period = qe
    return {"heads": heads, "bank": bank, "period_end": period, "basis": extracted.get("basis"), "unit": unit}


async def _reference_page(symbol: str | None, page: dict | None, basis: str | None) -> dict | None:
    """Screener's table on the same basis (consolidated/standalone) as the statement we read."""
    if not page or not basis or page.get("basis") == basis:
        return page
    if basis == "standalone" and symbol:
        try:
            return await screener.standalone_page(symbol)
        except Exception as exc:  # noqa: BLE001
            log.info("no standalone screener page for %s: %s", symbol, exc)
    return None


async def results_card(filing: dict, ctx: dict) -> dict:
    """Headline numbers from the results filing itself, verified against screener's standardised table.

    Pass 1 reads the text layer with the fast model. If that read fails the accounting identities or
    disagrees with screener on any column screener already has (the current quarter, or the previous
    and year-ago quarters when the filing is too fresh), pass 2 has the smart model read the page images
    (scanned filings often have a poor OCR layer). A clean power-of-ten mismatch is a misread unit and
    is corrected rather than rejected.
    """
    symbol = ctx.get("symbol")
    page = None
    try:
        page = await screener.company_page(symbol) if symbol else None
    except Exception as exc:  # noqa: BLE001
        log.info("screener unavailable for %s: %s", symbol, exc)
    qe = expected_quarter_end(filing.get("time"))
    card: dict = {"kind": "results", "quarter": screener.fy_quarter(qe), "period_end": qe, "source": None}
    page_bank = bool(page and page.get("is_bank"))
    accepted = None
    rejected: list[dict] = []
    reads: list[dict] = []  # what each pass did, for the slip's provenance line and /api/diag
    if available() and filing.get("pdf_url"):
        try:
            data = await pdfs.fetch_pdf(filing["pdf_url"])
            pages = await asyncio.to_thread(pdfs._extract, data, 60)
        except pdfs.PdfError as exc:
            card["note"] = f"Couldn't read the filing ({str(exc)[:120]})."
            pages, data = None, None
        except Exception as exc:  # noqa: BLE001
            log.warning("results PDF fetch failed for %s: %s", filing.get("id"), exc)
            card["note"] = "Couldn't fetch the filing."
            pages, data = None, None
        if pages is not None:
            text_layer = pdfs.has_text_layer(pages[:10])
            picked = pdfs.pick_results_pages(pages, 2) if text_layer else list(range(min(10, len(pages) or pdfs.page_count(data))))
            sniffed = sniff_unit(" ".join(pages[i] for i in picked if i < len(pages))) if text_layer else None
            # Text layer: read the text; if that fails, look at the page images (re-rendered without the
            # OCR layer), cheap model first. No text layer: the scanned pages themselves.
            passes = [("text", "fast"), ("vision", "fast"), ("vision", "smart")] if text_layer else [("vision", "fast"), ("vision", "smart")]
            images = None
            for n, (mode, tier) in enumerate(passes, 1):
                try:
                    if mode == "vision" and images is None:
                        images = await asyncio.to_thread(pdfs.raster_pages if text_layer else pdfs.trim_pdf, data, picked)
                    res = await complete(
                        "results_extract" if mode == "text" else "results_extract_vision",
                        system=prompts.RESULTS_SYSTEM,
                        prompt=prompts.RESULTS_PROMPT,
                        document=pdfs.numbered_text(pages, picked, 40_000) if mode == "text" else "",
                        pdf=images if mode == "vision" else None,
                        tier=tier,
                        max_tokens=4096,
                        validate=_validate_results,
                        effort="low",
                        timeout=45 if tier == "fast" else 75,
                        deadline=60 if tier == "fast" else 100,
                        borrow=n == len(passes),  # earlier passes fail fast: a later pass escalates anyway
                    )
                except LLMUnavailable as exc:
                    log.info("results pass %d (%s/%s) failed for %s: %s", n, mode, tier, filing.get("id"), str(exc)[:200])
                    card["note"] = "Couldn't read the filing (models busy or the statement didn't add up)."
                    reads.append({"pass": n, "mode": mode, "tier": tier, "error": str(exc)[:240]})
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.warning("results pass %d failed for %s: %s", n, filing.get("id"), exc)
                    reads.append({"pass": n, "mode": mode, "tier": tier, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                    continue
                read = read_results(res.data if isinstance(res.data, dict) else {}, page_bank, sniffed, qe)
                ref = await _reference_page(symbol, page, read["basis"])
                refs = _ref_columns(ref, read["period_end"])
                f = fix_scale(read["heads"], refs, read["bank"])
                if f:
                    read["heads"] = {k: {m: (None if v is None else v * f) for m, v in h.items()} for k, h in read["heads"].items()}
                    read["unit_corrected"] = True
                ver = cross_check(read["heads"], refs, read["bank"])
                read.update({"verified": ver, "llm": res.to_meta(), "pages": [p + 1 for p in picked], "pass": n, "mode": mode})
                reads.append({"pass": n, "mode": mode, "tier": tier, "model": res.model, "ok": bool(ver is None or ver["ok"]),
                              "revenue_diff_pct": ver and ver["revenue_diff_pct"], "unit_corrected": bool(f)})
                if ver is None or ver["ok"]:
                    accepted = read
                    break
                rejected.append(read)
    if reads:
        card["reads"] = reads
    retry_later = any("error" in r for r in reads)  # a pass that couldn't run (rate limits): try again later
    if accepted:
        card.pop("note", None)
        metrics, margins = build_metrics(accepted["heads"]["current"], accepted["heads"]["previous_quarter"],
                                         accepted["heads"]["year_ago_quarter"], accepted["bank"])
        card.update({
            "metrics": metrics, "margins": margins, "basis": accepted["basis"], "is_bank": accepted["bank"],
            "source": "results filing", "unit_reported": accepted["unit"], "llm": accepted["llm"], "pages": accepted["pages"],
            "period_end": accepted["period_end"], "quarter": screener.fy_quarter(accepted["period_end"]),
        })
        if accepted["verified"]:
            card["verified"] = accepted["verified"]
        if accepted.get("unit_corrected"):
            card["unit_note"] = "Unit corrected to ₹ crore by matching earlier quarters with screener.in."
        if accepted["pass"] > 1:
            card["second_read"] = True
        if not (accepted["verified"] and accepted["verified"]["current_checked"]):
            card["provisional"] = True  # re-verify once screener carries the quarter
        return card
    # Our read disagrees with the exchange-sourced standardised table: show that table, and say so.
    sh = screener_headline(page, qe) if page else None
    if sh:
        bank = page_bank
        metrics, margins = build_metrics(sh["current"], sh["prev"], sh["yoy"], bank)
        if bank:  # screener's "Financing Profit" is not pre-provision profit; name it as screener does
            for m in metrics:
                if m["key"] == "ebitda":
                    m["label"] = "Financing profit"
        card.pop("note", None)
        card.update({"metrics": metrics, "margins": margins, "basis": page.get("basis"), "is_bank": bank,
                     "source": "screener.in standardised table"})
        if rejected:
            best = min(rejected, key=lambda r: r["verified"]["revenue_diff_pct"])
            cur = next((c for c in best["verified"]["checks"] if c["column"] == "current"), None)
            gap = cur["revenue_diff_pct"] if cur and cur["revenue_diff_pct"] is not None else best["verified"]["revenue_diff_pct"]
            card["filing_read_rejected"] = f"FINN's read of the filing differed from screener by {gap:.1f}% on revenue"
            card["verified"] = {**best["verified"], "against": "results filing"}
            card["llm"] = best["llm"]
        if retry_later:
            card["provisional"] = True
        return card
    if rejected:
        # Too fresh for screener and our read disagrees with it on earlier quarters: show it, flagged.
        best = min(rejected, key=lambda r: r["verified"]["revenue_diff_pct"])
        metrics, margins = build_metrics(best["heads"]["current"], best["heads"]["previous_quarter"],
                                         best["heads"]["year_ago_quarter"], best["bank"])
        card.pop("note", None)
        card.update({"metrics": metrics, "margins": margins, "basis": best["basis"], "is_bank": best["bank"],
                     "source": "results filing", "unit_reported": best["unit"], "llm": best["llm"], "pages": best["pages"],
                     "verified": best["verified"], "provisional": True,
                     "caution": f"Earlier-quarter columns differ from screener.in by up to {best['verified']['revenue_diff_pct']:.1f}% on revenue; treat as unverified."})
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
        return {"found": False, "sources": clean_sources(sources)[:3]}
    li = str(d.get("linkedin_url") or "")
    return {
        "found": True,
        "headline": d.get("headline"),
        "background": d.get("background"),
        "previous_roles": [r for r in (d.get("previous_roles") or []) if isinstance(r, dict)][:4],
        # A profile, not a post or a company page that merely mentions the person.
        "linkedin_url": li if re.match(r"https://([a-z]{2,3}\.)?(www\.)?linkedin\.com/in/[^/?#]+", li) else None,
        "confidence": d.get("confidence"),
        "sources": clean_sources(sources)[:4],
    }


def clean_sources(sources: list[dict]) -> list[dict]:
    """Readable source labels: a page's own title when it is legible, else its domain."""
    out, seen = [], set()
    for s in sources or []:
        url = str(s.get("url") or "")
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        title = re.sub(r"\s+", " ", str(s.get("title") or "")).strip()
        legible = title and len(title) <= 90 and sum(ch.isascii() for ch in title) / len(title) > 0.85
        out.append({"title": title if legible else _domain(url), "url": url})
    return out


async def person_background(name: str, role: str, company: str, profile: str | None, bucket: str | None = None) -> dict | None:
    """Professional background of an incoming executive, cheapest route first.

    1. Gemini with Google Search grounding (when the key's free search quota allows it).
    2. A keyless web search (LinkedIn first) whose results the fast model summarises, using only results
       that clearly match the person (~1k tokens).
    3. Only if that couldn't run, or found nothing for a CEO/CFO/chair: a Groq search agent, which reads
       whole pages (~100k+ tokens), so it is the last resort.
    The company's own disclosed profile (SEBI requires one for appointments) is always shown alongside.
    Returns None when no route could run (so the card is re-tried later).
    """
    if not available():
        return None
    ask = dict(system=prompts.BIO_SYSTEM, prompt=prompts.BIO_PROMPT.format(name=name, role=role, company=company, profile=profile or "(none)"),
               tier="fast", search=True, max_tokens=1200, temperature=0.2)
    try:
        res = await complete("person_bio", **ask, search_agents=False, timeout=45, deadline=60)
        d = res.data if isinstance(res.data, dict) else {}
        if d:
            return _bio_result(d, res.sources)
    except LLMUnavailable as exc:
        log.info("grounded search unavailable (%s); falling back to web results", str(exc)[:120])
    bio = await _bio_from_web_results(name, role, company, profile)
    if bio is not None and (bio.get("found") or bucket not in ("ceo_md", "cfo", "chair")):
        return bio
    try:
        res = await complete("person_bio_agent", **ask, search_agents=True, timeout=60, deadline=75)
        d = res.data if isinstance(res.data, dict) else {}
        if d:
            return _bio_result(d, res.sources)
    except LLMUnavailable as exc:
        log.info("search agent unavailable: %s", str(exc)[:120])
    return bio


async def _bio_from_web_results(name: str, role: str, company: str, profile: str | None) -> dict | None:
    from ..sources import websearch

    results: list[dict] = []
    failed = 0
    for q in (f'"{name}" {company} linkedin', f"{name} {role} {company}"):
        if len(results) >= 3:
            break
        try:
            results += [r for r in await websearch.search(q, 6) if r not in results]
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.info("web search failed for %r: %s", q, exc)
    if not results:
        return None if failed else {"found": False, "sources": []}  # None = couldn't look
    listing = "\n".join(f"[{i}] {r['title']} | {r['url']} | {r['snippet']}" for i, r in enumerate(results[:8]))
    try:
        res = await complete(
            "person_bio_from_results",
            system=prompts.BIO_FROM_RESULTS_SYSTEM,
            prompt=prompts.BIO_FROM_RESULTS_PROMPT.format(name=name, role=role, company=company, profile=profile or "(none)", results=listing),
            tier="fast",
            max_tokens=900,
            temperature=0.1,
            timeout=45,
            deadline=60,
        )
    except LLMUnavailable:
        return None
    d = res.data if isinstance(res.data, dict) else {}
    used = [results[i] for i in (d.get("used_results") or []) if isinstance(i, int) and 0 <= i < len(results)]
    return _bio_result(d, [{"title": r["title"], "url": r["url"]} for r in used])


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
            timeout=45,
            deadline=70,
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
        *[person_background(c["name"], c.get("role") or "", company, c.get("profile_from_filing"), c.get("role_bucket")) for c in incoming[:2]]
    )
    bio_by_name = {c["name"]: b for c, b in zip(incoming[:2], bios)}
    for c in changes:
        c["linkedin_search"] = linkedin_search(c["name"], company)
        c["background"] = bio_by_name.get(c["name"])
    card["changes"] = changes[:6]
    if any(b is None for b in bios):
        card["provisional"] = True  # the background lookup couldn't run; retry later
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
            timeout=45,
            deadline=70,
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
    return f"enrich:v7:{filing_id}"


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


PROVISIONAL_TTL = 6 * 3600


def cached_enrichment(filing_id: str) -> dict | None:
    key = enrichment_key(filing_id)
    return cache.get(key, cache.FOREVER) or cache.get(key + ":provisional", PROVISIONAL_TTL) or cache.get(key + ":degraded", 600)


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
    # Provisional = good enough to show, but worth re-checking later (e.g. results too fresh for
    # screener to have the quarter yet). Model calls are cached, so a re-check costs nothing.
    cache.put(key + (":degraded" if degraded else ":provisional" if card.get("provisional") else ""), card)
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
    # The exchange headline often has no amount; the filing does. A deal that is a rounding error
    # next to the company's market value is not something to interrupt anyone for.
    mcap = item.get("market_cap_cr")
    if item.get("category") in AMOUNT_CATEGORIES and mcap and not item.get("amount_cr"):
        facts = " ".join([str(card.get("one_liner") or "")] + [str(k.get("value") or "") for k in card.get("key_facts") or [] if isinstance(k, dict)])
        amts = amounts_in_crore(facts)
        if amts:
            biggest = max(amts)
            pct = biggest / mcap * 100
            item["amount_cr"], item["amount_pct_mcap"] = biggest, round(pct, 3)
            if pct < 0.05:
                item["score"] = min(item["score"], 38)
                amt = f"₹{biggest * 100:,.0f} lakh" if biggest < 1 else f"₹{biggest:,.0f} cr"
                item["why"] = item.get("why", []) + [f"{amt} is {share_text(pct)} of market cap"]


AMOUNT_CATEGORIES = ("m_and_a", "fund_raise", "press_release", "litigation", "capex")
