"""Local mock of the FINN API for UI work without network access.

The digest is built by the REAL pipeline (classification, scoring, bundling, alerts) on real NSE
filings captured on 26-27 Oct 2026, with synthetic prices. Enrichment and research use canned
model outputs shaped exactly like production responses.

    python -m tests.mock_server   # serves http://127.0.0.1:8765 (API + ../frontend/dist)
"""
from __future__ import annotations

import math
import os
import random
from datetime import datetime, timedelta

os.environ.setdefault("FINN_DATA_DIR", "/tmp/claude-0/finn-mock")
os.environ.setdefault("FINN_REFRESH_MINUTES", "0")

from app import main  # noqa: E402
from app.push import digest as digest_mod  # noqa: E402
from app.push import enrich as enrich_mod  # noqa: E402
from app.research import engine  # noqa: E402
from app.research import fundamentals as F  # noqa: E402
from app.sources import nse, prices, screener  # noqa: E402
from app.sources.prices import IST  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "screener_reliance.html")
PAGE = screener.parse_company_page(open(FIXTURE, encoding="utf-8").read(), "https://www.screener.in/company/RELIANCE/consolidated/")

NOW = datetime.now(IST)


def t(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def nse_row(sym, company, desc, text, hours_ago, seq):
    return nse.normalise(
        {
            "an_dt": (NOW - timedelta(hours=hours_ago)).strftime("%d-%b-%Y %H:%M:%S"),
            "desc": desc,
            "attchmntText": text,
            "seq_id": str(seq),
            "sm_name": company,
            "attchmntFile": f"https://nsearchives.nseindia.com/corporate/{sym}_{seq}.pdf",
        },
        sym,
    )


FILINGS = {
    "ICICIBANK": [
        nse_row("ICICIBANK", "ICICI Bank Limited", "Outcome of Board Meeting", "ICICI Bank Limited has submitted to the Exchange, the financial results for the period ended September 30, 2026.", 6, 1001),
        nse_row("ICICIBANK", "ICICI Bank Limited", "Press Release", 'ICICI Bank Limited has informed the Exchange regarding a press release dated October 26, 2026, titled "Performance review: quarter ended September 30, 2026"', 5.8, 1002),
        nse_row("ICICIBANK", "ICICI Bank Limited", "Copy of Newspaper Publication", "ICICI Bank Limited has informed the Exchange about Copy of Newspaper Publication", 2, 1003),
    ],
    "BAJFINANCE": [
        nse_row("BAJFINANCE", "Bajaj Finance Limited", "Resignation", "Bajaj Finance Limited has informed the Exchange regarding Resignation of Mr Sandeep Jain as Chief Financial Officer of the company w.e.f. October 31, 2026.", 9, 1011),
        nse_row("BAJFINANCE", "Bajaj Finance Limited", "Appointment", "Bajaj Finance Limited has informed the Exchange regarding Appointment of Mr Ravi Kumar as Chief Financial Officer of the company w.e.f. November 01, 2026.", 8.9, 1012),
    ],
    "LT": [
        nse_row("LT", "Larsen & Toubro Limited", "Bagging/Receiving of orders/contracts", "Larsen & Toubro Limited has informed the Exchange about Bagging/Receiving of orders/contracts", 14, 1021),
        nse_row("LT", "Larsen & Toubro Limited", "Board Meeting Intimation", "Larsen & Toubro Limited has informed the Exchange about Board Meeting to be held on " + (NOW + timedelta(days=2)).strftime("%d-%b-%Y") + " to consider financial results", 200, 1022),
    ],
    "ZENTEC": [
        nse_row("ZENTEC", "Zen Technologies Limited", "Updates", "Zen Technologies Limited has informed the Exchange regarding Intimation regarding Business Today s Most Powerful Women in Business (MPW) Award received by Ms. Shilpa Choudari", 30, 1031),
        nse_row("ZENTEC", "Zen Technologies Limited", "Trading Window", "Zen Technologies Limited has informed the Exchange regarding the Trading Window closure pursuant to SEBI (Prohibition of Insider Trading) Regulations, 2015", 40, 1032),
    ],
    "DIXON": [
        nse_row("DIXON", "Dixon Technologies (India) Limited", "Board Meeting Intimation", "Dixon Technologies (India) Limited has informed the Exchange about Board Meeting to be held on " + (NOW + timedelta(days=3)).strftime("%d-%b-%Y") + " to consider financial results", 300, 1041),
        nse_row("DIXON", "Dixon Technologies (India) Limited", "Analysts/Institutional Investor Meet/Con. Call Updates", "Intimation of Investors Conference Call on Thursday, October 29, 2026.", 12, 1042),
    ],
    "TATASTEEL": [
        nse_row("TATASTEEL", "Tata Steel Limited", "Credit Rating", "Tata Steel Limited has informed the Exchange about Credit Rating", 20, 1051),
        nse_row("TATASTEEL", "Tata Steel Limited", "Certificate under SEBI (Depositories and Participants) Regulations, 2018", "Tata Steel Limited has informed the Exchange about Certificate under Regulation 74(5)", 22, 1052),
    ],
    "HINDUNILVR": [
        nse_row("HINDUNILVR", "Hindustan Unilever Limited", "Copy of Newspaper Publication", "Hindustan Unilever Limited has informed the Exchange about Copy of Newspaper Publication", 3, 1061),
    ],
}


async def fake_ann(symbol, days=30):
    return [f for f in FILINGS.get(symbol, []) if f]


def fake_bars(symbol):
    rnd = random.Random(symbol)
    px = rnd.uniform(200, 4000)
    bars = []
    d = NOW.date() - timedelta(days=400)
    while d <= NOW.date():
        if d.weekday() < 5:
            px *= math.exp(rnd.gauss(0.0003, 0.014))
            vol = int(rnd.uniform(0.6, 1.4) * 1_000_000)
            bars.append({"date": d.isoformat(), "open": px, "high": px * 1.01, "low": px * 0.99, "close": round(px, 2), "volume": vol, "provisional": False})
        d += timedelta(days=1)
    if symbol == "DIXON":  # a heavy-volume fall on the last session
        bars[-1]["close"] = round(bars[-2]["close"] * 0.937, 2)
        bars[-1]["volume"] = 3_400_000
    if symbol == "ICICIBANK":
        bars[-1]["close"] = round(bars[-2]["close"] * 1.046, 2)
        bars[-1]["volume"] = 2_600_000
    return {"source": "synthetic", "currency": "INR", "name": symbol, "fifty_two_week_high": None, "fifty_two_week_low": None, "bars": bars}


async def fake_history(symbol, range_="1y"):
    return fake_bars(symbol)


async def fake_page(symbol):
    p = dict(PAGE)
    p["symbol"] = symbol
    p["nse_symbol"] = symbol
    return p


async def fake_search(q, limit=8):
    names = {"TRENT": "Trent Ltd", "TITAN": "Titan Company Ltd", "TATASTEEL": "Tata Steel Ltd", "TCS": "Tata Consultancy Services Ltd", "PERSISTENT": "Persistent Systems Ltd"}
    return [{"symbol": k, "name": v, "screener_id": 1} for k, v in names.items() if q.lower() in (k + v).lower()][:limit]


RESULTS_CARD = {
    "kind": "results", "quarter": "Q2 FY27", "period_end": "2026-09-30", "source": "results filing", "basis": "consolidated", "is_bank": True,
    "metrics": [
        {"key": "revenue", "label": "Total income", "value": 52480.0, "prev": 50912.0, "year_ago": 47804.0, "qoq": 3.1, "yoy": 9.8},
        {"key": "ebitda", "label": "Pre-provision profit", "value": 18240.0, "prev": 17702.0, "year_ago": 16410.0, "qoq": 3.0, "yoy": 11.2},
        {"key": "pbt", "label": "PBT", "value": 16120.0, "prev": 15800.0, "year_ago": 14650.0, "qoq": 2.0, "yoy": 10.0},
        {"key": "pat", "label": "PAT", "value": 12360.0, "prev": 12110.0, "year_ago": 11250.0, "qoq": 2.1, "yoy": 9.9},
    ],
    "margins": {"ebitda_margin": 34.8, "ebitda_margin_qoq_bps": -2, "ebitda_margin_yoy_bps": 43, "pat_margin": 23.6},
    "verified": {"against": "screener.in", "revenue_diff_pct": 0.4, "ok": True}, "pages": [3, 4],
    "llm": {"model": "gemini:gemini-flash-lite-latest", "cached": False, "input_tokens": 3100, "output_tokens": 600},
}
MGMT_CARD = {
    "kind": "management", "summary": "CFO Sandeep Jain resigns effective 31 Oct; Ravi Kumar appointed CFO from 1 Nov.",
    "changes": [
        {"name": "Ravi Kumar", "role": "Chief Financial Officer", "role_bucket": "cfo", "change": "appointed", "effective_date": "1 November 2026", "reason": None,
         "profile_from_filing": "Chartered accountant with 22 years in consumer lending; previously deputy CFO.",
         "linkedin_search": "https://www.linkedin.com/search/results/all/?keywords=Ravi+Kumar+Bajaj+Finance",
         "background": {"found": True, "headline": "Deputy CFO, 22 years in consumer finance", "background": "Chartered accountant who joined from a large private bank's retail finance team in 2016; led treasury and investor relations before becoming deputy CFO.",
                        "previous_roles": [{"org": "Bajaj Finance", "title": "Deputy CFO", "years": "2021-2026"}, {"org": "A private bank", "title": "Head, Retail Finance", "years": "2009-2016"}],
                        "linkedin_url": None, "confidence": "medium", "sources": [{"title": "linkedin.com", "url": "https://www.linkedin.com"}]}},
        {"name": "Sandeep Jain", "role": "Chief Financial Officer", "role_bucket": "cfo", "change": "resigned", "effective_date": "31 October 2026", "reason": "To pursue opportunities outside the group", "profile_from_filing": None,
         "linkedin_search": "https://www.linkedin.com/search/results/all/?keywords=Sandeep+Jain", "background": None},
    ],
    "llm": {"model": "gemini:gemini-flash-lite-latest", "cached": False, "input_tokens": 900, "output_tokens": 300},
}
SUMMARY_CARD = {
    "kind": "summary", "one_liner": "Wins ₹2,140 cr order from NHPC for the civil works of a 1,200 MW pumped-storage project.",
    "key_facts": [{"label": "Value", "value": "₹2,140 cr"}, {"label": "Client", "value": "NHPC"}, {"label": "Timeline", "value": "48 months"}],
    "so_what": "Adds about 0.4% to the order book; in line with the hydro pipeline management flagged.",
    "category": "press_release", "materiality": 3, "llm": {"model": "gemini:gemini-flash-lite-latest", "cached": False, "input_tokens": 1500, "output_tokens": 150},
}


async def fake_enrich(filing, ctx):
    cat = filing.get("category")
    card = RESULTS_CARD if cat == "results" else MGMT_CARD if cat == "management_change" and "BAJ" in ctx.get("symbol", "") else SUMMARY_CARD
    return {**card, "filing_id": filing["id"]}


RESEARCH = {
    "promises": {
        "id": "promises", "title": "Promised vs delivered",
        "items": [
            {"id": "g1", "made_in": "Q4 FY26 call", "promise": "Revenue growth of 18-20% in FY27", "quote": "we expect to grow 18 to 20 percent this year", "due": "Q1 FY27–Q2 FY27", "outcome": "pending", "evidence": "Q1 FY27–Q2 FY27: revenue growth 27% vs 18–20% guided (on track, period not over)", "source": "Q4 FY26 call p.6", "computed": True},
            {"id": "g2", "made_in": "Q3 FY26 call", "promise": "Operating margin of at least 17% in Q4 FY26", "quote": "margins should be north of 17 percent", "due": "Q4 FY26", "outcome": "missed", "evidence": "Q4 FY26: operating margin 15% vs 17% guided", "source": "Q3 FY26 call p.9", "computed": True},
            {"id": "g3", "made_in": "Q2 FY26 call", "promise": "Commission the new gigafactory by March 2026", "due": "Q4 FY26", "outcome": "delivered", "evidence": "Q4 FY26 call confirms first phase commissioned in February", "source": "Q2 FY26 call p.4", "computed": False},
            {"id": "g4", "made_in": "Q2 FY26 call", "promise": "Bring net debt down meaningfully over FY26", "due": "FY26", "outcome": "partly", "evidence": "Borrowings rose 8% in FY26 though debt/equity held at 0.45", "source": "Q2 FY26 call p.11", "computed": False},
        ],
        "credibility": {"score": 5.0, "resolved": 3, "pending": 1, "delivered": 1, "partly": 1, "missed": 1},
        "summary": "Delivers on projects, optimistic on margins: the last margin target was missed by ~200 bps.",
        "model_score": 5, "method": "Quantitative company-level promises are scored by code against reported numbers; qualitative ones by the model, citing later calls.",
    },
    "narrative": {"id": "narrative", "title": "Narrative vs numbers", "items": [
        {"claim": "Margins have bottomed out and will expand from here.", "reality": "Operating margin fell from 18% in Q2 FY26 to 15% in Q1 FY27.", "verdict": "diverges", "source": "Q1 FY27 call p.5"},
        {"claim": "Consumer businesses are driving growth.", "reality": "Revenue grew 27% YoY in Q1 FY27, the fastest in three years.", "verdict": "agrees", "source": "Q1 FY27 call p.3"},
    ]},
    "guidance": {"id": "guidance", "title": "Current guidance", "items": [{"metric": "Revenue growth", "guidance": "18-20% for FY27", "period": "FY27", "source": "Q1 FY27 call p.6"}, {"metric": "Capex", "guidance": "₹1.3-1.5 lakh cr this year", "period": "FY27", "source": "Q1 FY27 call p.12"}], "as_of": "Q1 FY27 call"},
    "bull_bear": {"id": "bull_bear", "title": "Bull vs bear",
                  "bull": [{"point": "Revenue growth re-accelerated to 27% YoY.", "evidence": "Q1 FY27 revenue ₹3.09 lakh cr"}, {"point": "Cash conversion is strong.", "evidence": "5-yr CFO is 1.9x PAT"}, {"point": "New energy capex nearing completion.", "evidence": "CWIP falling since FY24"}],
                  "bear": [{"point": "Margins compressing despite growth.", "evidence": "OPM 15% vs 18% a year ago"}, {"point": "Profit growth lags revenue.", "evidence": "PAT −25% YoY in Q1 FY27"}, {"point": "Leverage creeping up.", "evidence": "Borrowings ₹4.03 lakh cr, up 8%"}],
                  "watch_next": ["Q2 FY27 operating margin vs the 17% promise", "Pace of borrowings growth", "Retail segment like-for-like growth"]},
    "business": {"id": "business", "title": "Business snapshot", "overview": "Conglomerate spanning oil-to-chemicals, retail, digital services and new energy; earns from refining margins, store sales and telecom subscriptions.",
                 "segments": [{"name": "Oil to chemicals", "revenue_share_pct": 55.2, "comment": "Refining and petrochemicals", "page": 112}, {"name": "Retail", "revenue_share_pct": 27.1, "comment": "Grocery, fashion, electronics", "page": 118}, {"name": "Digital services", "revenue_share_pct": 12.4, "comment": "Jio telecom and platforms", "page": 121}],
                 "revenue_mix": "About 35% of revenue is exports.", "risks": [{"point": "Refining margin volatility", "page": 140}], "audit": {"opinion": "unmodified", "key_audit_matters": ["Revenue recognition", "Impairment of oil and gas assets"], "emphasis_of_matter": None, "contingent_liabilities": "₹1.2 lakh cr of claims not acknowledged as debt", "related_parties": None}, "source": "Annual Report 2026"},
    "calls": [{"quarter": "Q1 FY27", "label": "Jul 2026", "url": "https://example.com", "tone": {"label": "confident", "why": "Management stressed growth across consumer businesses."},
               "pushback": [{"topic": "Margin compression in O2C", "management_answer": "Cyclical; expect recovery as spreads normalise.", "page": 14}], "highlights": []}],
}


async def fake_research_pipeline(job, calls):
    job.stage("numbers", "running")
    page = PAGE
    job.result.update({
        "company": {"symbol": job.symbol, "name": page["name"], "bse_code": page["bse_code"], "sector": page["sector"], "website": page["website"], "basis": page["basis"], "is_bank": False,
                    "screener_url": page["url"], "price": page["ratios"]["price"], "market_cap_cr": page["ratios"]["market_cap_cr"]},
        "financials": F.financial_snapshot(page), "trajectory": F.trajectory(page), "balance_sheet": F.balance_sheet(page),
        "cash_quality": F.cash_quality(page), "shareholding": F.shareholding(page),
        "sources": [{"kind": "transcript", "label": "Q1 FY27 earnings call (Jul 2026)", "url": "https://example.com/t.pdf"}, {"kind": "annual_report", "label": "Annual Report 2026", "url": "https://example.com/ar.pdf"}],
    })
    job.stage("numbers", "done", "13 quarters, 12 years")
    import asyncio
    for sid in ("documents", "calls", "annual_report"):
        job.stage(sid, "running")
        await asyncio.sleep(1.2)
        job.stage(sid, "done", "mock")
    job.stage("analysis", "running")
    await asyncio.sleep(1.5)
    job.result.update(RESEARCH)
    calls.append({"task": "analyst_reduce", "model": "gemini:gemini-flash-latest", "cached": False, "input_tokens": 21000, "output_tokens": 3000})
    job.stage("analysis", "done", "4 promises tracked")


async def fake_ask(symbol, question):
    return {"answer": "Management guided to **18-20% revenue growth** for FY27 [Q1 FY27 call p.6] and said margins should recover as spreads normalise [Q1 FY27 call p.14]. The annual report does not give a margin target [AR 2026 p.140].",
            "sources": ["Q1 FY27 call p.6"], "confidence": "medium", "retrieved": ["Q1 FY27 call p.6", "Q1 FY27 call p.14"]}


nse.announcements = fake_ann
digest_mod.nse.announcements = fake_ann
prices.history = fake_history
digest_mod.prices.history = fake_history
screener.company_page = fake_page
screener.search = fake_search
main.screener.search = fake_search
enrich_mod.enrich = fake_enrich
main.enrich_mod.enrich = fake_enrich
engine._pipeline = fake_research_pipeline
engine.ask = fake_ask
main.engine.ask = fake_ask

app = main.app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
