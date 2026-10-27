"""Keyless web search used only for push-side enrichment, e.g. the professional background of an
incoming executive. Never used by the research (pull) side.

Engines are tried in order; one that can't be reached from the host (DuckDuckGo times out from some
cloud regions) is moved to the back for half an hour so it doesn't cost every lookup a timeout.
"""
from __future__ import annotations

import base64
import html as _html
import logging
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse

from .. import cache, http

log = logging.getLogger("finn.websearch")

ENGINES = ("bing", "ddg_html", "ddg_lite")
_down_until: dict[str, float] = {}
TIMEOUT = 8.0


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _real_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    q = parse_qs(urlparse(href).query)
    if "uddg" in q:
        return unquote(q["uddg"][0])
    return href


def _bing_url(href: str) -> str:
    """Bing wraps results as bing.com/ck/a?...&u=a1<base64url(target)>; unwrap to the target."""
    href = _html.unescape(href)
    if "bing.com/ck/" not in href:
        return href
    u = (parse_qs(urlparse(href).query).get("u") or [""])[0]
    if u.startswith("a1"):
        raw = u[2:]
        try:
            return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return href
    return href


def parse_results(page: str) -> list[dict]:
    """DuckDuckGo (html or lite layout)."""
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', page, re.S):
        url = _real_url(_html.unescape(m.group(1)))
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
        out.append({"title": _clean(m.group(2)), "url": url, "snippet": _clean(snip.group(1)) if snip else ""})
    if out:
        return [r for r in out if r["url"].startswith("http") and "duckduckgo.com" not in r["url"]]
    for m in re.finditer(r'<a[^>]+class=[\'"]result-link[\'"][^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<td class=[\'"]result-snippet[\'"]>(.*?)</td>', page, re.S):
        out.append({"title": _clean(m.group(2)), "url": _real_url(_html.unescape(m.group(1))), "snippet": _clean(m.group(3))})
    return [r for r in out if r["url"].startswith("http") and "duckduckgo.com" not in r["url"]]


def parse_bing(page: str) -> list[dict]:
    out = []
    for block in re.findall(r'<li class="b_algo"(.*?)</li>\s*(?=<li class="b_|</ol>)', page, re.S):
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        snip = re.search(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.S) or re.search(
            r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', block, re.S)
        url = _bing_url(a.group(1))
        if url.startswith("http") and "bing.com/" not in url:
            out.append({"title": _clean(a.group(2)), "url": url, "snippet": _clean(snip.group(1)) if snip else ""})
    return out


async def _engine(name: str, query: str) -> list[dict]:
    if name == "bing":
        url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en-IN&cc=IN&count=10"
        resp = await http.request("GET", url, retries=0, timeout=TIMEOUT)
        return parse_bing(resp.text)
    ep = "https://html.duckduckgo.com/html/" if name == "ddg_html" else "https://lite.duckduckgo.com/lite/"
    resp = await http.request("POST", ep, retries=0, timeout=TIMEOUT, content=urlencode({"q": query}).encode(),
                              headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": ep})
    return parse_results(resp.text)


def _order() -> list[str]:
    now = time.time()
    return sorted(ENGINES, key=lambda e: (_down_until.get(e, 0) > now, ENGINES.index(e)))


async def search(query: str, limit: int = 6) -> list[dict]:
    async def fetch():
        last = None
        for name in _order():
            try:
                res = await _engine(name, query)
            except Exception as exc:  # noqa: BLE001
                last = exc
                _down_until[name] = time.time() + 1800
                log.info("web search engine %s failed: %s", name, str(exc)[:160])
                continue
            if res:
                return res[:limit]
        # Nothing from any engine (unreachable, or a bot wall that parses as empty): don't cache that.
        raise last or RuntimeError("no web results")

    return await cache.cached(f"websearch:{query.lower()}", 7 * 24 * 3600, fetch)


def engine_status() -> dict:
    now = time.time()
    return {e: ("down for %ds" % (_down_until[e] - now)) if _down_until.get(e, 0) > now else "ok" for e in ENGINES}
