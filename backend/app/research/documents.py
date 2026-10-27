"""Pick and prepare the primary documents for a company: the last four earnings-call transcripts
and the latest annual report. "Retrieve before you read": only the passages that can carry
guidance, segment, audit or risk information are sent to a model."""
from __future__ import annotations

import asyncio
import logging
import re

import fitz

from .. import cache
from ..sources import pdfs
from ..sources.screener import fy_quarter
from ..push.enrich import expected_quarter_end

log = logging.getLogger("finn.documents")

_PDF_SEM = asyncio.Semaphore(2)  # the free host has 512 MB and a fraction of a CPU

FORWARD_TERMS = {
    "guidance": 4, "guide": 3, "outlook": 3, "expect": 2, "target": 3, "aspire": 2, "aim": 1, "we will": 1,
    "going forward": 2, "next year": 2, "next quarter": 2, "fy2": 1, "fy 2": 1, "h2": 2, "second half": 2,
    "margin": 2, "ebitda": 2, "growth": 1, "double digit": 3, "high single": 3, "mid-teens": 3, "capex": 3,
    "capacity": 2, "order book": 3, "pipeline": 2, "debt": 1, "commission": 2, "by march": 2, "by the end of": 2,
    "%": 1, "crore": 1, "bps": 2, "basis points": 2, "trajectory": 1, "sustain": 1, "demand": 1, "volume": 1,
}


def transcripts_from_page(page: dict, n: int = 4) -> list[dict]:
    """The latest ``n`` distinct earnings-call transcripts, newest first, with the quarter they discuss."""
    out = []
    seen_quarters: set[str] = set()
    for c in (page.get("documents") or {}).get("concalls") or []:
        if not c.get("transcript") or not c.get("date_key"):
            continue
        qe = expected_quarter_end(c["date_key"] + "T12:00:00+05:30")
        q = fy_quarter(qe)
        if q in seen_quarters:
            continue
        seen_quarters.add(q)
        out.append({"label": c["label"], "quarter": q, "quarter_end": qe, "url": c["transcript"], "ppt": c.get("ppt")})
        if len(out) >= n:
            break
    return out


def latest_annual_report(page: dict) -> dict | None:
    ars = [a for a in (page.get("documents") or {}).get("annual_reports") or [] if a.get("is_pdf") and a.get("year")]
    if not ars:
        return None
    return max(ars, key=lambda a: a["year"])


# ------------------------------------------------------------------ transcripts

def _paragraphs(pages: list[str]) -> list[tuple[int, str]]:
    out = []
    for pi, text in enumerate(pages):
        t = re.sub(r"[ \t]+", " ", text)
        for para in re.split(r"\n\s*\n|\n(?=[A-Z][A-Za-z .']{2,40}:)", t):
            p = re.sub(r"\s*\n\s*", " ", para).strip()
            if len(p) >= 40:
                out.append((pi, p))
    return out


def condense_transcript(pages: list[str], max_chars: int = 48_000) -> str:
    """Opening remarks verbatim + the most forward-looking Q&A passages, with [p.N] markers."""
    paras = _paragraphs(pages)
    if not paras:
        return ""
    total = sum(len(p) for _, p in paras)
    if total <= max_chars:
        chosen = list(range(len(paras)))
    else:
        opening_budget = int(max_chars * 0.35)
        chosen, used = [], 0
        for i, (_, p) in enumerate(paras):
            if used + len(p) > opening_budget:
                break
            chosen.append(i)
            used += len(p)
        scored = []
        for i in range(len(chosen), len(paras)):
            low = paras[i][1].lower()
            s = sum(w * min(low.count(k), 3) for k, w in FORWARD_TERMS.items())
            scored.append((s / (1 + len(low) / 900), i))
        for _, i in sorted(scored, reverse=True):
            if used + len(paras[i][1]) > max_chars:
                continue
            chosen.append(i)
            used += len(paras[i][1])
        chosen.sort()
    out, last_page = [], None
    for i in chosen:
        pi, p = paras[i]
        if pi != last_page:
            out.append(f"\n[p.{pi + 1}]")
            last_page = pi
        out.append(p)
    return "\n".join(out).strip()


async def transcript_text(url: str) -> str:
    async with _PDF_SEM:
        pages = await pdfs.page_texts(url, max_pages=80)
    if not pdfs.has_text_layer(pages, 150):
        raise pdfs.PdfError("transcript has no text layer")
    return condense_transcript(pages)


# ------------------------------------------------------------------ annual report

AR_THEMES = {
    "business": {"management discussion": 4, "business overview": 4, "our business": 3, "segment": 3, "revenue from": 2,
                 "products": 1, "customers": 1, "market share": 2, "industry structure": 2, "opportunities": 1},
    "audit": {"independent auditor": 4, "opinion": 2, "key audit matter": 5, "emphasis of matter": 5, "qualified opinion": 6,
              "basis for opinion": 3, "material uncertainty": 5},
    "contingent": {"contingent liabilit": 6, "claims against the company not acknowledged": 5, "guarantees": 1},
    "related": {"related party": 4, "related parties": 4, "key management personnel": 2, "transactions with": 1},
    "capex": {"capital expenditure": 4, "capex": 4, "capacity": 2, "commissioned": 2, "expansion": 2, "new plant": 3},
    "risk": {"risks and concerns": 5, "risk management": 2, "threats": 2, "risks": 1},
}
TOC_PATTERNS = re.compile(
    r"management discussion|md ?& ?a|business (overview|review|segment)|segment|auditor|contingent|related party|risk|"
    r"directors'? report|board'?s report|operating review|performance review", re.I)
PAGES_PER_THEME = {"business": 8, "audit": 4, "contingent": 2, "related": 2, "capex": 3, "risk": 3}


def _toc_candidates(doc: "fitz.Document") -> list[int]:
    toc = doc.get_toc(simple=True) or []
    starts = sorted({p - 1 for _, title, p in toc if p and p > 0 and TOC_PATTERNS.search(title or "")})
    pages = set()
    for s in starts:
        for p in range(s, min(s + 10, doc.page_count)):
            pages.add(p)
    return sorted(pages)


@pdfs.frees_memory
def _select_ar_pages(data: bytes) -> tuple[list[int], dict[int, str], int]:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        n = doc.page_count
        cand = _toc_candidates(doc) if n > 60 else list(range(n))
        if len(cand) < 25:  # no usable bookmarks: scan everything (slow on a tiny CPU, but cached)
            cand = list(range(n))
        texts = {i: doc.load_page(i).get_text("text") for i in cand}
    finally:
        doc.close()
    chosen: list[int] = []
    for theme, kws in AR_THEMES.items():
        scored = []
        for i, t in texts.items():
            low = t.lower()
            s = sum(w * min(low.count(k), 4) for k, w in kws.items())
            if s > 0:
                scored.append((s, i))
        for _, i in sorted(scored, reverse=True)[: PAGES_PER_THEME[theme]]:
            if i not in chosen:
                chosen.append(i)
    return sorted(chosen), texts, n


async def annual_report_text(url: str, max_chars: int = 70_000) -> tuple[str, int]:
    """Selected pages of the annual report with [p.N] markers, and the report's page count (cached)."""

    async def compute() -> dict:
        async with _PDF_SEM:
            data = await pdfs.fetch_pdf(url)
            chosen, texts, n = await asyncio.to_thread(_select_ar_pages, data)
            del data
        pages_list = [texts.get(i, "") for i in range(max(chosen) + 1)] if chosen else []
        return {"text": pdfs.numbered_text(pages_list, chosen, max_chars), "pages": n}

    out = await cache.cached(f"artext:v1:{url}:{max_chars}", cache.FOREVER, compute, mem=False)
    return out["text"], out["pages"]
