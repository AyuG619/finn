"""Lenient JSON extraction for model output."""
from __future__ import annotations

import json
import re

_fence_re = re.compile(r"^```(?:json|JSON)?\s*|\s*```$", re.M)
_trailing_comma_re = re.compile(r",\s*([}\]])")


def _balanced_block(s: str) -> str | None:
    start = None
    for i, ch in enumerate(s):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None
    stack = []
    in_str = False
    esc = False
    for j in range(start, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack.pop() != ch:
                return None
            if not stack:
                return s[start : j + 1]
    return None


def parse_json(text: str):
    """Parse JSON from model output, tolerating code fences, prose and trailing commas."""
    if text is None:
        raise ValueError("empty response")
    t = text.strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    t2 = _fence_re.sub("", t).strip()
    for candidate in (t2, _balanced_block(t2) or ""):
        if not candidate:
            continue
        for c in (candidate, _trailing_comma_re.sub(r"\1", candidate)):
            try:
                return json.loads(c)
            except ValueError:
                continue
    raise ValueError(f"could not parse JSON from model output: {t[:200]!r}")
