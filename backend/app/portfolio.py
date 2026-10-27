"""The mock portfolio and the ad-hoc research set.

Weights are a mock allocation (sum to 100). They matter: FINN ranks a 5% fall in a 9% position
above the same fall in a 4% position, because the investor cares about what it does to their book.
BSE codes are cached here for speed; for any other ticker FINN resolves the code from screener.in.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Holding:
    symbol: str  # NSE symbol
    name: str
    bse_code: str
    sector: str
    size: str  # Mega / Large / Mid / Small (by market cap bucket)
    weight: float  # % of mock portfolio

    def to_dict(self) -> dict:
        return asdict(self)


PORTFOLIO: list[Holding] = [
    Holding("RELIANCE", "Reliance Industries", "500325", "Energy & Conglomerate", "Mega", 9),
    Holding("ICICIBANK", "ICICI Bank", "532174", "Private Bank", "Mega", 9),
    Holding("INFY", "Infosys", "500209", "IT Services", "Mega", 7),
    Holding("BHARTIARTL", "Bharti Airtel", "532454", "Telecom", "Mega", 7),
    Holding("BAJFINANCE", "Bajaj Finance", "500034", "NBFC", "Large", 6),
    Holding("LT", "Larsen & Toubro", "500510", "Engineering & Construction", "Mega", 6),
    Holding("ETERNAL", "Eternal (Zomato)", "543320", "Food Delivery & Quick Commerce", "Large", 6),
    Holding("MARUTI", "Maruti Suzuki", "532500", "Automobiles", "Large", 5),
    Holding("HINDUNILVR", "Hindustan Unilever", "500696", "FMCG", "Mega", 5),
    Holding("SUNPHARMA", "Sun Pharma", "524715", "Pharmaceuticals", "Large", 5),
    Holding("NTPC", "NTPC", "532555", "Power (PSU)", "Large", 5),
    Holding("TITAN", "Titan Company", "500114", "Jewellery & Watches", "Large", 5),
    Holding("DIXON", "Dixon Technologies", "540699", "Electronics Manufacturing", "Mid", 5),
    Holding("TATASTEEL", "Tata Steel", "500470", "Metals", "Large", 4),
    Holding("ULTRACEMCO", "UltraTech Cement", "532538", "Cement", "Large", 4),
    Holding("POLYCAB", "Polycab India", "542652", "Cables & Wires", "Mid", 4),
    Holding("ZENTEC", "Zen Technologies", "533339", "Defence Simulation", "Small", 4),
    Holding("HAPPSTMNDS", "Happiest Minds", "543227", "IT Services", "Small", 4),
]

# Companies outside the portfolio used to demo on-demand research. The engine is not limited to these.
RESEARCH_SET: list[dict] = [
    {"symbol": "PERSISTENT", "name": "Persistent Systems", "why": "Guidance-heavy IT story: good test of promised vs delivered"},
    {"symbol": "ASIANPAINT", "name": "Asian Paints", "why": "Market leader facing new competition: narrative vs numbers"},
    {"symbol": "TRENT", "name": "Trent", "why": "High-growth retailer: is growth turning into cash?"},
    {"symbol": "KAYNES", "name": "Kaynes Technology", "why": "Fast-growing EMS mid-cap: working capital and cash quality"},
]

BY_SYMBOL: dict[str, Holding] = {h.symbol: h for h in PORTFOLIO}
KNOWN_BSE_CODES: dict[str, str] = {h.symbol: h.bse_code for h in PORTFOLIO} | {
    "PERSISTENT": "533179",
    "ASIANPAINT": "500820",
    "TRENT": "500251",
    "KAYNES": "543664",
}
