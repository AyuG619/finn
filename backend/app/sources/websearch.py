"""Keyless web search (DuckDuckGo's HTML endpoint) used only for push-side enrichment, e.g. the
professional background of an incoming executive. Never used by the research (pull) side."""
from __future__ import annotations

import html as _html
import logging
import re
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from .. import cache, http

log = logging.getLogger("finn.websearch")

ENDPOINTS = ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _real_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    q = parse_qs(urlparse(href).query)
    if "uddg" in q:
        return unquote(q["uddg"][0])
    return href


def parse_results(page: str) -> list[dict]:
    out = []
    # html.duckduckgo.com layout
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', page, re.S):
        url = _real_url(_html.unescape(m.group(1)))
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
        out.append({"title": _clean(m.group(2)), "url": url, "snippet": _clean(snip.group(1)) if snip else ""})
    if out:
        return [r for r in out if r["url"].startswith("http") and "duckduckgo.com" not in r["url"]]
    # lite.duckduckgo.com layout
    for m in re.finditer(r'<a[^>]+class=[\'"]result-link[\'"][^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<td class=[\'"]result-snippet[\'"]>(.*?)</td>', page, re.S):
        out.append({"title": _clean(m.group(2)), "url": _real_url(_html.unescape(m.group(1))), "snippet": _clean(m.group(3))})
    return [r for r in out if r["url"].startswith("http") and "duckduckgo.com" not in r["url"]]


async def search(query: str, limit: int = 6) -> list[dict]:
    async def fetch():
        last = None
        for ep in ENDPOINTS:
            try:
                resp = await http.request("POST", ep, retries=0, content=urlencode({"q": query}).encode(),
                                          headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": ep})
                res = parse_results(resp.text)
                if res:
                    return res[:limit]
            except Exception as exc:  # noqa: BLE001
                last = exc
        if last:
            raise last
        return []

    return await cache.cached(f"websearch:{query.lower()}", 7 * 24 * 3600, fetch)
