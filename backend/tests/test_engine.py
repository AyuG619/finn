"""Unit tests for the number-crunching that must never be wrong."""
from app.llm.jsonutil import parse_json
from app.llm.router import rank_gemini
from app.push.enrich import build_metrics, expected_quarter_end, headline_from_columns, screener_headline, unit_factor
from app.research import analyst
from app.research.documents import condense_transcript, transcripts_from_page
from app.sources import screener

PAGE = screener.parse_company_page(open("tests/fixtures/screener_reliance.html", encoding="utf-8").read(), "u")


def test_unit_factor():
    assert unit_factor("₹ in lakhs") == 0.01
    assert unit_factor("(Rs. in Crores)") == 1.0
    assert unit_factor("₹ million") == 0.1
    assert unit_factor("Rupees in thousands") == 0.0001
    assert unit_factor(None) is None


def test_ebitda_from_filing_columns():
    col = {"revenue_from_operations": 1000, "other_income": 50, "finance_costs": 30, "depreciation": 70,
           "exceptional_items": -20, "profit_before_tax": 200, "net_profit": 150, "net_profit_owners": 140}
    h = headline_from_columns(col, 0.01, is_bank=False)  # lakhs -> crore
    # EBITDA = (PBT - exceptional) + finance + D&A - other income = (200+20)+30+70-50 = 270 lakh
    assert round(h["ebitda"], 4) == 2.7
    assert round(h["revenue"], 4) == 10.0
    assert round(h["pat"], 4) == 1.4  # owners' share preferred


def test_bank_headline_uses_preprovision_profit():
    col = {"interest_earned": 900, "other_income": 100, "total_income": None, "operating_profit_before_provisions": 400,
           "provisions": 80, "profit_before_tax": 320, "net_profit": 240}
    h = headline_from_columns(col, 1.0, is_bank=True)
    assert h["revenue"] == 1000 and h["ebitda"] == 400 and h["pbt"] == 320


def test_build_metrics_changes_and_margin_bps():
    cur = {"revenue": 110.0, "ebitda": 22.0, "pbt": 15.0, "pat": 11.0}
    prev = {"revenue": 100.0, "ebitda": 21.0, "pbt": 14.0, "pat": 10.0}
    yoy = {"revenue": 88.0, "ebitda": 16.0, "pbt": 10.0, "pat": -2.0}
    metrics, margins = build_metrics(cur, prev, yoy, False)
    m = {x["key"]: x for x in metrics}
    assert m["revenue"]["qoq"] == 10.0 and m["revenue"]["yoy"] == 25.0
    assert m["pat"]["yoy"] is None  # loss base: growth not meaningful
    assert margins["ebitda_margin"] == 20.0
    assert margins["ebitda_margin_qoq_bps"] == -100
    assert margins["ebitda_margin_yoy_bps"] == 182


def test_expected_quarter_end():
    assert expected_quarter_end("2026-10-23T19:42:00+05:30") == "2026-09-30"
    assert expected_quarter_end("2026-01-15T10:00:00+05:30") == "2025-12-31"
    assert expected_quarter_end("2026-05-20T10:00:00+05:30") == "2026-03-31"


def test_screener_headline_alignment():
    sh = screener_headline(PAGE, "2026-06-30")
    assert sh["current"]["revenue"] == 309468.0
    assert sh["prev"]["revenue"] == 294059.0
    assert sh["yoy"]["revenue"] == 243632.0
    assert screener_headline(PAGE, "2026-09-30") is None


def _quarters():
    from app.research.fundamentals import annual_rows, quarter_rows

    return quarter_rows(PAGE), annual_rows(PAGE)


def test_promise_fy_growth_scored_from_annual():
    q, a = _quarters()
    g = {"metric": "revenue_growth", "quantitative": True, "scope": "company", "target_low": 8, "target_high": 10, "unit": "%", "period": "FY26",
         "statement": "Revenue to grow 8-10% in FY26", "quote": "8 to 10 percent"}
    m = analyst.measure(g, "Q3 FY25", q, a)
    assert m["status"] == "delivered" and m["actual"] == 9.7 and m["span"] == "FY26"


def test_promise_missed_margin():
    q, a = _quarters()
    g = {"metric": "ebitda_margin", "quantitative": True, "scope": "company", "target_low": 18, "target_high": None, "unit": "%", "period": "Q4 FY26",
         "statement": "Margins above 18% in Q4", "quote": ""}
    m = analyst.measure(g, "Q3 FY26", q, a)
    assert m["status"] == "missed"  # Q4 FY26 margin = 44,141 / 2,94,059 = 15.0%
    assert m["actual"] == 15.0


def test_promise_pending_when_period_open():
    q, a = _quarters()
    g = {"metric": "revenue_growth", "quantitative": True, "scope": "company", "target_low": 15, "target_high": 18, "unit": "%", "period": "FY27",
         "statement": "Grow 15-18% in FY27", "quote": ""}
    m = analyst.measure(g, "Q4 FY26", q, a)
    assert m["status"] == "pending" and "on track" in m["evidence"]  # Q1 FY27 grew 27%


def test_promise_guards():
    q, a = _quarters()
    base = {"metric": "revenue_growth", "quantitative": True, "target_low": 15, "unit": "%", "period": "FY26", "statement": "x", "quote": ""}
    assert analyst.measure({**base, "scope": "Retail segment"}, "Q1 FY26", q, a) is None
    assert analyst.measure({**base, "scope": "company", "basis": "constant currency"}, "Q1 FY26", q, a) is None
    assert analyst.measure({**base, "scope": "company", "statement": "USD revenue growth of 15%"}, "Q1 FY26", q, a) is None
    assert analyst.measure({**base, "scope": "company", "period": "next 2-3 years"}, "Q1 FY26", q, a) is None


def test_merge_and_credibility():
    computed = [{"id": "g1", "made_in": "Q1 FY26 call", "promise": "Revenue growth 8-10% in FY26", "outcome": "delivered", "evidence": "x", "computed": True},
                {"id": "g2", "made_in": "Q3 FY26 call", "promise": "Margin above 18% in Q4", "outcome": "missed", "evidence": "y", "computed": True}]
    model = [{"id": "g1", "made_in": "Q1 FY26 call", "promise": "Revenue growth of 8-10% in FY26", "outcome": "missed"},
             {"id": "q9", "made_in": "Q2 FY26 call", "promise": "Revenue growth 8-10% in FY26 for the company", "outcome": "missed"},
             {"id": "q7", "made_in": "Q2 FY26 call", "promise": "Commission new plant by March", "outcome": "delivered"}]
    merged = analyst.merge_promises(computed, model)
    ids = [p["id"] for p in merged]
    assert ids.count("g1") == 1 and "q9" not in ids and "q7" in ids
    assert next(p for p in merged if p["id"] == "g1")["outcome"] == "delivered"  # code wins
    cred = analyst.credibility(merged)
    assert cred["resolved"] == 3 and cred["score"] == round(10 * 2 / 3, 1)


def test_resolve_period():
    assert analyst.resolve_period("FY27", "Q1 FY27") == ("FY", 27, [1, 2, 3, 4])
    assert analyst.resolve_period("H2 FY27", "Q1 FY27") == ("H", 27, [3, 4])
    assert analyst.resolve_period("Q3 FY2027", "Q1 FY27") == ("Q", 27, [3])
    assert analyst.resolve_period("this year", "Q2 FY27") == ("FY", 27, [1, 2, 3, 4])
    assert analyst.resolve_period("next year", "Q2 FY27") == ("FY", 28, [1, 2, 3, 4])
    assert analyst.resolve_period("over the medium term", "Q2 FY27") is None


def test_transcripts_from_page_maps_calls_to_quarters():
    t = transcripts_from_page(PAGE, 4)
    assert [x["quarter"] for x in t] == ["Q1 FY27", "Q4 FY26", "Q3 FY26"]


def test_condense_transcript_keeps_pages_and_budget():
    pages = [("Moderator: Welcome to the call.\n\n" + "Opening remarks about the quarter. " * 40)] + [
        ("Analyst: question about weather. " * 30 + "\n\nCFO: We expect margin guidance of 18 to 20% for FY27 and capex of 500 crore. " * 3) for _ in range(30)
    ]
    out = condense_transcript(pages, max_chars=6000)
    assert out.startswith("[p.1]")
    assert len(out) < 7000
    assert "guidance" in out


def test_parse_json_lenient():
    assert parse_json('```json\n{"a": 1,}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": [1, 2,], "b": "x}y"} thanks') == {"a": [1, 2], "b": "x}y"}


def test_rank_gemini_prefers_aliases_then_stable():
    names = ["gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash-lite-preview", "gemini-3.8-flash", "gemini-flash-latest",
             "gemini-2.5-pro", "gemini-3-flash-image", "gemini-embedding-001", "gemini-3.7-flash"]
    fast = rank_gemini(names, "fast")
    smart = rank_gemini(names, "smart")
    assert fast[0] == "gemini-flash-lite-latest" and fast[1] == "gemini-3.1-flash-lite"
    assert "gemini-3-flash-image" not in fast and "gemini-embedding-001" not in fast
    assert smart[:3] == ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash"]


def test_same_promise_keeps_different_targets():
    assert analyst._same_promise("Revenue growth of 15% in FY27", "Revenue growth of 20% in FY27") is False
    assert analyst._same_promise("Revenue growth 8-10% in FY26", "Revenue growth of 8-10% in FY26 for the company") is True


def test_yahoo_gap_detected_and_change_labelled():
    from app.push.alerts import Thresholds, evaluate, session_stats
    from app.sources.prices import parse_yahoo_chart

    ts = [1792627200 + i * 86400 for i in range(25)]  # daily, from 2026-10-21-ish
    close = [100.0 + i for i in range(25)]
    close[23] = None  # a hole the day before the last session
    close[24] = 140.0
    payload = {"chart": {"result": [{"meta": {"gmtoffset": 19800}, "timestamp": ts,
               "indicators": {"quote": [{"close": close, "volume": [1000] * 25, "open": close, "high": close, "low": close}]}}]}}
    d = parse_yahoo_chart(payload)
    assert len(d["gaps"]) == 1
    stats = session_stats(d["bars"], 20, d["gaps"])
    last = stats[-1]
    assert last["since"] == d["bars"][-2]["date"]
    hits = evaluate(last, Thresholds(price_pct=4))
    assert hits[0]["kind"] == "price" and "since" in hits[0]["label"] and "in a day" not in hits[0]["label"]


def test_results_identities_catch_mixed_statements():
    from app.push.enrich import check_identities

    good = {"revenue_from_operations": 49244, "other_income": 848, "total_income": 50092, "total_expenses": 39029,
            "profit_before_tax": 11063, "net_profit": 7838}
    assert check_identities(good) == []
    mixed = dict(good, revenue_from_operations=45472)  # revenue lifted from another statement
    assert "revenue + other income != total income" in check_identities(mixed)
    bad_pbt = dict(good, profit_before_tax=9000)
    assert "total income - expenses != PBT" in check_identities(bad_pbt)


def test_pick_results_pages_prefers_dense_consolidated_statement():
    from app.sources.pdfs import pick_results_pages

    items = "Revenue from operations Other income Total income Employee benefit expenses Finance cost Depreciation Total expenses Profit before tax Tax expense Profit for the period"
    pages = ["Press release: revenue grew, profit before tax rose" * 3,
             "Statement of unaudited standalone results for the quarter ended " + items,
             "Statement of unaudited consolidated results for the quarter ended " + items,
             "continued: earnings per share, other comprehensive income",
             "IFRS statement in US$ million " + items,
             "Segment information revenue from operations"]
    assert pick_results_pages(pages) == [1, 2, 3]
    assert pick_results_pages(pages, 2) == [2, 3]  # consolidated statement + its continuation only


def test_websearch_parses_duckduckgo_html():
    from app.sources.websearch import parse_results

    page = (
        '<div class="result results_links"><h2 class="result__title"><a rel="nofollow" class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fin.linkedin.com%2Fin%2Fravi-kumar&amp;rut=abc">Ravi Kumar - CFO - Bajaj Finance | LinkedIn</a></h2>'
        '<a class="result__snippet" href="x">Experience: <b>Bajaj Finance</b> &amp; HDFC Bank. Education: ICAI</a></div>'
        '<div class="result"><h2><a rel="nofollow" class="result__a" href="https://example.com/news">Some news</a></h2>'
        '<a class="result__snippet" href="y">Other text</a></div>'
    )
    r = parse_results(page)
    assert r[0] == {"title": "Ravi Kumar - CFO - Bajaj Finance | LinkedIn", "url": "https://in.linkedin.com/in/ravi-kumar",
                    "snippet": "Experience: Bajaj Finance & HDFC Bank. Education: ICAI"}
    assert r[1]["url"] == "https://example.com/news"
