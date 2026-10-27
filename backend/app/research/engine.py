"""On-demand research for any NSE ticker, run as a background job with visible progress.

Deterministic sections (from filed numbers) are available within seconds; model-written sections
fill in as the transcripts and annual report are read. Results are cached for 12 hours, the
expensive intermediate reads (per-document notes) forever.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .. import cache
from ..llm import prompts
from ..llm.router import LLMUnavailable, available, complete
from ..sources import screener
from . import analyst, documents
from . import fundamentals as F

log = logging.getLogger("finn.research")

RESULT_TTL = 12 * 3600
_JOB_SEM = asyncio.Semaphore(2)

STAGES = [
    ("numbers", "Reading filed financials"),
    ("documents", "Fetching annual report & call transcripts"),
    ("calls", "Reading earnings calls"),
    ("annual_report", "Reading the annual report"),
    ("analysis", "Holding promises against delivery"),
]


@dataclass
class Job:
    symbol: str
    status: str = "running"
    stages: list[dict] = field(default_factory=lambda: [{"id": s, "label": l, "status": "pending", "detail": None} for s, l in STAGES])
    result: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    error: str | None = None
    task: asyncio.Task | None = None

    def stage(self, sid: str, status: str, detail: str | None = None) -> None:
        for s in self.stages:
            if s["id"] == sid:
                s["status"] = status
                if detail is not None:
                    s["detail"] = detail

    def view(self) -> dict:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "stages": self.stages,
            "result": self.result,
            "elapsed_s": round((self.finished or time.time()) - self.started, 1),
            "error": self.error,
        }


_jobs: dict[str, Job] = {}


def result_key(symbol: str) -> str:
    return f"research:v1:{symbol}"


def get_or_start(symbol: str, refresh: bool = False) -> dict:
    sym = screener.normalise_symbol(symbol)
    job = _jobs.get(sym)
    if job and job.status == "running":
        return job.view()
    if not refresh:
        cached_view = cache.get(result_key(sym), RESULT_TTL)
        if cached_view:
            return {**cached_view, "cached": True}
    job = Job(sym)
    _jobs[sym] = job
    job.task = asyncio.create_task(_run(job))
    return job.view()


def peek(symbol: str) -> dict | None:
    sym = screener.normalise_symbol(symbol)
    job = _jobs.get(sym)
    if job:
        return job.view()
    return cache.get(result_key(sym), RESULT_TTL)


async def _run(job: Job) -> None:
    calls: list[dict] = []
    try:
        async with _JOB_SEM:
            await _pipeline(job, calls)
        job.status = "done"
    except screener.CompanyNotFound:
        job.status = "error"
        job.error = f"No company found for '{job.symbol}' on screener.in. Check the NSE symbol."
    except Exception as exc:  # noqa: BLE001
        log.exception("research failed for %s", job.symbol)
        job.status = "error"
        job.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        job.finished = time.time()
        job.result["engine"] = _engine_meta(calls, job)
        for s in job.stages:
            if s["status"] in ("pending", "running"):
                s["status"] = "skipped" if job.status == "done" else "error"
        if job.status == "done":
            cache.put(result_key(job.symbol), {k: v for k, v in job.view().items()})


def _engine_meta(calls: list[dict], job: Job) -> dict:
    return {
        "llm_calls": len([c for c in calls if not c.get("cached")]),
        "cached_calls": len([c for c in calls if c.get("cached")]),
        "input_tokens": sum(c.get("input_tokens", 0) for c in calls if not c.get("cached")),
        "output_tokens": sum(c.get("output_tokens", 0) for c in calls if not c.get("cached")),
        "models": sorted({c["model"] for c in calls if c.get("model")}),
        "calls": calls,
        "seconds": round((job.finished or time.time()) - job.started, 1),
    }


async def _llm(calls: list[dict], task: str, **kw):
    res = await complete(task, **kw)
    calls.append({"task": task, **res.to_meta()})
    return res


async def _pipeline(job: Job, calls: list[dict]) -> None:
    sym = job.symbol
    # 1. Numbers ---------------------------------------------------------------------------
    job.stage("numbers", "running")
    page = await screener.company_page(sym)
    job.result.update(
        {
            "company": {
                "symbol": page.get("nse_symbol") or sym,
                "name": page.get("name"),
                "bse_code": page.get("bse_code"),
                "sector": page.get("sector"),
                "website": page.get("website"),
                "basis": page.get("basis"),
                "is_bank": page.get("is_bank"),
                "screener_url": page.get("url"),
                "price": (page.get("ratios") or {}).get("price"),
                "market_cap_cr": (page.get("ratios") or {}).get("market_cap_cr"),
            },
            "financials": F.financial_snapshot(page),
            "trajectory": F.trajectory(page),
            "balance_sheet": F.balance_sheet(page),
            "cash_quality": F.cash_quality(page),
            "shareholding": F.shareholding(page),
        }
    )
    quarters = F.quarter_rows(page)
    annual = F.annual_rows(page)
    job.stage("numbers", "done", f"{len(quarters)} quarters, {len([a for a in annual if a['period'] != 'TTM'])} years")

    # 2. Documents -------------------------------------------------------------------------
    job.stage("documents", "running")
    calls_meta = documents.transcripts_from_page(page, 4)
    ar_meta = documents.latest_annual_report(page)
    sources = []
    for t in calls_meta:
        sources.append({"kind": "transcript", "label": f"{t['quarter']} earnings call ({t['label']})", "url": t["url"]})
    if ar_meta:
        sources.append({"kind": "annual_report", "label": ar_meta["title"], "url": ar_meta["url"]})
    job.result["sources"] = sources
    if not available():
        job.result["business"] = {"id": "business", "title": "Business", "overview": None, "note": "Add a Gemini or Groq key to read the annual report."}
        job.result["analysis_note"] = "No LLM key configured: showing the numbers only."
        for sid in ("documents", "calls", "annual_report", "analysis"):
            job.stage(sid, "skipped", "no LLM key")
        return

    async def load_transcript(t: dict) -> dict:
        try:
            t["text"] = await documents.transcript_text(t["url"])
        except Exception as exc:  # noqa: BLE001
            t["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        return t

    async def load_ar() -> dict | None:
        if not ar_meta:
            return None
        try:
            text, n = await documents.annual_report_text(ar_meta["url"])
            return {**ar_meta, "text": text, "page_count": n}
        except Exception as exc:  # noqa: BLE001
            return {**ar_meta, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    loaded, ar = await asyncio.gather(asyncio.gather(*[load_transcript(t) for t in calls_meta]), load_ar())
    ok_t = [t for t in loaded if t.get("text")]
    job.stage(
        "documents",
        "done",
        f"{len(ok_t)}/{len(calls_meta)} transcripts" + (f", annual report {ar['year']} ({ar.get('page_count', '?')} pages)" if ar and ar.get("text") else ", no annual report"),
    )
    chunks = []

    # 3. Calls (map) -----------------------------------------------------------------------
    job.stage("calls", "running")
    company = page.get("name") or sym

    async def read_call(t: dict) -> dict:
        try:
            res = await _llm(
                calls,
                "transcript_notes",
                system=prompts.TRANSCRIPT_SYSTEM,
                prompt=prompts.TRANSCRIPT_PROMPT.format(company=company, call_label=t["label"], quarter=t["quarter"]),
                document=t["text"],
                tier="fast",
                max_tokens=6000,
            )
            t["notes"] = res.data if isinstance(res.data, dict) else None
        except LLMUnavailable as exc:
            t["error"] = f"model unavailable: {str(exc)[:120]}"
        return t

    await asyncio.gather(*[read_call(t) for t in ok_t])
    read = [t for t in ok_t if t.get("notes")]
    for t in ok_t:
        chunks.extend(_chunks(t["text"], f"{t['quarter']} call"))
    job.stage("calls", "done" if read else ("skipped" if not ok_t else "error"), f"{len(read)} calls read")

    # 4. Annual report (map) ---------------------------------------------------------------
    job.stage("annual_report", "running")
    ar_notes = None
    if ar and ar.get("text"):
        try:
            res = await _llm(
                calls,
                "annual_report_notes",
                system=prompts.AR_SYSTEM,
                prompt=prompts.AR_PROMPT.format(company=company, title=ar["title"]),
                document=ar["text"],
                tier="fast",
                max_tokens=5000,
            )
            ar_notes = res.data if isinstance(res.data, dict) else None
            if ar_notes is not None:
                ar_notes["title"] = ar["title"]
        except LLMUnavailable as exc:
            ar["error"] = f"model unavailable: {str(exc)[:120]}"
        chunks.extend(_chunks(ar["text"], ar["title"].replace("Annual Report", "AR")))
        job.stage("annual_report", "done" if ar_notes else "error", ar.get("error") or f"{ar.get('page_count', '?')}-page report, key pages read")
    else:
        job.stage("annual_report", "skipped", (ar or {}).get("error") or "no annual report PDF")
    job.result["business"] = _business_section(page, ar_notes, read)
    cache.put(f"research:chunks:{sym}", chunks, mem=False)

    # 5. Analysis (check + reduce) ---------------------------------------------------------
    job.stage("analysis", "running")
    computed, rest = analyst.computed_promises(read, quarters, annual)
    by_t: dict[str, list] = {}
    for g in rest:
        by_t.setdefault(g["made_in_q"], []).append(g)
    for t in read:
        t["guidance_ids"] = by_t.get(t["quarter"], [])
    material = analyst.material_text(company, read, ar_notes, {g["id"] for g in rest})
    if not material.strip():
        job.result["promises"] = {"id": "promises", "title": "Promised vs delivered", "items": computed, "credibility": analyst.credibility(computed),
                                  "note": "No earnings-call transcripts could be read for this company."}
        job.stage("analysis", "skipped", "nothing to analyse")
        return
    computed_txt = "\n".join(f"[{c['id']}] {c['made_in']}: {c['promise']} -> {c['outcome'].upper()} ({c['evidence']})" for c in computed) or "(none)"
    try:
        res = await _llm(
            calls,
            "analyst_reduce",
            system=prompts.ANALYST_SYSTEM,
            prompt=prompts.ANALYST_PROMPT.format(
                company=company,
                sector=(page.get("sector") or {}).get("Industry") or (page.get("sector") or {}).get("Sector") or "",
                numbers=F.numbers_digest(page),
                material="(see document)",
                computed=computed_txt,
            ),
            document=material,
            tier="smart",
            max_tokens=7000,
            temperature=0.2,
        )
        a = res.data if isinstance(res.data, dict) else {}
    except LLMUnavailable as exc:
        a = {}
        job.result["analysis_note"] = f"Analysis model unavailable right now ({str(exc)[:100]}). Numbers and document notes are shown."
    promises = analyst.merge_promises(computed, a.get("promises") or [])
    cred = analyst.credibility(promises)
    model_cred = a.get("credibility") if isinstance(a.get("credibility"), dict) else None
    job.result["promises"] = {
        "id": "promises",
        "title": "Promised vs delivered",
        "items": promises,
        "credibility": cred,
        "summary": (model_cred or {}).get("summary"),
        "model_score": (model_cred or {}).get("score"),
        "method": "Quantitative company-level promises are scored by code against reported numbers; qualitative ones by the model, citing later calls.",
    }
    job.result["narrative"] = {"id": "narrative", "title": "Narrative vs numbers", "items": [x for x in (a.get("narrative_vs_numbers") or []) if isinstance(x, dict)]}
    guidance = [x for x in (a.get("current_guidance") or []) if isinstance(x, dict)]
    if not guidance and read:
        latest = read[0]
        guidance = [{"metric": g.get("metric"), "guidance": g.get("statement"), "period": g.get("period"), "source": f"{latest['quarter']} call p.{g.get('page')}"}
                    for g in (latest["notes"].get("guidance") or []) if isinstance(g, dict)]
    job.result["guidance"] = {"id": "guidance", "title": "Current guidance", "items": guidance, "as_of": read[0]["quarter"] + " call" if read else None}
    job.result["bull_bear"] = {
        "id": "bull_bear",
        "title": "Bull vs bear",
        "bull": [x for x in (a.get("bull_case") or []) if isinstance(x, dict)][:3],
        "bear": [x for x in (a.get("bear_case") or []) if isinstance(x, dict)][:3],
        "watch_next": [x for x in (a.get("watch_next") or []) if isinstance(x, str)][:3],
    }
    job.result["calls"] = [
        {"quarter": t["quarter"], "label": t["label"], "url": t["url"], "tone": (t["notes"] or {}).get("tone"),
         "pushback": (t["notes"] or {}).get("analyst_pushback") or [], "highlights": (t["notes"] or {}).get("reported_highlights") or []}
        for t in read
    ]
    job.stage("analysis", "done" if a else "error", f"{len(promises)} promises tracked")


def _business_section(page: dict, ar: dict | None, calls_read: list[dict]) -> dict:
    sec = {"id": "business", "title": "Business snapshot", "overview": None, "segments": [], "source": None}
    if ar:
        sec.update(
            {
                "overview": ar.get("business_overview"),
                "segments": [s for s in (ar.get("segments") or []) if isinstance(s, dict)][:8],
                "revenue_mix": ar.get("revenue_mix_notes"),
                "strategy": [s for s in (ar.get("strategy_priorities") or []) if isinstance(s, dict)][:5],
                "risks": [s for s in (ar.get("risks_flagged_by_management") or []) if isinstance(s, dict)][:5],
                "audit": {
                    "opinion": ar.get("auditor_opinion"),
                    "key_audit_matters": ar.get("key_audit_matters") or [],
                    "emphasis_of_matter": ar.get("emphasis_of_matter"),
                    "contingent_liabilities": ar.get("contingent_liabilities"),
                    "related_parties": ar.get("related_party_highlights"),
                },
                "capex_plans": ar.get("capex_plans"),
                "source": ar.get("title"),
            }
        )
    if not sec["segments"] and calls_read:
        segs = (calls_read[0].get("notes") or {}).get("segments") or []
        sec["segments"] = [{"name": s.get("name"), "comment": s.get("comment"), "revenue_share_pct": None} for s in segs if isinstance(s, dict)]
        sec["segments_source"] = f"{calls_read[0]['quarter']} call"
    return sec


def _chunks(text: str, tag: str, size: int = 1400) -> list[dict]:
    """Split page-marked text into ~size-char chunks tagged with their page, for Q&A retrieval."""
    out, buf, page = [], [], None
    cur_page = None
    length = 0
    for line in text.split("\n"):
        if line.startswith("[p.") and line.rstrip().endswith("]"):
            cur_page = line.strip()[3:-1]
            continue
        if not line.strip():
            continue
        if page is None:
            page = cur_page
        buf.append(line)
        length += len(line)
        if length >= size:
            out.append({"tag": f"{tag} p.{page}", "text": " ".join(buf)})
            buf, length, page = [], 0, None
    if buf:
        out.append({"tag": f"{tag} p.{page}", "text": " ".join(buf)})
    return out


async def ask(symbol: str, question: str) -> dict:
    """Grounded Q&A over the company's transcripts and annual report (BM25 retrieval)."""
    from rank_bm25 import BM25Okapi

    sym = screener.normalise_symbol(symbol)
    chunks = cache.get(f"research:chunks:{sym}", cache.FOREVER, mem=False)
    if not chunks:
        state = get_or_start(sym)
        return {"answer": None, "status": "indexing", "research": state.get("status"),
                "note": "FINN is reading this company's documents first. Ask again in a minute."}
    page = await screener.company_page(sym)

    def tok(s: str) -> list[str]:
        import re as _re
        return [w for w in _re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2]

    bm = BM25Okapi([tok(c["text"]) for c in chunks])
    scores = bm.get_scores(tok(question))
    top = sorted(range(len(chunks)), key=lambda i: -scores[i])[:6]
    excerpts = "\n\n".join(f"[{chunks[i]['tag']}] {chunks[i]['text']}" for i in top if scores[i] > 0)
    if not excerpts:
        return {"answer": "The annual report and recent call transcripts don't discuss that.", "sources": [], "confidence": "low"}
    res = await complete(
        "qa",
        system=prompts.QA_SYSTEM,
        prompt=prompts.QA_PROMPT.format(question=question, numbers=F.numbers_digest(page)[:3000], excerpts="(see document)"),
        document=excerpts,
        tier="fast",
        max_tokens=1200,
        cache_ttl=24 * 3600,
    )
    d = res.data if isinstance(res.data, dict) else {}
    return {"answer": d.get("answer"), "sources": d.get("sources_used") or [], "confidence": d.get("confidence"),
            "retrieved": [chunks[i]["tag"] for i in top if scores[i] > 0], "llm": res.to_meta()}
