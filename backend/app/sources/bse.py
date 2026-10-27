"""BSE corporate announcements (the exchange's own public JSON feed)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from .. import cache, http
from .prices import IST, now_ist

log = logging.getLogger("finn.bse")

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
}
PDF_OPEN = "https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname={name}"
PDF_LIVE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{name}"
PDF_HIS = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/{name}"

_prefix_re = re.compile(r"^.*?\s-\s\d{6}\s-\s")
_reg30_re = re.compile(r"^Announcement under Regulation 30 \(LODR\)\s*-\s*", re.I)


def clean_subject(newssub: str, company: str | None = None) -> str:
    s = (newssub or "").replace("\r", " ").replace("\n", " ").strip()
    s = _prefix_re.sub("", s, count=1)
    s = _reg30_re.sub("", s)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip(" -")


def parse_time(s: str | None) -> str | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.split(".")[0])
    except ValueError:
        return None
    return dt.replace(tzinfo=IST).isoformat()


def normalise(row: dict) -> dict:
    name = row.get("ATTACHMENTNAME") or ""
    pdf = PDF_OPEN.format(name=name) if name.lower().endswith(".pdf") else None
    headline = (row.get("HEADLINE") or "").replace("<BR>", " ").replace("<br>", " ")
    headline = re.sub(r"\s+", " ", headline).strip()
    return {
        "id": f"BSE:{row.get('NEWSID')}",
        "source": "BSE",
        "bse_code": str(row.get("SCRIP_CD") or ""),
        "company": (row.get("SLONGNAME") or "").replace("-$", "").strip(),
        "time": parse_time(row.get("NEWS_DT") or row.get("DT_TM")),
        "subject": clean_subject(row.get("NEWSSUB") or ""),
        "raw_subject": (row.get("NEWSSUB") or "").strip(),
        "detail": headline,
        "exchange_category": (row.get("CATEGORYNAME") or "").strip(),
        "exchange_subcategory": (row.get("SUBCATNAME") or "").strip() if row.get("SUBCATNAME") else "",
        "critical": str(row.get("CRITICALNEWS") or "0") == "1",
        "pdf_url": pdf,
        "attachment": name or None,
        "pdf_size": row.get("Fld_Attachsize"),
    }


def pdf_candidates(attachment: str) -> list[str]:
    return [PDF_LIVE.format(name=attachment), PDF_HIS.format(name=attachment), PDF_OPEN.format(name=attachment)]


async def _fetch_range(code: str, start: datetime, end: datetime) -> list[dict]:
    rows: list[dict] = []
    total = None
    for page in range(1, 21):
        params = {
            "pageno": str(page),
            "strCat": "-1",
            "strPrevDate": start.strftime("%Y%m%d"),
            "strScrip": code,
            "strSearch": "P",
            "strToDate": end.strftime("%Y%m%d"),
            "strType": "C",
            "subcategory": "-1",
        }
        payload = await http.get_json(API, params=params, headers=HEADERS, retries=2)
        table = (payload or {}).get("Table") or []
        if total is None:
            try:
                total = int(((payload or {}).get("Table1") or [{}])[0].get("ROWCNT") or 0)
            except (TypeError, ValueError):
                total = 0
        rows.extend(table)
        if not table or len(rows) >= (total or 0):
            break
    out, seen = [], set()
    for r in rows:
        n = normalise(r)
        key = n["attachment"] or n["id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


async def announcements(code: str, days: int = 30) -> list[dict]:
    """Announcements for one BSE scrip over the last ``days`` days (cached 5 minutes)."""
    end = now_ist()
    start = end - timedelta(days=days)
    key = f"bse:ann:{code}:{days}:{end.strftime('%Y%m%d')}"
    return await cache.cached(key, 300, lambda: _fetch_range(code, start, end), swr=3600)
