"""Download PDFs, extract text page by page, and cut them down to the pages that matter.

Exchange filings are frequently scanned, signed images with no text layer, and annual reports run
to 300-500 pages. Two rules keep this cheap: (1) only the relevant pages are ever sent to a model,
(2) text is sent when a text layer exists, the trimmed PDF itself (for vision) only when it doesn't.
"""
from __future__ import annotations

import asyncio
import logging
import re

import fitz  # PyMuPDF

from .. import cache, http

log = logging.getLogger("finn.pdfs")

MAX_PDF_BYTES = 80 * 1024 * 1024


class PdfError(RuntimeError):
    pass


def _candidates(url: str) -> list[str]:
    """BSE's AnnPdfOpen redirector can be slow; try the direct archive paths first."""
    m = re.search(r"AnnPdfOpen\.aspx\?Pname=([\w\-]+\.pdf)", url, re.I)
    if m:
        from .bse import pdf_candidates

        return pdf_candidates(m.group(1))
    return [url]


async def fetch_pdf(url: str) -> bytes:
    """Bytes of a PDF (disk-cached forever: filings never change once published)."""
    blob = cache.get_blob(url, ".pdf")
    if blob is not None:
        return blob
    last: Exception | None = None
    for cand in _candidates(url):
        try:
            resp = await http.request("GET", cand, retries=1, timeout=60.0)
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            last = exc
            continue
        data = resp.content
        if len(data) > MAX_PDF_BYTES:
            raise PdfError(f"PDF too large ({len(data) // 1_000_000} MB)")
        if not data.startswith(b"%PDF"):
            last = PdfError(f"not a PDF at {cand} ({resp.headers.get('content-type')})")
            continue
        cache.put_blob(url, data, ".pdf")
        return data
    raise PdfError(f"could not download {url}: {last}")


def _extract(data: bytes, max_pages: int | None) -> list[str]:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PdfError(f"unreadable PDF: {exc}") from exc
    try:
        n = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        return [doc.load_page(i).get_text("text") for i in range(n)]
    finally:
        doc.close()


async def page_texts(url: str, max_pages: int | None = None) -> list[str]:
    """Per-page text for a PDF URL (cached)."""

    async def compute() -> list[str]:
        data = await fetch_pdf(url)
        return await asyncio.to_thread(_extract, data, max_pages)

    return await cache.cached(f"pdftext:{url}:{max_pages}", cache.FOREVER, compute, mem=False)


def page_count(data: bytes) -> int:
    with fitz.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


def has_text_layer(pages: list[str], min_chars: int = 250) -> bool:
    if not pages:
        return False
    rich = sum(1 for p in pages if len(p.strip()) >= min_chars)
    return rich >= max(1, len(pages) // 3)


def trim_pdf(data: bytes, pages: list[int]) -> bytes:
    """A new PDF containing only ``pages`` (0-based), in order.

    Copies the pages without links, annotations or form fields (signed filings carry broken ones that
    make PyMuPDF's copy fail). If the copy still fails, the pages are re-rendered as images.
    """
    src = fitz.open(stream=data, filetype="pdf")
    try:
        keep = [p for p in pages if 0 <= p < src.page_count]
        out = fitz.open()
        try:
            for p in keep:
                out.insert_pdf(src, from_page=p, to_page=p, links=0, annots=0, widgets=0)
            return out.tobytes(garbage=3, deflate=True)
        except Exception as exc:  # noqa: BLE001
            log.info("page copy failed (%s); rasterising %d pages", exc, len(keep))
        finally:
            out.close()
        return _raster_pdf(src, keep)
    finally:
        src.close()


def raster_pages(data: bytes, pages: list[int], dpi: int = 160) -> bytes:
    """``pages`` re-rendered as plain images in a new PDF. For a model's second look at a scanned filing:
    the image alone, without the (often garbled) OCR text layer that came with it."""
    src = fitz.open(stream=data, filetype="pdf")
    try:
        return _raster_pdf(src, [p for p in pages if 0 <= p < src.page_count], dpi)
    finally:
        src.close()


def _raster_pdf(src, pages: list[int], dpi: int = 150) -> bytes:
    out = fitz.open()
    try:
        for p in pages:
            page = src.load_page(p)
            pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
            dst = out.new_page(width=page.rect.width, height=page.rect.height)
            dst.insert_image(dst.rect, stream=pix.tobytes("jpeg", jpg_quality=80))
        return out.tobytes(deflate=True)
    finally:
        out.close()


def score_pages(pages: list[str], keywords: dict[str, float]) -> list[tuple[int, float]]:
    """Score pages by weighted keyword hits; returns [(page_index, score)] sorted best-first."""
    scored = []
    for i, text in enumerate(pages):
        low = text.lower()
        s = 0.0
        for kw, w in keywords.items():
            hits = low.count(kw)
            if hits:
                s += w * min(hits, 5)
        scored.append((i, s))
    return sorted(scored, key=lambda t: (-t[1], t[0]))


RESULTS_KEYWORDS = {
    "statement of unaudited": 6,
    "statement of audited": 6,
    "financial results": 3,
    "quarter ended": 3,
    "three months ended": 3,
    "revenue from operations": 4,
    "total income": 2,
    "profit before tax": 4,
    "finance cost": 2,
    "depreciation": 2,
    "net profit": 2,
    "profit for the period": 3,
    "consolidated": 2,
    "earnings per share": 1,
    "interest earned": 3,  # banks
    "operating profit": 2,
}


# Short stems, so a scanned filing's imperfect OCR layer ("revenue from operalions") still matches.
PNL_LINE_ITEMS = [
    "revenue from", "other income", "total income", "finance cost", "depreciation", "before tax",
    "tax expense", "total expenses", "employee benefit", "for the period", "interest earned", "interest expended",
    "before provisions", "provisions (other", "net profit",
]


def pnl_score(text: str) -> tuple[int, float]:
    """(P&L line-item coverage, preference bonus) for one page."""
    low = text.lower()
    coverage = sum(1 for k in PNL_LINE_ITEMS if k in low)
    head = low[:1500]
    bonus = 0.0
    if re.search(r"consol[il1]dat", head):
        bonus += 3
    elif re.search(r"stand[a-z]?alon", head):
        bonus -= 1.5
    if re.search(r"quarter ended|three months ended", low):
        bonus += 2
    if re.search(r"statement of (un)?audited", low):
        bonus += 1
    if re.search(r"us\$|usd|ifrs", low[:2000]):
        bonus -= 3
    return coverage, bonus


def pick_results_pages(pages: list[str], limit: int = 3) -> list[int]:
    """The page(s) carrying the quarterly P&L statement, consolidated first.

    Results filings bundle standalone and consolidated statements, segment tables, press releases and
    sometimes IFRS/USD statements. Mixing lines from different statements is the classic extraction
    error, so pick the pages with the densest set of P&L line items rather than scattered keyword hits.
    """
    cands = []
    for i, text in enumerate(pages[:45]):
        coverage, bonus = pnl_score(text)
        if coverage < 5:
            continue
        cands.append((coverage + bonus, i))
    if not cands:
        scored = [(i, s) for i, s in score_pages(pages, RESULTS_KEYWORDS) if s > 0]
        return sorted(i for i, _ in scored[:4]) or list(range(min(4, len(pages))))
    cands.sort(reverse=True)
    best = cands[0][1]
    chosen = [best]
    # The statement often runs onto the next page (tax, PAT, EPS, other comprehensive income).
    if best + 1 < len(pages) and best + 1 not in chosen:
        chosen.append(best + 1)
    for _, i in cands[1:]:
        if len(chosen) >= limit:
            break
        if i not in chosen:
            chosen.append(i)
    return sorted(chosen[:limit])


def numbered_text(pages: list[str], indices: list[int], max_chars: int) -> str:
    """Concatenate selected pages with [p.N] markers (1-based) so models can cite pages."""
    out, total = [], 0
    for i in indices:
        t = re.sub(r"[ \t]+", " ", pages[i]).strip()
        t = re.sub(r"\n{3,}", "\n\n", t)
        chunk = f"\n[p.{i + 1}]\n{t}\n"
        if total + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - total)]
        out.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return "".join(out)
