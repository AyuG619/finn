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


async def test_cache_stale_while_revalidate(tmp_path):
    import asyncio
    import time

    from app import cache

    calls = []

    async def fn():
        calls.append(time.time())
        return {"n": len(calls)}

    key = f"test:swr:{time.time()}"
    assert (await cache.cached(key, 0.05, fn, swr=60))["n"] == 1
    await asyncio.sleep(0.08)  # now stale but inside the SWR window
    assert (await cache.cached(key, 0.05, fn, swr=60))["n"] == 1  # served stale immediately
    await asyncio.sleep(0.05)  # background refresh has run
    assert len(calls) == 2
    assert (await cache.cached(key, 0.05, fn, swr=60))["n"] == 2


def test_unit_sniffed_from_ocr_header():
    from app.push.enrich import sniff_unit

    assert sniff_unit("3. Audited Consolidated Balance Sheet \n(in if' crore) \nParticulars") == "crore"
    assert sniff_unit("(INR in million, except per share data)") == "million"
    assert sniff_unit("Rs. in Lakhs") == "lakhs"
    assert sniff_unit("Revenue from operations 1,234") is None


def test_shift_quarter():
    from app.push.enrich import shift_quarter

    assert shift_quarter("2026-09-30", -1) == "2026-06-30"
    assert shift_quarter("2026-09-30", -4) == "2025-09-30"
    assert shift_quarter("2026-03-31", -1) == "2025-12-31"
    assert shift_quarter("2025-12-31", 1) == "2026-03-31"


def _ref(rev, pbt, q):
    return {"revenue": rev, "pbt": pbt, "ebitda": None, "pat": None, "quarter": q}


def test_misread_unit_is_corrected_from_earlier_quarters():
    """A filing in ₹ million read as crore is 10x off on every column screener has: fix, don't reject."""
    from app.push.enrich import cross_check, fix_scale

    heads = {"current": {"revenue": 533402.0, "pbt": 31658.0}, "previous_quarter": {"revenue": 524551.0, "pbt": 43413.0},
             "year_ago_quarter": {"revenue": 423323.0, "pbt": 42909.0}}
    refs = {"previous_quarter": _ref(52455, 4341, "2026-06-30"), "year_ago_quarter": _ref(42332, 4291, "2025-09-30")}
    f = fix_scale(heads, refs, bank=False)
    assert f == 0.1
    fixed = {k: {m: v * f for m, v in h.items()} for k, h in heads.items()}
    ver = cross_check(fixed, refs, bank=False)
    assert ver["ok"] and not ver["current_checked"]
    assert fix_scale(fixed, refs, bank=False) is None  # already right: nothing to fix


def test_cross_check_rejects_a_misaligned_column():
    """INFY Q2 FY27: the OCR layer dropped the revenue line, so the model took the year-ago total income."""
    from app.push.enrich import cross_check, fix_scale

    heads = {"current": {"revenue": 45472.0, "pbt": 11063.0}, "previous_quarter": {"revenue": 48211.0, "pbt": 11028.0},
             "year_ago_quarter": {"revenue": 44490.0, "pbt": 10229.0}}
    refs = {"current": _ref(49244, 11063, "2026-09-30"), "previous_quarter": _ref(48211, 11028, "2026-06-30"),
            "year_ago_quarter": _ref(44490, 10229, "2025-09-30")}
    assert fix_scale(heads, refs, bank=False) is None  # 7.7% off is not a unit problem
    ver = cross_check(heads, refs, bank=False)
    assert not ver["ok"] and ver["current_checked"]
    assert ver["revenue_diff_pct"] == 7.66
    assert [c["ok"] for c in ver["checks"]] == [False, True, True]


def test_cross_check_banks_use_interest_earned_and_small_pbt_tolerance():
    from app.push.enrich import cross_check

    heads = {"current": {"revenue": 60000.0, "interest": 50000.0, "pbt": 120.0}}
    refs = {"current": _ref(50100, 100, "2026-09-30")}
    ver = cross_check(heads, refs, bank=True)
    assert ver["ok"]  # PBT is 20% off but only 0.04% of revenue
    assert cross_check(heads, refs, bank=False)["ok"] is False  # total income vs interest would fail


def test_pick_results_pages_tolerates_ocr():
    from app.sources.pdfs import pick_results_pages, pnl_score

    ocr = ("Statement of Consolidatcd Unaudited Financial Results for the quarter ended September 30, 2026 "
           "Total revenue from operalions Other income Total income Finance costs Depreciation and amortisation "
           "Total expenses Profit before tax Tax expense Profit for the period")
    sa = ocr.replace("Consolidatcd", "Standalonc")
    assert pnl_score(ocr)[0] >= 8 and pnl_score(ocr)[1] > pnl_score(sa)[1]
    assert pick_results_pages(["cover letter", sa, "balance sheet", ocr, "notes"], 1) == [3]


def test_websearch_parses_bing():
    import base64

    from app.sources.websearch import parse_bing

    target = "https://in.linkedin.com/in/ravi-kumar"
    u = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    page = (
        '<ol id="b_results"><li class="b_algo" data-id=""><h2><a href="https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=' + u + '&amp;ntb=1" h="ID=SERP">'
        'Ravi Kumar - <strong>CFO</strong> - LinkedIn</a></h2><div class="b_caption"><p class="b_lineclamp2">Experience: Bajaj Finance &amp; HDFC Bank</p></div></li>'
        '<li class="b_algo"><h2><a href="https://example.com/x">Other</a></h2><div class="b_caption"><p>Plain snippet</p></div></li></ol>'
    )
    r = parse_bing(page)
    assert r[0] == {"title": "Ravi Kumar - CFO - LinkedIn", "url": target, "snippet": "Experience: Bajaj Finance & HDFC Bank"}
    assert r[1] == {"title": "Other", "url": "https://example.com/x", "snippet": "Plain snippet"}


def test_groq_compound_sources():
    from app.llm.router import groq_sources

    msg = {"content": "{}", "executed_tools": [
        {"type": "search", "search_results": {"results": [{"title": "A", "url": "https://a.com", "content": "x"},
                                                          {"title": "A again", "url": "https://a.com"}]}},
        {"type": "visit", "output": "page"}, "junk"]}
    assert groq_sources(msg) == [{"title": "A", "url": "https://a.com"}]


def _fake_extraction(rev, prev_rev, yoy_rev, unit="crore"):
    def col(r):
        # A self-consistent column: revenue + other income = total income; TI - TE = PBT.
        return {"revenue_from_operations": r, "other_income": r * 0.02, "total_income": r * 1.02, "total_expenses": r * 0.8,
                "finance_costs": r * 0.01, "depreciation": r * 0.03, "profit_before_tax": r * 0.22, "net_profit": r * 0.16}
    return {"basis": "consolidated", "unit": unit, "is_bank": False,
            "periods": {"current": "2026-09-30"},
            "columns": {"current": col(rev), "previous_quarter": col(prev_rev), "year_ago_quarter": col(yoy_rev)}}


def _results_env(monkeypatch, reads, screener_quarters):
    from app.llm.router import LLMResult
    from app.push import enrich

    keys = sorted(screener_quarters)
    page = {"basis": "consolidated", "is_bank": False, "quarters": {"keys": keys, "rows": {
        "Sales": [screener_quarters[k] for k in keys],
        "Operating Profit": [screener_quarters[k] * 0.24 for k in keys],
        "Profit before tax": [screener_quarters[k] * 0.22 for k in keys],
        "Net Profit": [screener_quarters[k] * 0.16 for k in keys]}}}
    calls = []

    async def fake_complete(task, **kw):
        calls.append((task, kw["tier"], kw["pdf"] is not None))
        data = reads[len(calls) - 1]
        kw["validate"](data)
        return LLMResult("{}", data, "fake:model", False, 10, 10)

    async def fake_page(sym):
        return page

    async def fake_pdf(url):
        return b"%PDF"

    text_page = "Statement of Consolidated Unaudited Results quarter ended " + " ".join(
        ["revenue from operations", "other income", "total income", "finance cost", "depreciation", "profit before tax",
         "tax expense", "total expenses"]) + " " + "x" * 300
    monkeypatch.setattr(enrich, "complete", fake_complete)
    monkeypatch.setattr(enrich, "available", lambda: True)
    monkeypatch.setattr(enrich.screener, "company_page", fake_page)
    monkeypatch.setattr(enrich.pdfs, "fetch_pdf", fake_pdf)
    monkeypatch.setattr(enrich.pdfs, "_extract", lambda data, n: ["cover " + "y" * 300, text_page, "notes " + "z" * 300])
    monkeypatch.setattr(enrich.pdfs, "trim_pdf", lambda data, pages: b"%PDF-trimmed")
    monkeypatch.setattr(enrich.pdfs, "raster_pages", lambda data, pages: b"%PDF-images")
    return enrich, calls


FILING = {"id": "NSE:1", "pdf_url": "https://x/y.pdf", "time": "2026-10-23T16:07:00+05:30"}


async def test_results_card_second_read_fixes_a_bad_first_read(monkeypatch):
    enrich, calls = _results_env(monkeypatch, [_fake_extraction(45472, 48211, 44490), _fake_extraction(49244, 48211, 44490)],
                                 {"2025-09-30": 44490, "2026-06-30": 48211, "2026-09-30": 49244})
    card = await enrich.results_card(FILING, {"symbol": "INFY"})
    assert calls == [("results_extract", "fast", False), ("results_extract_vision", "fast", True)]
    assert card["source"] == "results filing" and card["second_read"] and card["verified"]["ok"]
    assert card["metrics"][0]["value"] == 49244 and card["metrics"][0]["yoy"] == 10.7
    assert "provisional" not in card and "filing_read_rejected" not in card


async def test_results_card_falls_back_to_screener_when_both_reads_disagree(monkeypatch):
    bad = _fake_extraction(45472, 48211, 44490)
    enrich, calls = _results_env(monkeypatch, [bad, bad, bad], {"2025-09-30": 44490, "2026-06-30": 48211, "2026-09-30": 49244})
    card = await enrich.results_card(FILING, {"symbol": "INFY"})
    assert [c[1] for c in calls] == ["fast", "fast", "smart"]
    assert card["source"] == "screener.in standardised table" and "7.7%" in card["filing_read_rejected"]
    assert card["metrics"][0]["value"] == 49244


async def test_results_card_fresh_quarter_in_million_is_rescaled(monkeypatch):
    enrich, calls = _results_env(monkeypatch, [_fake_extraction(533402, 524551, 423323, unit=None)],
                                 {"2025-09-30": 42332, "2026-06-30": 52455})  # screener lacks the new quarter
    card = await enrich.results_card(FILING, {"symbol": "MARUTI"})
    assert len(calls) == 1
    assert card["metrics"][0]["value"] == 53340.2 and card["unit_note"]
    assert card["verified"]["ok"] and not card["verified"]["current_checked"] and card["provisional"]


def test_trim_pdf_keeps_selected_pages_and_raster_fallback():
    import fitz

    from app.sources import pdfs

    doc = fitz.open()
    for i in range(5):
        doc.new_page().insert_text((72, 72), f"page {i + 1} revenue from operations")
    for i in range(4):  # links into a page that won't be copied
        doc.load_page(i).insert_link({"kind": fitz.LINK_GOTO, "page": 4, "from": fitz.Rect(70, 60, 200, 80)})
    data = doc.tobytes()
    out = fitz.open(stream=pdfs.trim_pdf(data, [3, 1, 9]), filetype="pdf")
    assert out.page_count == 2 and "page 4" in out.load_page(0).get_text() and "page 2" in out.load_page(1).get_text()
    src = fitz.open(stream=data, filetype="pdf")
    raster = fitz.open(stream=pdfs._raster_pdf(src, [0, 2]), filetype="pdf")
    assert raster.page_count == 2 and raster.load_page(0).get_images()
    clean = fitz.open(stream=pdfs.raster_pages(data, [1, 7]), filetype="pdf")
    assert clean.page_count == 1 and clean.load_page(0).get_text().strip() == ""  # image only, no OCR layer


def test_bio_keeps_only_linkedin_profiles_and_legible_sources():
    from app.push.enrich import _bio_result

    b = _bio_result({"found": True, "headline": "h", "linkedin_url": "https://www.linkedin.com/posts/nhpclimited_nhpc-activity-75"},
                    [{"title": "à¤¨à¥‡à¤¶à¤¨à¤² à¤¸à¥à¤Ÿà¥‰à¤• à¤à¤•à¥à¤¸à¤šà¥‡à¤‚à¤œ", "url": "https://nsearchives.nseindia.com/x.pdf"},
                     {"title": "Corporate Announcements", "url": "https://ntpc.co.in/investors"},
                     {"title": "dup", "url": "https://ntpc.co.in/investors"}])
    assert b["linkedin_url"] is None
    assert b["sources"] == [{"title": "nsearchives.nseindia.com", "url": "https://nsearchives.nseindia.com/x.pdf"},
                            {"title": "Corporate Announcements", "url": "https://ntpc.co.in/investors"}]
    assert _bio_result({"found": True, "linkedin_url": "https://in.linkedin.com/in/ravi-kumar-123"}, [])["linkedin_url"] == "https://in.linkedin.com/in/ravi-kumar-123"
