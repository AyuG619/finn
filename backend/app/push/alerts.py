"""End-of-day price & volume monitoring with user-defined thresholds."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass
class Thresholds:
    price_pct: float = 4.0  # alert when |daily move| >= this
    vol_x: float = 2.0  # alert when volume >= this multiple of the trailing average
    lookback: int = 20  # sessions in the trailing average
    near_52w_pct: float = 1.0  # within this % of a 52-week high/low

    def clamp(self) -> "Thresholds":
        self.price_pct = min(max(self.price_pct, 0.5), 30.0)
        self.vol_x = min(max(self.vol_x, 1.1), 20.0)
        self.lookback = int(min(max(self.lookback, 5), 120))
        self.near_52w_pct = min(max(self.near_52w_pct, 0.1), 10.0)
        return self


def session_stats(bars: list[dict], lookback: int = 20, gaps: list[str] | None = None) -> list[dict]:
    """Per-session change and volume-vs-average. Provisional (intraday) bars are kept but flagged.
    ``gaps`` are sessions the data source has no print for: a change spanning one is not "in a day"."""
    gapset = set(gaps or [])
    out: list[dict] = []
    complete_vols: list[int] = []
    closes_window: list[float] = []
    for i, b in enumerate(bars):
        prev = bars[i - 1] if i > 0 else None
        trailing = complete_vols[-lookback:]
        avg_vol = mean(trailing) if len(trailing) >= max(5, lookback // 2) else None
        change_pct = ((b["close"] / prev["close"] - 1) * 100) if prev and prev["close"] else None
        closes_window.append(b["close"])
        last_year = closes_window[-250:]
        hi = max(last_year)
        lo = min(last_year)
        out.append(
            {
                "date": b["date"],
                "close": b["close"],
                "prev_close": prev["close"] if prev else None,
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "volume": b["volume"],
                "avg_volume": round(avg_vol) if avg_vol else None,
                "vol_ratio": round(b["volume"] / avg_vol, 2) if avg_vol and b["volume"] else None,
                "provisional": bool(b.get("provisional")),
                "since": prev["date"] if prev and any(prev["date"] < g < b["date"] for g in gapset) else None,
                "high_52w": hi,
                "low_52w": lo,
            }
        )
        if not b.get("provisional") and b["volume"]:
            complete_vols.append(b["volume"])
    return out


def _short_date(d: str) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{int(d[8:10])} {months[int(d[5:7]) - 1]}"


def evaluate(stat: dict, t: Thresholds) -> list[dict]:
    """Which thresholds a single session breaches."""
    hits: list[dict] = []
    chg = stat.get("change_pct")
    if chg is not None and abs(chg) >= t.price_pct:
        span = f"since {_short_date(stat['since'])}" if stat.get("since") else "in a day"
        hits.append({"kind": "price", "label": f"{'+' if chg > 0 else '−'}{abs(chg):.1f}% {span}", "value": chg})
    vr = stat.get("vol_ratio")
    if vr is not None and not stat.get("provisional") and vr >= t.vol_x:
        hits.append({"kind": "volume", "label": f"{vr:.1f}× normal volume", "value": vr})
    if stat.get("high_52w") and stat["close"] >= stat["high_52w"] * (1 - t.near_52w_pct / 100) and chg and chg > 0:
        hits.append({"kind": "high_52w", "label": "at a 52-week high", "value": stat["high_52w"]})
    elif stat.get("low_52w") and stat["close"] <= stat["low_52w"] * (1 + t.near_52w_pct / 100) and chg and chg < 0:
        hits.append({"kind": "low_52w", "label": "at a 52-week low", "value": stat["low_52w"]})
    return hits


def alert_score(stat: dict, hits: list[dict], t: Thresholds, weight: float) -> int:
    kinds = {h["kind"] for h in hits}
    s = 45.0
    chg = abs(stat.get("change_pct") or 0)
    if "price" in kinds:
        s += 12 + min(25, (chg / t.price_pct - 1) * 18)
    if "volume" in kinds:
        s += 8 + min(12, ((stat.get("vol_ratio") or 0) / t.vol_x - 1) * 8)
    if "price" in kinds and "volume" in kinds:
        s += 6  # a move on heavy volume is more meaningful than either alone
    if kinds & {"high_52w", "low_52w"}:
        s += 4
    s += weight * 0.8
    return int(max(0, min(100, s)))
