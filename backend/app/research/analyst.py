"""Narrative vs numbers: what management promised, what was delivered.

Map:    each transcript and the annual report are read by the fast model into structured notes.
Check:  every quantitative, company-level promise whose period has data is scored by CODE against
        reported numbers (no model judgement on arithmetic).
Reduce: the smart model reads the notes + numbers + computed verdicts and writes the qualitative
        verdicts, narrative-vs-numbers, current guidance and the bull/bear case.
"""
from __future__ import annotations

import re

from ..sources.screener import fy_quarter

_FY_RE = re.compile(r"FY\s*'?(\d{4}|\d{2})", re.I)
_Q_RE = re.compile(r"\bQ([1-4])\s*(?:of\s*)?FY\s*'?(\d{4}|\d{2})", re.I)
_H_RE = re.compile(r"\bH([12])\s*FY\s*'?(\d{4}|\d{2})", re.I)
FX_TERMS = re.compile(r"constant currency|\bcc\b|usd|dollar|\$", re.I)


def _fy(y: str) -> int:
    v = int(y)
    return v % 100 if v >= 2000 else v


def fy_of_quarter_label(label: str) -> int | None:
    m = re.search(r"FY(\d{2})", label or "")
    return int(m.group(1)) if m else None


def resolve_period(period: str, made_in: str) -> tuple[str, int, list[int]] | None:
    """('FY'|'Q'|'H', fiscal_year_2digit, quarters[1..4]) or None if not a concrete fiscal period."""
    p = (period or "").strip()
    if m := _Q_RE.search(p):
        return "Q", _fy(m.group(2)), [int(m.group(1))]
    if m := _H_RE.search(p):
        return "H", _fy(m.group(2)), [1, 2] if m.group(1) == "1" else [3, 4]
    if m := _FY_RE.search(p):
        return "FY", _fy(m.group(1)), [1, 2, 3, 4]
    base = fy_of_quarter_label(made_in)
    low = p.lower()
    if base and re.search(r"this (fiscal|financial )?year|current (fiscal|financial )?year|full[- ]year|for the year", low):
        return "FY", base, [1, 2, 3, 4]
    if base and re.search(r"next (fiscal|financial )?year", low):
        return "FY", base + 1, [1, 2, 3, 4]
    return None


def _qmap(quarters: list[dict]) -> dict[str, dict]:
    return {q["label"]: q for q in quarters}


def _sum(qm: dict, labels: list[str], key: str) -> float | None:
    vals = [qm.get(l, {}).get(key) for l in labels]
    if not vals or any(v is None for v in vals):
        return None
    return sum(vals)


def measure(item: dict, made_in: str, quarters: list[dict], annual: list[dict]) -> dict | None:
    """Compute the actual for a guidance item, or None if code can't decide it."""
    metric = item.get("metric")
    if metric not in ("revenue_growth", "pat_growth", "ebitda_margin", "revenue", "pat"):
        return None
    if not item.get("quantitative"):
        return None
    scope = str(item.get("scope") or "company").lower()
    if scope not in ("company", "consolidated", "overall", "total", "group"):
        return None
    text = f"{item.get('statement', '')} {item.get('quote', '')} {item.get('basis') or ''}"
    if FX_TERMS.search(text) or (item.get("basis") not in (None, "reported INR")):
        return None  # constant-currency / USD guidance can't be checked against reported ₹ numbers
    lo, hi = item.get("target_low"), item.get("target_high")
    target = lo if isinstance(lo, (int, float)) else hi if isinstance(hi, (int, float)) else None
    if target is None:
        return None
    unit = item.get("unit")
    if metric in ("revenue_growth", "pat_growth", "ebitda_margin") and unit not in ("%", None):
        return None
    if metric in ("revenue", "pat") and unit != "crore":
        return None
    per = resolve_period(item.get("period", ""), made_in)
    if not per:
        return None
    kind, fy, qs = per
    qm = _qmap(quarters)
    labels_now = [f"Q{q} FY{fy:02d}" for q in qs]
    labels_prev = [f"Q{q} FY{fy - 1:02d}" for q in qs]
    have = [l for l in labels_now if l in qm and qm[l].get("revenue") is not None]
    annual_map = {a["label"]: a for a in annual}
    key = {"revenue_growth": "revenue", "revenue": "revenue", "pat_growth": "pat", "pat": "pat"}.get(metric)
    complete = len(have) == len(labels_now)
    if kind == "FY" and f"FY{fy:02d}" in annual_map and annual_map[f"FY{fy:02d}"].get("revenue") is not None:
        complete = True
        a, b = annual_map[f"FY{fy:02d}"], annual_map.get(f"FY{fy - 1:02d}")
        if metric == "ebitda_margin":
            actual = a.get("opm")
        elif metric in ("revenue", "pat"):
            actual = a.get(key)
        else:
            actual = None if not b or not b.get(key) or b[key] <= 0 or a.get(key) is None else round((a[key] / b[key] - 1) * 100, 1)
        span = f"FY{fy:02d}"
    else:
        if not have:
            return {"status": "pending", "actual": None, "span": None, "evidence": f"No reported quarters of FY{fy:02d} yet."}
        prev_have = [labels_prev[labels_now.index(l)] for l in have]
        if metric == "ebitda_margin":
            op, rev = _sum(qm, have, "op_profit"), _sum(qm, have, "revenue")
            actual = round(op / rev * 100, 1) if op is not None and rev else None
        elif metric in ("revenue", "pat"):
            actual = _sum(qm, have, key) if complete else None
        else:
            cur, prev = _sum(qm, have, key), _sum(qm, prev_have, key)
            actual = round((cur / prev - 1) * 100, 1) if cur is not None and prev and prev > 0 else None
        span = have[0] if len(have) == 1 else f"{have[0]}–{have[-1]}"
    if actual is None:
        return None
    tol = 0.75 if metric == "ebitda_margin" else 0.0
    if metric in ("revenue", "pat"):
        ok = actual >= target * 0.98
        partly = actual >= target * 0.9
    elif metric == "ebitda_margin":
        ok = actual >= target - tol
        partly = actual >= target - 2.0
    else:
        ok = actual >= target - tol
        partly = actual >= target * 0.75
    unit_s = "%" if metric != "revenue" and metric != "pat" else " cr"
    tgt_s = f"{lo:g}–{hi:g}{unit_s}" if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo != hi else f"{target:g}{unit_s}"
    label = {"revenue_growth": "revenue growth", "pat_growth": "PAT growth", "ebitda_margin": "operating margin", "revenue": "revenue", "pat": "PAT"}[metric]
    if not complete:
        status = "pending"
        track = "on track" if ok else "behind"
        evidence = f"{span}: {label} {actual:g}{unit_s} vs {tgt_s} guided ({track}, period not over)"
    else:
        status = "delivered" if ok else ("partly" if partly else "missed")
        evidence = f"{span}: {label} {actual:g}{unit_s} vs {tgt_s} guided"
    if metric == "ebitda_margin":
        evidence += " · margin = operating profit before other income ÷ revenue"
    return {"status": status, "actual": actual, "span": span, "evidence": evidence}


def computed_promises(transcripts: list[dict], quarters: list[dict], annual: list[dict]) -> tuple[list[dict], list[dict]]:
    """(computed promises, remaining guidance items for the model)."""
    computed, rest = [], []
    n = 0
    for t in transcripts:
        notes = t.get("notes") or {}
        made_in = notes.get("quarter_discussed") or t.get("quarter")
        for g in notes.get("guidance") or []:
            if not isinstance(g, dict):
                continue
            n += 1
            gid = f"g{n}"
            g = {**g, "id": gid, "made_in": f"{t['quarter']} call", "made_in_q": t["quarter"], "source": f"{t['quarter']} call p.{g.get('page')}"}
            m = measure(g, t["quarter"], quarters, annual)
            if m:
                computed.append(
                    {
                        "id": gid,
                        "made_in": g["made_in"],
                        "promise": g.get("statement"),
                        "quote": g.get("quote"),
                        "due": m["span"] or g.get("period"),
                        "period": g.get("period"),
                        "outcome": m["status"],
                        "evidence": m["evidence"],
                        "source": g["source"],
                        "computed": True,
                        "metric": g.get("metric"),
                        "actual": m["actual"],
                    }
                )
            else:
                rest.append(g)
    return computed, rest


def material_text(company: str, transcripts: list[dict], ar: dict | None, rest_ids: set[str]) -> str:
    """Compact notes for the reduce prompt (newest call first)."""
    lines: list[str] = []
    for t in transcripts:
        notes = t.get("notes")
        if not notes:
            continue
        lines.append(f"[{t['quarter']} call, held {t['label']}]")
        for h in (notes.get("reported_highlights") or [])[:6]:
            lines.append(f"  reported: {h.get('point')} (p.{h.get('page')})")
        for g in t.get("guidance_ids") or []:
            lines.append(f"  guidance [{g['id']}] {g.get('metric')}: {g.get('statement')} | period {g.get('period')} | scope {g.get('scope') or 'company'} | \"{g.get('quote')}\" (p.{g.get('page')})")
        for s in (notes.get("segments") or [])[:5]:
            lines.append(f"  segment {s.get('name')}: {s.get('comment')} (p.{s.get('page')})")
        for a in (notes.get("analyst_pushback") or [])[:4]:
            lines.append(f"  analysts pressed: {a.get('topic')} -> {a.get('management_answer')} (p.{a.get('page')})")
        tone = notes.get("tone") or {}
        if tone:
            lines.append(f"  tone: {tone.get('label')} - {tone.get('why')}")
    if ar:
        lines.append(f"[{ar.get('title')}]")
        for k in ("business_overview", "revenue_mix_notes", "capex_plans", "contingent_liabilities", "related_party_highlights", "emphasis_of_matter"):
            if ar.get(k):
                lines.append(f"  {k.replace('_', ' ')}: {ar[k]}")
        for s in (ar.get("segments") or [])[:6]:
            lines.append(f"  segment {s.get('name')}: share {s.get('revenue_share_pct')}% {s.get('comment') or ''}")
        for s in (ar.get("strategy_priorities") or [])[:4]:
            lines.append(f"  strategy: {s.get('point')} (p.{s.get('page')})")
        for s in (ar.get("risks_flagged_by_management") or [])[:4]:
            lines.append(f"  risk: {s.get('point')} (p.{s.get('page')})")
        if ar.get("auditor_opinion"):
            lines.append(f"  auditor opinion: {ar['auditor_opinion']}; key audit matters: {', '.join(ar.get('key_audit_matters') or [])}")
    return "\n".join(lines)


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


def merge_promises(computed: list[dict], model_promises: list[dict]) -> list[dict]:
    """Computed verdicts win; model promises that duplicate them are dropped."""
    by_id = {c["id"]: c for c in computed}
    out = list(computed)
    for p in model_promises or []:
        if not isinstance(p, dict) or not p.get("promise"):
            continue
        if p.get("id") in by_id:
            # keep the computed verdict, borrow nothing but a nicer phrasing if ours is empty
            if not by_id[p["id"]].get("promise"):
                by_id[p["id"]]["promise"] = p["promise"]
            continue
        tp = _tokens(p["promise"])
        if any(len(tp & _tokens(c["promise"])) / max(1, len(tp | _tokens(c["promise"]))) > 0.5 for c in computed):
            continue
        if p.get("outcome") not in ("delivered", "missed", "partly", "pending", "revised", "unverifiable"):
            p["outcome"] = "unverifiable"
        p["computed"] = False
        out.append(p)

    def order(p):
        m = re.search(r"Q([1-4]) FY(\d{2})", p.get("made_in") or "")
        return (int(m.group(2)) * 10 + int(m.group(1))) if m else 0

    return sorted(out, key=order, reverse=True)


def credibility(promises: list[dict]) -> dict | None:
    weights = {"delivered": 1.0, "partly": 0.5, "missed": 0.0}
    resolved = [p for p in promises if p.get("outcome") in weights]
    if len(resolved) < 2:
        return None
    score = round(10 * sum(weights[p["outcome"]] for p in resolved) / len(resolved), 1)
    counts = {k: sum(1 for p in resolved if p["outcome"] == k) for k in weights}
    pending = sum(1 for p in promises if p.get("outcome") == "pending")
    return {"score": score, "resolved": len(resolved), "pending": pending, **counts}


def quarter_label_for(qe: str) -> str:
    return fy_quarter(qe)
