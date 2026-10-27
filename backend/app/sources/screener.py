"""screener.in: standardised multi-year financials + links to primary documents.

screener consolidates each company's filings (quarterly results, annual reports, concall
transcripts) and standardises the numbers. FINN uses it for (a) the numeric tables, which feed
deterministic analysis code, and (b) the *links* to primary documents (annual reports and
transcripts), which the LLM reads. screener's own AI summaries and commentary are deliberately
ignored: the pull side must stay grounded in primary sources.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .. import cache, http

log = logging.getLogger("finn.screener")

BASE = "https://www.screener.in"
MONTHS = {m: i for i, m in enumerate(calendar.month_abbr) if m}


class CompanyNotFound(LookupError):
    pass


# ---------------------------------------------------------------- number helpers

_num_re = re.compile(r"-?\d[\d,]*\.?\d*")


def parse_number(text: str | None) -> float | None:
    """'1,23,456' -> 123456.0, '18%' -> 18.0, '-1,234' -> -1234.0, '' -> None."""
    if text is None:
        return None
    t = text.replace("\xa0", " ").replace("₹", "").strip()
    if not t or t in {"-", "--", "NA", "N/A"}:
        return None
    m = _num_re.search(t)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def period_key_from_label(label: str) -> str | None:
    """'Sep 2026' -> '2026-09-30'."""
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", label.strip())
    if not m or m.group(1).title() not in MONTHS:
        return None
    month, year = MONTHS[m.group(1).title()], int(m.group(2))
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def fy_quarter(period_key: str) -> str:
    """Indian fiscal quarter label for a quarter-end date: '2026-06-30' -> 'Q1 FY27'."""
    y, m = int(period_key[:4]), int(period_key[5:7])
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(m)
    if q is None:
        return period_key
    fy = y + 1 if m >= 4 else y
    return f"Q{q} FY{fy % 100:02d}"


def fy_label(period_key: str) -> str:
    """Fiscal-year label for a March year-end: '2026-03-31' -> 'FY26'."""
    if period_key == "TTM":
        return "TTM"
    y, m = int(period_key[:4]), int(period_key[5:7])
    fy = y + 1 if m >= 4 else y
    return f"FY{fy % 100:02d}"


# ---------------------------------------------------------------- HTML parsing

def _clean_label(td: Tag) -> str:
    td = BeautifulSoup(str(td), "lxml")  # copy so we can mutate
    for t in td.select(".has-tooltip, .tooltip"):
        t.decompose()
    text = td.get_text(" ", strip=True).replace("\xa0", " ")
    text = re.sub(r"\s*\+\s*$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_table(table: Tag | None) -> dict | None:
    if table is None:
        return None
    heads = table.select("thead th")[1:]
    periods, keys = [], []
    for th in heads:
        label = th.get_text(" ", strip=True)
        periods.append(label)
        keys.append(th.get("data-date-key") or period_key_from_label(label) or label)
    rows: dict[str, list[float | None]] = {}
    order: list[str] = []
    links: dict[str, list[str | None]] = {}
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        label = _clean_label(cells[0])
        if not label:
            continue
        if label == "Raw PDF":
            links["raw_pdf"] = [
                urljoin(BASE, a["href"]) if (a := c.find("a", href=True)) else None for c in cells[1:]
            ]
            continue
        vals = [parse_number(c.get_text(" ", strip=True)) for c in cells[1:]]
        vals += [None] * (len(keys) - len(vals))
        rows[label] = vals[: len(keys)]
        order.append(label)
    if not rows:
        return None
    return {"periods": periods, "keys": keys, "rows": rows, "order": order, "links": links}


def _ranges(section: Tag | None) -> dict:
    out: dict[str, dict[str, float | None]] = {}
    if section is None:
        return out
    for t in section.select("table.ranges-table"):
        th = t.find("th")
        if not th:
            continue
        title = th.get_text(" ", strip=True)
        vals = {}
        for tr in t.select("tr"):
            tds = tr.find_all("td")
            if len(tds) == 2:
                vals[tds[0].get_text(strip=True).rstrip(":")] = parse_number(tds[1].get_text(strip=True))
        out[title] = vals
    return out


def _top_ratios(soup: BeautifulSoup) -> dict:
    out: dict[str, object] = {}
    for li in soup.select("#top-ratios li"):
        name_el = li.select_one(".name")
        if not name_el:
            continue
        name = name_el.get_text(" ", strip=True)
        nums = [parse_number(n.get_text()) for n in li.select(".number")]
        if name == "High / Low" and len(nums) == 2:
            out["high"], out["low"] = nums
            continue
        key = {
            "Market Cap": "market_cap_cr",
            "Current Price": "price",
            "Stock P/E": "pe",
            "Book Value": "book_value",
            "Dividend Yield": "dividend_yield",
            "ROCE": "roce",
            "ROE": "roe",
            "Face Value": "face_value",
        }.get(name, re.sub(r"\W+", "_", name.lower()).strip("_"))
        out[key] = nums[0] if nums else None
    return out


def _documents(soup: BeautifulSoup) -> dict:
    docs: dict[str, list] = {"annual_reports": [], "concalls": [], "announcements": []}
    for li in soup.select("#documents .annual-reports li"):
        a = li.find("a", href=True)
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        m = re.search(r"(\d{4})", title)
        src = li.select_one(".smaller")
        docs["annual_reports"].append(
            {
                "title": title,
                "year": int(m.group(1)) if m else None,
                "url": urljoin(BASE, a["href"]),
                "source": src.get_text(strip=True).replace("from ", "") if src else None,
                "is_pdf": a["href"].lower().split("?")[0].endswith(".pdf"),
            }
        )
    seen_transcripts: set[str] = set()
    for li in soup.select("#documents .concalls li"):
        label_el = li.find("div")
        label = label_el.get_text(" ", strip=True) if label_el else ""
        item = {"label": label, "date_key": period_key_from_label(label), "transcript": None, "ppt": None, "rec": None}
        for a in li.find_all("a", href=True):
            text = a.get_text(" ", strip=True).lower()
            url = urljoin(BASE, a["href"])
            if text == "transcript" or (a.get("title") or "").lower() == "raw transcript":
                item["transcript"] = url
            elif text == "ppt":
                item["ppt"] = url
            elif text == "rec":
                item["rec"] = url
        if item["transcript"]:
            if item["transcript"] in seen_transcripts:
                item["transcript"] = None  # screener sometimes lists the same transcript twice
            else:
                seen_transcripts.add(item["transcript"])
        if item["transcript"] or item["ppt"]:
            docs["concalls"].append(item)
    for li in soup.select("#documents #company-announcements-tab li"):
        a = li.find("a", href=True)
        if not a:
            continue
        t = li.find("time")
        sub = a.find("div")
        title_text = a.get_text(" ", strip=True)
        if sub:
            title_text = title_text.replace(sub.get_text(" ", strip=True), "").strip()
        docs["announcements"].append(
            {"title": title_text, "url": urljoin(BASE, a["href"]), "time": t.get("datetime") if t else None}
        )
    return docs


def parse_company_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    wrapper = soup.select_one("[data-company-id]")
    name_el = soup.select_one("#top h1 span.min-width-0") or soup.select_one("#top h1")
    bse_code = nse_symbol = website = None
    for a in soup.select("#top a[href]"):
        txt = a.get_text(" ", strip=True)
        if (m := re.search(r"BSE:\s*(\d{6})", txt)):
            bse_code = m.group(1)
        elif (m := re.search(r"NSE:\s*([A-Z0-9&\-_.]+)", txt)):
            nse_symbol = m.group(1)
        elif "icon-link" in str(a) and "bseindia" not in a["href"] and "nseindia" not in a["href"] and website is None:
            website = a["href"]
    sector = {a.get("title"): a.get_text(" ", strip=True) for a in soup.select("#peers p.sub a[title]")}
    about_el = soup.select_one("#top .about")
    sub = soup.select_one("#quarters p.sub")
    basis = "consolidated" if sub and "Consolidated" in sub.get_text() else "standalone"
    shp_table = soup.select_one("#quarterly-shp table") or soup.select_one("#shareholding table")

    def section_table(sec_id: str) -> Tag | None:
        sec = soup.select_one(f"#{sec_id}")
        return sec.select_one("table.data-table") if sec else None

    quarters = parse_table(section_table("quarters"))
    profit_loss = parse_table(section_table("profit-loss"))
    data = {
        "url": url,
        "name": name_el.get_text(" ", strip=True) if name_el else None,
        "company_id": wrapper.get("data-company-id") if wrapper else None,
        "warehouse_id": wrapper.get("data-warehouse-id") if wrapper else None,
        "bse_code": bse_code,
        "nse_symbol": nse_symbol,
        "website": website,
        "sector": sector,
        "about": about_el.get_text(" ", strip=True) if about_el else None,
        "basis": basis,
        "ratios": _top_ratios(soup),
        "quarters": quarters,
        "profit_loss": profit_loss,
        "balance_sheet": parse_table(section_table("balance-sheet")),
        "cash_flow": parse_table(section_table("cash-flow")),
        "ratios_table": parse_table(section_table("ratios")),
        "shareholding": parse_table(shp_table),
        "growth": _ranges(soup.select_one("#profit-loss")),
        "documents": _documents(soup),
    }
    data["is_bank"] = bool(quarters and ("Financing Profit" in quarters["rows"] or "Gross NPA %" in quarters["rows"]))
    return data


def _revenue_points(data: dict) -> int:
    q = data.get("quarters") or {}
    rows = q.get("rows") or {}
    series = rows.get("Sales") or rows.get("Revenue") or []
    return sum(1 for v in series if v is not None)


def normalise_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    for suffix in (".NS", ".BO", "-EQ"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


async def _fetch_page(path: str) -> tuple[str, str]:
    url = f"{BASE}{path}"
    try:
        resp = await http.request("GET", url, retries=2)
    except http.FetchError as exc:
        if exc.status == 404:
            raise CompanyNotFound(path) from exc
        raise
    return resp.text, str(resp.url)


async def company_page(symbol: str) -> dict:
    """Parsed screener page for an NSE symbol (or BSE code). Consolidated when available."""
    sym = normalise_symbol(symbol)

    async def fetch() -> dict:
        html, url = await _fetch_page(f"/company/{sym}/consolidated/")
        data = parse_company_page(html, url)
        if _revenue_points(data) < 3:
            try:
                html2, url2 = await _fetch_page(f"/company/{sym}/")
                alt = parse_company_page(html2, url2)
                if _revenue_points(alt) > _revenue_points(data):
                    data = alt
            except CompanyNotFound:
                pass
        if not data.get("quarters") and not data.get("profit_loss"):
            raise CompanyNotFound(sym)
        data["symbol"] = data.get("nse_symbol") or sym
        return data

    return await cache.cached(f"screener:page:{sym}", 6 * 3600, fetch, swr=24 * 3600)


async def company_ids(symbol: str) -> dict:
    page = await company_page(symbol)
    return {k: page.get(k) for k in ("company_id", "warehouse_id", "bse_code", "nse_symbol", "name", "symbol")}


async def search(query: str, limit: int = 8) -> list[dict]:
    q = query.strip()
    if not q:
        return []

    async def fetch() -> list[dict]:
        payload = await http.get_json(
            f"{BASE}/api/company/search/", params={"q": q, "v": "3", "fts": "1"}, headers={"Accept": "application/json"}
        )
        out = []
        for item in payload or []:
            url = item.get("url") or ""
            m = re.match(r"^/company/([^/]+)/", url)
            if not item.get("id") or not m or m.group(1) == "id":
                continue
            out.append({"symbol": m.group(1).upper(), "name": item.get("name"), "screener_id": item.get("id")})
        return out[:limit]

    return await cache.cached(f"screener:search:{q.lower()}", 24 * 3600, fetch)
