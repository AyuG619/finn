"""Deterministic analysis of the standardised financials. No model involved: numbers are computed,
and the plain-English "signals" are produced by rules, so they can't be hallucinated."""
from __future__ import annotations

from ..sources.screener import fy_label, fy_quarter


def _row(table: dict | None, *names: str) -> list | None:
    if not table:
        return None
    rows = table.get("rows") or {}
    for n in names:
        for label, vals in rows.items():
            if label.lower() == n.lower():
                return vals
    for n in names:
        for label, vals in rows.items():
            if label.lower().startswith(n.lower()):
                return vals
    return None


def _series(table: dict | None, *names: str, annual: bool = False) -> list[tuple[str, float | None]]:
    vals = _row(table, *names)
    if not table or vals is None:
        return []
    out = list(zip(table["keys"], vals))
    if annual:
        out = [(k, v) for k, v in out if k != "TTM"]
    return out


def _last(series: list[tuple[str, float | None]]) -> tuple[str | None, float | None]:
    for k, v in reversed(series):
        if v is not None:
            return k, v
    return None, None


def cagr(first: float | None, last: float | None, years: float) -> float | None:
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 1)


def pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b <= 0:
        return None
    return round((a / b - 1) * 100, 1)


def _ttm(table: dict | None, *names: str) -> float | None:
    vals = _row(table, *names)
    if not table or vals is None or "TTM" not in table["keys"]:
        return None
    return vals[table["keys"].index("TTM")]


def _margin(op: float | None, rev: float | None, reported: float | None) -> float | None:
    """Margin from the raw numbers (screener's OPM % row is rounded to whole percent)."""
    if op is not None and rev and rev > 0:
        return round(op / rev * 100, 1)
    return reported


def _sig(tone: str, text: str) -> dict:
    return {"tone": tone, "text": text}


def labels(page: dict) -> dict:
    bank = page.get("is_bank")
    return {
        "revenue": ("Revenue",) if bank else ("Sales", "Revenue"),
        "op": ("Financing Profit",) if bank else ("Operating Profit",),
        "opm": ("Financing Margin %",) if bank else ("OPM %",),
    }


# ------------------------------------------------------------------ quarterly table

def quarter_rows(page: dict) -> list[dict]:
    """Last ~12 quarters with YoY growth, used by charts, the analyst prompt and promise checks."""
    q = page.get("quarters")
    if not q:
        return []
    L = labels(page)
    rev = _row(q, *L["revenue"]) or []
    op = _row(q, *L["op"]) or []
    opm = _row(q, *L["opm"]) or []
    pat = _row(q, "Net Profit") or []
    pbt = _row(q, "Profit before tax") or []
    oi = _row(q, "Other Income") or []
    out = []
    for i, k in enumerate(q["keys"]):
        g = lambda arr: arr[i] if i < len(arr) else None  # noqa: E731
        y = lambda arr: arr[i - 4] if i - 4 >= 0 and i - 4 < len(arr) else None  # noqa: E731
        out.append(
            {
                "period": k,
                "label": fy_quarter(k) if len(k) == 10 else k,
                "revenue": g(rev),
                "op_profit": g(op),
                "opm": _margin(g(op), g(rev), g(opm)),
                "pbt": g(pbt),
                "pat": g(pat),
                "other_income": g(oi),
                "revenue_yoy": pct(g(rev), y(rev)),
                "op_profit_yoy": pct(g(op), y(op)),
                "pat_yoy": pct(g(pat), y(pat)),
            }
        )
    return out


def annual_rows(page: dict) -> list[dict]:
    pl = page.get("profit_loss")
    if not pl:
        return []
    L = labels(page)
    rev = _row(pl, *L["revenue"]) or []
    op = _row(pl, *L["op"]) or []
    opm = _row(pl, *L["opm"]) or []
    pat = _row(pl, "Net Profit") or []
    out = []
    for i, k in enumerate(pl["keys"]):
        g = lambda arr: arr[i] if i < len(arr) else None  # noqa: E731
        p = lambda arr: arr[i - 1] if i >= 1 and i - 1 < len(arr) else None  # noqa: E731
        out.append({"period": k, "label": fy_label(k), "revenue": g(rev), "op_profit": g(op), "opm": _margin(g(op), g(rev), g(opm)), "pat": g(pat),
                    "revenue_yoy": pct(g(rev), p(rev)) if k != "TTM" else None, "pat_yoy": pct(g(pat), p(pat)) if k != "TTM" else None})
    return out


# ------------------------------------------------------------------ sections

def financial_snapshot(page: dict) -> dict:
    r = page.get("ratios") or {}
    pl, bs = page.get("profit_loss"), page.get("balance_sheet")
    L = labels(page)
    bank = page.get("is_bank")
    ttm_rev = _ttm(pl, *L["revenue"])
    ttm_op = _ttm(pl, *L["op"])
    ttm_pat = _ttm(pl, "Net Profit")
    qr = quarter_rows(page)
    lq = next((x for x in reversed(qr) if x["revenue"] is not None), None)
    metrics = [
        {"label": "Market cap", "value": r.get("market_cap_cr"), "unit": "cr"},
        {"label": "P/E", "value": r.get("pe"), "unit": "x"},
        {"label": "ROE", "value": r.get("roe"), "unit": "%"},
    ]
    if not bank:
        metrics.append({"label": "ROCE", "value": r.get("roce"), "unit": "%"})
    metrics += [
        {"label": "Revenue (TTM)", "value": ttm_rev, "unit": "cr"},
        {"label": ("Financing profit" if bank else "Operating profit") + " (TTM)", "value": ttm_op, "unit": "cr",
         "hint": f"margin {ttm_op / ttm_rev * 100:.1f}%" if ttm_op is not None and ttm_rev else None},
        {"label": "PAT (TTM)", "value": ttm_pat, "unit": "cr",
         "hint": f"margin {ttm_pat / ttm_rev * 100:.1f}%" if ttm_pat is not None and ttm_rev else None},
    ]
    if not bank and bs:
        eq = _last(_series(bs, "Equity Capital"))[1]
        res = _last(_series(bs, "Reserves"))[1]
        debt = _last(_series(bs, "Borrowings", "Borrowing"))[1]
        if eq is not None and res is not None and debt is not None and (eq + res) > 0:
            metrics.append({"label": "Debt / Equity", "value": round(debt / (eq + res), 2), "unit": "x"})
    if bank:
        q = page.get("quarters")
        gnpa = _last(_series(q, "Gross NPA %"))[1]
        nnpa = _last(_series(q, "Net NPA %"))[1]
        if gnpa is not None:
            metrics.append({"label": "Gross NPA", "value": gnpa, "unit": "%"})
        if nnpa is not None:
            metrics.append({"label": "Net NPA", "value": nnpa, "unit": "%"})
    metrics.append({"label": "Dividend yield", "value": r.get("dividend_yield"), "unit": "%"})
    signals = []
    if lq:
        parts = [f"{lq['label']}: revenue {_fmt_pct(lq['revenue_yoy'])} YoY", f"PAT {_fmt_pct(lq['pat_yoy'])} YoY"]
        tone = "good" if (lq["pat_yoy"] or 0) > 10 and (lq["revenue_yoy"] or 0) > 0 else "bad" if (lq["pat_yoy"] or 0) < -5 else "neutral"
        signals.append(_sig(tone, ", ".join(parts) + (f"; {'financing' if bank else 'operating'} margin {lq['opm']:.1f}%" if lq.get("opm") is not None else "")))
        idx = qr.index(lq)
        base = qr[idx - 4] if idx >= 4 else None
        others = sorted(x["other_income"] for x in qr if x.get("other_income") is not None and x is not base)
        if base and base.get("other_income") and others and not bank:
            typical = others[len(others) // 2]
            if typical > 0 and base["other_income"] > 2.2 * typical:
                signals.append(_sig("neutral", f"The year-ago quarter ({base['label']}) carried unusually high other income "
                                    f"(₹{base['other_income']:,.0f} cr vs ~₹{typical:,.0f} cr typical), so the PAT YoY comparison is distorted"))
        if lq.get("other_income") and lq.get("pbt") and lq["pbt"] > 0 and lq["other_income"] / lq["pbt"] > 0.25 and not bank:
            signals.append(_sig("bad", f"Other income is {lq['other_income'] / lq['pbt'] * 100:.0f}% of {lq['label']} PBT: profit quality depends on non-operating items"))
    return {
        "id": "financials",
        "title": "Financial snapshot",
        "metrics": [m for m in metrics if m.get("value") is not None],
        "latest_quarter": lq,
        "signals": signals,
        "source": f"screener.in standardised filings ({page.get('basis')})",
    }


def trajectory(page: dict) -> dict:
    ann = [a for a in annual_rows(page) if a["period"] != "TTM"]
    qr = quarter_rows(page)
    signals = []
    growth = {}
    for key, name in (("revenue", "Revenue"), ("op_profit", "Operating profit" if not page.get("is_bank") else "Financing profit"), ("pat", "PAT")):
        vals = [(a["label"], a[key]) for a in ann if a[key] is not None]
        if len(vals) >= 4:
            growth[key] = {
                "name": name,
                "cagr_3y": cagr(vals[-4][1], vals[-1][1], 3),
                "cagr_5y": cagr(vals[-6][1], vals[-1][1], 5) if len(vals) >= 6 else None,
                "last_year": pct(vals[-1][1], vals[-2][1]),
            }
    rq = [x for x in qr if x["revenue_yoy"] is not None]
    if growth.get("revenue") and len(rq) >= 4:
        recent = [x["revenue_yoy"] for x in rq[-4:]]
        avg_recent = sum(recent) / len(recent)
        c3 = growth["revenue"]["cagr_3y"]
        if c3 is not None:
            if avg_recent > c3 + 3:
                signals.append(_sig("good", f"Revenue growth is accelerating: last 4 quarters average {avg_recent:.0f}% YoY vs {c3:.0f}% 3-year CAGR"))
            elif avg_recent < c3 - 3:
                signals.append(_sig("bad", f"Revenue growth is slowing: last 4 quarters average {avg_recent:.0f}% YoY vs {c3:.0f}% 3-year CAGR"))
            else:
                signals.append(_sig("neutral", f"Revenue growth steady: ~{avg_recent:.0f}% YoY recently, {c3:.0f}% 3-year CAGR"))
    opm = [(a["label"], a["opm"]) for a in ann if a["opm"] is not None]
    if len(opm) >= 4:
        d = (opm[-1][1] - opm[-4][1]) * 100
        if abs(d) >= 100:
            signals.append(_sig("good" if d > 0 else "bad",
                                f"{'Margin expanded' if d > 0 else 'Margin compressed'} {abs(d):.0f} bps over 3 years ({opm[-4][1]:.1f}% in {opm[-4][0]} → {opm[-1][1]:.1f}% in {opm[-1][0]})"))
        else:
            signals.append(_sig("neutral", f"Margins broadly stable over 3 years ({opm[-4][1]:.1f}% → {opm[-1][1]:.1f}%)"))
    if growth.get("pat") and growth.get("revenue") and growth["pat"]["cagr_3y"] is not None and growth["revenue"]["cagr_3y"] is not None:
        gp, gr = growth["pat"]["cagr_3y"], growth["revenue"]["cagr_3y"]
        if gp > gr + 4:
            signals.append(_sig("good", f"Operating leverage: PAT compounding {gp:.0f}% vs revenue {gr:.0f}% (3y)"))
        elif gp < gr - 4:
            signals.append(_sig("bad", f"Profits lagging sales: PAT {gp:.0f}% vs revenue {gr:.0f}% 3-year CAGR"))
    return {
        "id": "trajectory",
        "title": "Trajectory",
        "growth": growth,
        "annual": [{"label": a["label"], "revenue": a["revenue"], "opm": a["opm"], "pat": a["pat"]} for a in ann[-8:]],
        "quarterly": [{"label": x["label"], "revenue": x["revenue"], "revenue_yoy": x["revenue_yoy"], "pat_yoy": x["pat_yoy"], "opm": x["opm"]} for x in qr[-8:]],
        "signals": signals,
        "source": "screener.in standardised filings",
    }


def balance_sheet(page: dict) -> dict:
    bs = page.get("balance_sheet")
    pl = page.get("profit_loss")
    ratios = page.get("ratios_table")
    bank = page.get("is_bank")
    signals = []
    years = []
    if bs:
        eq = dict(_series(bs, "Equity Capital"))
        res = dict(_series(bs, "Reserves"))
        debt = dict(_series(bs, "Borrowings", "Borrowing"))
        dep = dict(_series(bs, "Deposits"))
        cwip = dict(_series(bs, "CWIP"))
        fa = dict(_series(bs, "Fixed Assets"))
        tot = dict(_series(bs, "Total Liabilities"))
        for k in bs["keys"]:
            e = (eq.get(k) or 0) + (res.get(k) or 0) if eq.get(k) is not None else None
            years.append({
                "label": fy_label(k) if k.endswith("-03-31") else k,
                "period": k,
                "equity": e,
                "debt": debt.get(k),
                "deposits": dep.get(k),
                "de": round(debt[k] / e, 2) if debt.get(k) is not None and e else None,
                "cwip_pct_fa": round(cwip[k] / fa[k] * 100, 1) if cwip.get(k) is not None and fa.get(k) else None,
                "total": tot.get(k),
            })
    interest_cover = None
    if pl and not bank:
        op_last = _last(_series(pl, "Operating Profit", annual=True))[1]
        int_last = _last(_series(pl, "Interest", annual=True))[1]
        if op_last is not None and int_last:
            interest_cover = round(op_last / int_last, 1)
    wc = []
    if ratios and not bank:
        dd = dict(_series(ratios, "Debtor Days"))
        inv = dict(_series(ratios, "Inventory Days"))
        dp = dict(_series(ratios, "Days Payable"))
        ccc = dict(_series(ratios, "Cash Conversion Cycle"))
        for k in ratios["keys"]:
            wc.append({"label": fy_label(k), "debtor_days": dd.get(k), "inventory_days": inv.get(k), "payable_days": dp.get(k), "ccc": ccc.get(k)})
        valid = [w for w in wc if w["ccc"] is not None]
        if len(valid) >= 4:
            d = valid[-1]["ccc"] - valid[-4]["ccc"]
            if d >= 15:
                signals.append(_sig("bad", f"Working-capital cycle lengthened by {d:.0f} days in 3 years ({valid[-4]['ccc']:.0f} → {valid[-1]['ccc']:.0f} days): cash is getting tied up"))
            elif d <= -15:
                signals.append(_sig("good", f"Working-capital cycle shortened by {abs(d):.0f} days in 3 years ({valid[-4]['ccc']:.0f} → {valid[-1]['ccc']:.0f} days)"))
            dvalid = [w for w in wc if w["debtor_days"] is not None]
            if len(dvalid) >= 4 and dvalid[-1]["debtor_days"] - dvalid[-4]["debtor_days"] >= 20:
                signals.append(_sig("bad", f"Receivables stretching: debtor days {dvalid[-4]['debtor_days']:.0f} → {dvalid[-1]['debtor_days']:.0f}"))
    yv = [y for y in years if y["de"] is not None]
    if yv and not bank:
        de = yv[-1]["de"]
        if de < 0.1:
            signals.append(_sig("good", f"Effectively debt-free (debt/equity {de:.2f})"))
        elif de > 1.0:
            signals.append(_sig("bad", f"Leveraged: debt/equity {de:.2f}"))
        else:
            signals.append(_sig("neutral", f"Moderate leverage: debt/equity {de:.2f}"))
        if len(yv) >= 4 and yv[-1]["debt"] and yv[-4]["debt"] and yv[-1]["debt"] > yv[-4]["debt"] * 1.5 and yv[-1]["de"] > 0.3:
            signals.append(_sig("bad", f"Borrowings up {pct(yv[-1]['debt'], yv[-4]['debt']):.0f}% in 3 years"))
    if interest_cover is not None:
        signals.append(_sig("good" if interest_cover > 6 else "bad" if interest_cover < 2.5 else "neutral",
                            f"Interest cover {interest_cover:.1f}x (operating profit / interest, last year)"))
    if bank and years:
        dv = [y for y in years if y["deposits"]]
        if len(dv) >= 4:
            signals.append(_sig("neutral", f"Deposits compounding {cagr(dv[-4]['deposits'], dv[-1]['deposits'], 3):.0f}% a year (3y)"))
    cw = [y for y in years if y["cwip_pct_fa"] is not None]
    if cw and cw[-1]["cwip_pct_fa"] > 25:
        signals.append(_sig("neutral", f"Capital work-in-progress is {cw[-1]['cwip_pct_fa']:.0f}% of fixed assets: large capex still to be commissioned"))
    return {
        "id": "balance_sheet",
        "title": "Balance sheet",
        "years": years[-6:],
        "working_capital": wc[-6:],
        "interest_cover": interest_cover,
        "signals": signals,
        "note": "Leverage and working-capital ratios are not meaningful for banks; deposits and asset quality matter instead." if bank else None,
        "source": "screener.in standardised filings",
    }


def cash_quality(page: dict) -> dict:
    cf = page.get("cash_flow")
    pl = page.get("profit_loss")
    bank = page.get("is_bank")
    if bank:
        return {
            "id": "cash_quality", "title": "Cash quality", "years": [], "signals": [],
            "note": "For a bank, operating cash flow mostly reflects deposit and loan movements, so profit-to-cash conversion is not a meaningful test. Asset quality (NPAs) is the better check.",
            "source": "screener.in standardised filings",
        }
    cfo = dict(_series(cf, "Cash from Operating Activity"))
    cfi = dict(_series(cf, "Cash from Investing Activity"))
    fcf = dict(_series(cf, "Free Cash Flow"))
    cfo_ebitda = dict(_series(cf, "CFO/EBITDA"))
    pat = dict(_series(pl, "Net Profit", annual=True))
    years = []
    for k in (cf or {}).get("keys", []):
        p = pat.get(k)
        c = cfo.get(k)
        years.append({
            "label": fy_label(k), "cfo": c, "pat": p,
            "fcf": fcf.get(k) if fcf.get(k) is not None else ((c + cfi[k]) if c is not None and cfi.get(k) is not None else None),
            "cfo_to_pat": round(c / p, 2) if c is not None and p and p > 0 else None,
            "cfo_to_ebitda": cfo_ebitda.get(k),
        })
    signals = []
    last5 = [y for y in years[-5:] if y["cfo"] is not None and y["pat"] is not None]
    cum = None
    if len(last5) >= 3:
        sp = sum(y["pat"] for y in last5)
        sc = sum(y["cfo"] for y in last5)
        if sp > 0:
            cum = round(sc / sp, 2)
            if cum >= 0.9:
                signals.append(_sig("good", f"Profits turn into cash: {len(last5)}-year operating cash flow is {cum:.0%} of reported PAT"))
            elif cum >= 0.7:
                signals.append(_sig("neutral", f"Partial cash conversion: {len(last5)}-year operating cash flow is {cum:.0%} of PAT"))
            else:
                signals.append(_sig("bad", f"Weak cash conversion: {len(last5)}-year operating cash flow is only {cum:.0%} of PAT"))
    fv = [y for y in years[-5:] if y["fcf"] is not None]
    if len(fv) >= 3:
        neg = sum(1 for y in fv if y["fcf"] < 0)
        total = sum(y["fcf"] for y in fv)
        if neg >= 3:
            signals.append(_sig("bad", f"Free cash flow negative in {neg} of the last {len(fv)} years: growth is being funded externally or from reserves"))
        else:
            signals.append(_sig("good" if total > 0 else "neutral", f"Cumulative free cash flow over {len(fv)} years: ₹{total:,.0f} cr"))
    return {"id": "cash_quality", "title": "Cash quality", "years": years[-6:], "cfo_to_pat_5y": cum, "signals": signals,
            "source": "screener.in standardised filings"}


def shareholding(page: dict) -> dict:
    sh = page.get("shareholding")
    if not sh:
        return {"id": "shareholding", "title": "Who owns it", "rows": [], "signals": []}
    rows = []
    signals = []
    for label in ("Promoters", "FIIs", "DIIs", "Government", "Public"):
        s = [(k, v) for k, v in _series(sh, label) if v is not None]
        if not s:
            continue
        rows.append({"holder": label, "latest": s[-1][1], "change_1y": round(s[-1][1] - s[-5][1], 2) if len(s) >= 5 else None})
    prom = next((r for r in rows if r["holder"] == "Promoters"), None)
    if prom and prom["change_1y"] is not None and prom["change_1y"] <= -1:
        signals.append(_sig("bad", f"Promoters reduced their stake by {abs(prom['change_1y']):.1f} pp over the last year (now {prom['latest']:.1f}%)"))
    elif prom and prom["change_1y"] is not None and prom["change_1y"] >= 1:
        signals.append(_sig("good", f"Promoters raised their stake by {prom['change_1y']:.1f} pp over the last year"))
    fii = next((r for r in rows if r["holder"] == "FIIs"), None)
    if fii and fii["change_1y"] is not None and abs(fii["change_1y"]) >= 2:
        signals.append(_sig("neutral", f"Foreign institutions {'added' if fii['change_1y'] > 0 else 'cut'} {abs(fii['change_1y']):.1f} pp in a year"))
    return {"id": "shareholding", "title": "Who owns it", "rows": rows, "as_of": sh["periods"][-1] if sh.get("periods") else None,
            "signals": signals, "source": "screener.in (exchange shareholding filings)"}


def numbers_digest(page: dict) -> str:
    """Compact plain-text table of reported numbers for the analyst prompt (~1.5k tokens)."""
    lines = ["Quarter | Revenue | YoY% | OpProfit | OPM% | PAT | PAT YoY%"]
    for x in quarter_rows(page)[-10:]:
        lines.append(" | ".join(str(v) for v in (x["label"], _r(x["revenue"]), _r(x["revenue_yoy"]), _r(x["op_profit"]), _r(x["opm"]), _r(x["pat"]), _r(x["pat_yoy"]))))
    lines.append("")
    lines.append("Year | Revenue | YoY% | OpProfit | OPM% | PAT | PAT YoY%")
    for a in annual_rows(page)[-7:]:
        lines.append(" | ".join(str(v) for v in (a["label"], _r(a["revenue"]), _r(a["revenue_yoy"]), _r(a["op_profit"]), _r(a["opm"]), _r(a["pat"]), _r(a["pat_yoy"]))))
    cq = cash_quality(page)
    if cq.get("years"):
        lines.append("")
        lines.append("Year | CFO | PAT | FCF")
        for y in cq["years"]:
            lines.append(f"{y['label']} | {_r(y['cfo'])} | {_r(y['pat'])} | {_r(y['fcf'])}")
    bsec = balance_sheet(page)
    if bsec.get("years"):
        lines.append("")
        lines.append("Year | Equity | Debt | D/E")
        for y in bsec["years"]:
            lines.append(f"{y['label']} | {_r(y['equity'])} | {_r(y['debt'])} | {_r(y['de'])}")
    return "\n".join(lines)


def _r(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.1f}" if abs(v) < 100 else f"{v:,.0f}"
    return str(v)


def _fmt_pct(v):
    return "n/a" if v is None else f"{v:+.0f}%"
