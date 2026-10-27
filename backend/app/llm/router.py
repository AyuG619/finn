"""Provider-agnostic LLM router.

Two tiers:
  * ``fast``  - bulk extraction / classification / one-liners. Cheapest capable model.
  * ``smart`` - multi-document reasoning (promise tracking, bull/bear). Stronger model.

Providers: Google Gemini (native PDF + long context + optional Google Search grounding) and Groq
(very fast open models, small per-minute token budgets). Models are discovered from each key at
startup, so the app survives model renames; each call falls back across models/providers on rate
limits, quota exhaustion or bad output. Every call is cached by content hash and metered.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

import httpx

from .. import cache
from ..config import (
    GEMINI_API_KEY,
    GEMINI_MODELS_FAST,
    GEMINI_MODELS_SMART,
    GROQ_API_KEY,
    GROQ_MODELS_FAST,
    GROQ_MODELS_SMART,
)
from .jsonutil import parse_json

log = logging.getLogger("finn.llm")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GROQ_BASE = "https://api.groq.com/openai/v1"


class LLMUnavailable(RuntimeError):
    """No provider could serve the request (no keys, all rate-limited, or all failed)."""


@dataclass
class ModelSlot:
    provider: str  # "gemini" | "groq"
    model: str
    tier: str
    rpm: int
    max_input_tokens: int
    supports_pdf: bool = False
    supports_search: bool = False
    search_only: bool = False  # web-search agents (Groq compound): only for calls that need search
    cooldown_until: float = 0.0
    search_cooldown_until: float = 0.0  # grounding has its own (often zero) free quota
    calls: deque = field(default_factory=deque)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


# List prices (USD per 1M tokens) used only to report what the same work would cost on paid tiers.
PRICE_HINTS = {
    "flash-lite": (0.10, 0.40),
    "flash": (0.30, 2.50),
    "pro": (1.25, 10.0),
    "groq-small": (0.05, 0.08),
    "groq-large": (0.15, 0.75),
}

usage: dict = {
    "calls": 0,
    "cache_hits": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "est_cost_usd": 0.0,
    "by_model": defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0}),
    "by_task": defaultdict(lambda: {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0}),
    "errors": deque(maxlen=30),
}

_slots: dict[str, list[ModelSlot]] = {"fast": [], "smart": []}
_available_models: dict[str, list[str]] = {}
_discovered = False
_discover_lock = asyncio.Lock()
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))
    return _client


def available() -> bool:
    return bool(GEMINI_API_KEY or GROQ_API_KEY)


# ------------------------------------------------------------------ model discovery

_EXCLUDE = re.compile(r"image|tts|audio|live|embed|vision|learnlm|gemma|aqa|imagen|veo|robotics|computer-use|8b|exp-|thinking|nano|customtools", re.I)


def _gemini_version(name: str) -> float:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def rank_gemini(names: list[str], tier: str) -> list[str]:
    """Order discovered Gemini model ids for a tier (best first)."""
    cands = []
    for n in names:
        if _EXCLUDE.search(n):
            continue
        is_lite = "flash-lite" in n
        is_flash = "flash" in n and not is_lite
        is_pro = "-pro" in n
        alias = n.endswith("-latest")
        preview = "preview" in n
        if tier == "fast":
            if not (is_lite or is_flash):
                continue
            family = 0 if is_lite else 1
        else:
            if not (is_flash or is_pro or is_lite):
                continue
            family = 0 if is_flash else (1 if is_pro else 2)
        # Aliases first (Google keeps them pointed at the current release), then stable models
        # newest-first, then previews: previews tend to have tighter free quotas and get retired.
        cands.append(((family, 0 if alias else 1, 1 if preview else 0, -_gemini_version(n), len(n)), n))
    return [n for _, n in sorted(cands)]


GROQ_PREFS = {
    "fast": ["llama-3.1-8b-instant", "meta-llama/llama-4-scout-17b-16e-instruct", "openai/gpt-oss-20b", "llama3-8b-8192"],
    "smart": [
        "openai/gpt-oss-120b",
        "llama-3.3-70b-versatile",
        "moonshotai/kimi-k2-instruct",
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "qwen/qwen3-32b",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    ],
}
# Web-search-capable Groq models: compound systems search on their own; GPT-OSS models take the
# built-in browser_search tool. Which exist depends on the key, so they are discovered like the rest.
GROQ_SEARCH_MODELS = ["groq/compound-mini", "groq/compound", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]
# Free-tier tokens-per-minute budgets are the binding constraint on Groq; keep requests well inside.
GROQ_INPUT_BUDGET = {
    "llama-3.1-8b-instant": 4500,
    "meta-llama/llama-4-scout-17b-16e-instruct": 20000,
    "openai/gpt-oss-20b": 6000,
    "openai/gpt-oss-120b": 6000,
    "llama-3.3-70b-versatile": 9000,
    "moonshotai/kimi-k2-instruct": 8000,
    "meta-llama/llama-4-maverick-17b-128e-instruct": 4500,
    "qwen/qwen3-32b": 4500,
}


async def _list_gemini() -> list[str]:
    r = await _http().get(f"{GEMINI_BASE}/models", params={"pageSize": 1000}, headers={"x-goog-api-key": GEMINI_API_KEY})
    r.raise_for_status()
    out = []
    for m in r.json().get("models", []):
        if "generateContent" in (m.get("supportedGenerationMethods") or []):
            out.append(m["name"].split("/", 1)[-1])
    return out


async def _list_groq() -> list[str]:
    r = await _http().get(f"{GROQ_BASE}/models", headers={"Authorization": f"Bearer {GROQ_API_KEY}"})
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", []) if m.get("active", True)]


def _split(env: str) -> list[str]:
    return [s.strip() for s in env.split(",") if s.strip()]


async def discover(force: bool = False) -> dict:
    """Build the per-tier model ladder from what the configured keys can actually use."""
    global _discovered
    async with _discover_lock:
        if _discovered and not force:
            return status()
        fast: list[ModelSlot] = []
        smart: list[ModelSlot] = []
        if GEMINI_API_KEY:
            try:
                names = await _list_gemini()
                _available_models["gemini"] = sorted(names)
            except Exception as exc:  # noqa: BLE001
                log.warning("Gemini model listing failed: %s", exc)
                usage["errors"].append({"t": time.time(), "where": "gemini:list", "error": str(exc)[:300]})
                names = []
            f_names = _split(GEMINI_MODELS_FAST) or rank_gemini(names, "fast")[:3] or ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
            s_names = _split(GEMINI_MODELS_SMART) or rank_gemini(names, "smart")[:3] or ["gemini-2.5-flash"]
            for n in f_names:
                fast.append(ModelSlot("gemini", n, "fast", rpm=14 if "lite" in n else 9, max_input_tokens=900_000, supports_pdf=True, supports_search=True))
            for n in s_names:
                smart.append(ModelSlot("gemini", n, "smart", rpm=9 if "pro" not in n else 4, max_input_tokens=900_000, supports_pdf=True, supports_search=True))
        if GROQ_API_KEY:
            try:
                gnames = set(await _list_groq())
                _available_models["groq"] = sorted(gnames)
            except Exception as exc:  # noqa: BLE001
                log.warning("Groq model listing failed: %s", exc)
                usage["errors"].append({"t": time.time(), "where": "groq:list", "error": str(exc)[:300]})
                gnames = set()
            for tier, target in (("fast", fast), ("smart", smart)):
                prefs = _split(GROQ_MODELS_FAST if tier == "fast" else GROQ_MODELS_SMART) or GROQ_PREFS[tier]
                chosen = [p for p in prefs if not gnames or p in gnames][:3]
                for n in chosen:
                    target.append(ModelSlot("groq", n, tier, rpm=28, max_input_tokens=GROQ_INPUT_BUDGET.get(n, 5000)))
            # Groq's compound systems run their own web search: the fallback when Google grounding's
            # free quota is exhausted. Used only for calls that ask for search.
            for n in GROQ_SEARCH_MODELS:
                if n in gnames:
                    for tier, target in (("fast", fast), ("smart", smart)):
                        target.append(ModelSlot("groq", n, tier, rpm=12, max_input_tokens=6000, supports_search=True, search_only=True))
        # Each tier can borrow the other tier's models as a last resort.
        _slots["fast"] = fast + [s for s in smart if s.provider == "gemini"]
        _slots["smart"] = smart + [s for s in fast if s.provider == "gemini"]
        _discovered = True
        log.info("LLM ladder fast=%s smart=%s", [s.key for s in _slots["fast"]], [s.key for s in _slots["smart"]])
        return status()


def status() -> dict:
    now = time.time()
    return {
        "configured": {"gemini": bool(GEMINI_API_KEY), "groq": bool(GROQ_API_KEY)},
        "tiers": {
            t: [{"model": s.key, "cooling_s": max(0, round(s.cooldown_until - now)), "search_cooling_s": max(0, round(s.search_cooldown_until - now))} for s in slots]
            for t, slots in _slots.items()
        },
    }


# ------------------------------------------------------------------ helpers

def estimate_tokens(text: str) -> int:
    return int(len(text) / 3.6) + 1


def _price_key(slot: ModelSlot) -> str:
    if slot.provider == "groq":
        return "groq-large" if any(x in slot.model for x in ("70b", "120b", "kimi", "maverick", "compound")) else "groq-small"
    if "flash-lite" in slot.model:
        return "flash-lite"
    if "pro" in slot.model:
        return "pro"
    return "flash"


def _meter(task: str, slot: ModelSlot, tin: int, tout: int) -> None:
    pin, pout = PRICE_HINTS[_price_key(slot)]
    usage["calls"] += 1
    usage["input_tokens"] += tin
    usage["output_tokens"] += tout
    usage["est_cost_usd"] += (tin * pin + tout * pout) / 1_000_000
    bm = usage["by_model"][slot.key]
    bm["calls"] += 1
    bm["input_tokens"] += tin
    bm["output_tokens"] += tout
    bt = usage["by_task"][task]
    bt["calls"] += 1
    bt["input_tokens"] += tin
    bt["output_tokens"] += tout


def usage_report() -> dict:
    return {
        "models_on_keys": _available_models,
        "calls": usage["calls"],
        "cache_hits": usage["cache_hits"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "est_cost_usd_at_list_price": round(usage["est_cost_usd"], 4),
        "by_model": dict(usage["by_model"]),
        "by_task": dict(usage["by_task"]),
        "recent_errors": list(usage["errors"])[-10:],
    }


async def _pace(slot: ModelSlot) -> None:
    """Keep each model under its requests-per-minute budget."""
    while True:
        now = time.time()
        while slot.calls and now - slot.calls[0] > 60:
            slot.calls.popleft()
        if len(slot.calls) < slot.rpm:
            slot.calls.append(now)
            return
        await asyncio.sleep(max(0.2, 60 - (now - slot.calls[0]) + 0.1))


class _RateLimited(Exception):
    def __init__(self, retry_after: float, daily: bool = False):
        super().__init__(f"rate limited ({retry_after:.0f}s, daily={daily})")
        self.retry_after = retry_after
        self.daily = daily


class _BadRequest(Exception):
    pass


def _retry_after_from_gemini(body: dict) -> tuple[float, bool]:
    delay, daily = 30.0, False
    for d in (body.get("error") or {}).get("details") or []:
        if d.get("@type", "").endswith("RetryInfo") and d.get("retryDelay"):
            try:
                delay = float(str(d["retryDelay"]).rstrip("s"))
            except ValueError:
                pass
        if d.get("@type", "").endswith("QuotaFailure"):
            for v in d.get("violations") or []:
                if "PerDay" in (v.get("quotaId") or ""):
                    daily = True
    return delay, daily


# ------------------------------------------------------------------ provider calls

async def _call_gemini(slot: ModelSlot, system: str, prompt: str, *, json_mode: bool, schema: dict | None,
                       pdf: bytes | None, search: bool, max_tokens: int, temperature: float) -> tuple[str, int, int, list]:
    parts: list[dict] = []
    if pdf is not None:
        parts.append({"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(pdf).decode()}})
    parts.append({"text": prompt})
    gen: dict = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if json_mode and not search:
        gen["responseMimeType"] = "application/json"
        if schema:
            gen["responseSchema"] = schema
    body: dict = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen,
    }
    if search:
        body["tools"] = [{"google_search": {}}]
    # Fast-tier 2.x flash models think by default; turn it off for bulk extraction to save tokens.
    if slot.tier == "fast" and re.search(r"gemini-2\.\d-flash(?!-lite)", slot.model):
        gen["thinkingConfig"] = {"thinkingBudget": 0}
    url = f"{GEMINI_BASE}/models/{slot.model}:generateContent"
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    r = await _http().post(url, json=body, headers=headers)
    if r.status_code == 400 and ("thinking" in r.text.lower() or "responseschema" in r.text.lower()):
        gen.pop("thinkingConfig", None)
        gen.pop("responseSchema", None)
        r = await _http().post(url, json=body, headers=headers)
    if r.status_code == 429:
        try:
            delay, daily = _retry_after_from_gemini(r.json())
        except ValueError:
            delay, daily = 30.0, False
        raise _RateLimited(delay, daily)
    if r.status_code in (500, 502, 503, 504):
        raise _RateLimited(8.0)
    if r.status_code != 200:
        raise _BadRequest(f"gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise _BadRequest(f"gemini returned no candidates: {json.dumps(data)[:300]}")
    cand = cands[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [] if not p.get("thought"))
    if not text.strip():
        raise _BadRequest(f"gemini empty output (finishReason={cand.get('finishReason')})")
    um = data.get("usageMetadata") or {}
    tin = int(um.get("promptTokenCount") or 0)
    tout = int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0)
    sources = []
    gm = cand.get("groundingMetadata") or {}
    for ch in gm.get("groundingChunks") or []:
        web = ch.get("web") or {}
        if web.get("uri"):
            sources.append({"title": web.get("title") or "source", "url": web["uri"]})
    return text, tin, tout, sources


async def _call_groq(slot: ModelSlot, system: str, prompt: str, *, json_mode: bool, max_tokens: int,
                     temperature: float, search: bool = False) -> tuple[str, int, int, list]:
    agent = "compound" in slot.model
    body: dict = {
        "model": slot.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    if not agent:  # compound systems take a minimal request; the prompt itself asks for JSON
        body["temperature"] = temperature
        body["max_completion_tokens"] = max_tokens + (1500 if search else 0)
        if json_mode and not search:
            body["response_format"] = {"type": "json_object"}
    if "gpt-oss" in slot.model:
        body["reasoning_effort"] = "low" if slot.tier == "fast" or search else "medium"
        if search:
            body["tools"] = [{"type": "browser_search"}]
            body["tool_choice"] = "required"
    r = await _http().post(
        f"{GROQ_BASE}/chat/completions", json=body, headers={"Authorization": f"Bearer {GROQ_API_KEY}"}
    )
    if r.status_code == 429:
        ra = r.headers.get("retry-after")
        try:
            delay = float(ra) if ra else 20.0
        except ValueError:
            delay = 20.0
        daily = "per day" in r.text.lower() or "tpd" in r.text.lower() or "rpd" in r.text.lower()
        raise _RateLimited(delay, daily)
    if r.status_code == 413:
        raise _BadRequest("groq: request too large for this model's budget")
    if r.status_code in (500, 502, 503, 504):
        raise _RateLimited(8.0)
    if r.status_code != 200:
        raise _BadRequest(f"groq {r.status_code}: {r.text[:300]}")
    data = r.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    text = msg.get("content") or ""
    if not text.strip():
        raise _BadRequest("groq empty output")
    u = data.get("usage") or {}
    return text, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), groq_sources(msg) if (agent or search) else []


def groq_sources(msg: dict) -> list[dict]:
    """Web pages a Groq search call read. They sit under message.executed_tools (search_results.results[]
    for compound and browser_search); walk the structure rather than trust one exact shape."""
    out, seen = [], set()

    def walk(node, depth=0):
        if depth > 6 or len(out) >= 8:
            return
        if isinstance(node, dict):
            url = node.get("url")
            if isinstance(url, str) and url.startswith("http") and url not in seen:
                seen.add(url)
                out.append({"title": str(node.get("title") or "source")[:200], "url": url})
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    walk(msg.get("executed_tools") or [])
    return out[:8]


# ------------------------------------------------------------------ public API

@dataclass
class LLMResult:
    text: str
    data: object | None
    model: str
    cached: bool
    input_tokens: int = 0
    output_tokens: int = 0
    sources: list = field(default_factory=list)

    def to_meta(self) -> dict:
        return {"model": self.model, "cached": self.cached, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


def _cache_key(task: str, system: str, prompt: str, document: str, pdf: bytes | None, schema: dict | None, search: bool, tier: str) -> str:
    h = hashlib.sha256()
    for part in (task, tier, system, prompt, document, json.dumps(schema, sort_keys=True) if schema else "", "S" if search else ""):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    if pdf is not None:
        h.update(hashlib.sha256(pdf).digest())
    return "llm:" + h.hexdigest()


def _fit_document(document: str, budget_tokens: int, fixed: str) -> str:
    room = budget_tokens - estimate_tokens(fixed) - 200
    if room <= 0:
        return ""
    max_chars = int(room * 3.6)
    if len(document) <= max_chars:
        return document
    return document[:max_chars] + "\n[...truncated to fit model budget...]"


async def complete(
    task: str,
    *,
    system: str,
    prompt: str,
    document: str = "",
    tier: str = "fast",
    json_mode: bool = True,
    schema: dict | None = None,
    pdf: bytes | None = None,
    search: bool = False,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    validate=None,
    cache_ttl: float = cache.FOREVER,
) -> LLMResult:
    """Run a task on the best available model for ``tier`` with caching and fallbacks.

    ``document`` is the long source text; it is placed after ``prompt`` and truncated to fit
    smaller models. ``pdf`` (bytes) requires a PDF-capable provider. ``validate`` is an optional
    callable that raises on semantically bad JSON, triggering the next fallback.
    """
    if not available():
        raise LLMUnavailable("no LLM API key configured")
    if not _discovered:
        await discover()
    key = _cache_key(task, system, prompt, document, pdf, schema, search, tier)
    hit = cache.get(key, cache_ttl)
    if hit is not None:
        usage["cache_hits"] += 1
        usage["by_task"][task]["cache_hits"] += 1
        return LLMResult(hit["text"], hit.get("data"), hit["model"], True, hit.get("in", 0), hit.get("out", 0), hit.get("sources", []))

    ladder = [s for s in _slots.get(tier, []) if (pdf is None or s.supports_pdf) and (s.supports_search if search else not s.search_only)]
    if not ladder:
        raise LLMUnavailable(f"no model available for tier={tier} pdf={pdf is not None} search={search}")
    errors: list[str] = []
    for round_ in range(2):
        for slot in ladder:
            now = time.time()
            if slot.cooldown_until > now or (search and slot.search_cooldown_until > now):
                continue
            doc = _fit_document(document, slot.max_input_tokens, system + prompt) if document else ""
            if document and len(doc) < min(len(document), 1500):
                errors.append(f"{slot.key}: document does not fit")
                continue
            full_prompt = prompt + ("\n\n<document>\n" + doc + "\n</document>" if doc else "")
            await _pace(slot)
            try:
                if slot.provider == "gemini":
                    text, tin, tout, sources = await _call_gemini(
                        slot, system, full_prompt, json_mode=json_mode, schema=schema, pdf=pdf, search=search,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                else:
                    text, tin, tout, sources = await _call_groq(
                        slot, system, full_prompt, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature, search=search
                    )
            except _RateLimited as rl:
                pause = 3600 if rl.daily else min(max(rl.retry_after, 5), 120)
                if search:
                    # A 429 on a grounded call is about the search quota, not the model: don't starve plain calls.
                    slot.search_cooldown_until = time.time() + max(pause, 900)
                else:
                    slot.cooldown_until = time.time() + pause
                errors.append(f"{slot.key}: {rl}")
                usage["errors"].append({"t": time.time(), "where": slot.key, "task": task, "error": str(rl)})
                continue
            except (_BadRequest, httpx.HTTPError) as exc:
                errors.append(f"{slot.key}: {exc}")
                usage["errors"].append({"t": time.time(), "where": slot.key, "task": task, "error": str(exc)[:300]})
                continue
            _meter(task, slot, tin or estimate_tokens(full_prompt), tout or estimate_tokens(text))
            data = None
            if json_mode:
                try:
                    data = parse_json(text)
                    if validate is not None:
                        validate(data)
                except Exception as exc:  # noqa: BLE001 - bad output -> next model
                    errors.append(f"{slot.key}: invalid output: {exc}")
                    usage["errors"].append({"t": time.time(), "where": slot.key, "task": task, "error": f"invalid output: {str(exc)[:200]}",
                                            "sample": text[:400]})
                    continue
            cache.put(key, {"text": text, "data": data, "model": slot.key, "in": tin, "out": tout, "sources": sources})
            return LLMResult(text, data, slot.key, False, tin, tout, sources)
        # Everything is cooling down: wait for the soonest model once, then try again.
        soonest = min((max(s.cooldown_until, s.search_cooldown_until if search else 0) for s in ladder), default=0) - time.time()
        if round_ == 0 and 0 < soonest <= 65:
            await asyncio.sleep(soonest + 0.5)
            continue
        break
    raise LLMUnavailable("; ".join(errors[-6:]) or "all models unavailable")
