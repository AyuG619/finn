# FINN: a financial investment agent that filters

**Live (open it on your phone):** https://finn-rvpm.onrender.com
On iPhone, tap Share → *Add to Home Screen* to get push alerts. On Android, Chrome works directly.

FINN watches an 18-company NSE portfolio and does two jobs:

- **Push.** It tells you what changed and why you should care. It ranks every exchange filing and unusual
  price/volume move, reads the few that matter, and folds the routine paperwork away.
- **Pull.** It researches any NSE-listed company on demand from its own annual report and earnings-call
  transcripts. The centrepiece holds what management *promised* against what the filed numbers *delivered*.

The point of view: an investor's scarce resource is attention, so FINN is built for **triage, not
browsing**. The Brief is an inbox you clear, not a feed you scroll. The chat bar is a **command line,
not a destination**: one bar, everywhere, with voice, that either takes you somewhere ("research
trent", "show last week", "alert me on 3% moves") or answers a question from the company's own documents.

---

## What it does

### Push: the Brief

| What you see | How FINN decides |
|---|---|
| **Needs you**: slips you can swipe away when seen | Filings with materiality ≥ 70 and price/volume alerts, ranked by score + your position weight |
| **Worth a glance**: one line each, tap to expand | Materiality 40–69 |
| **Folded away**: counts per category | Compliance paperwork (trading-window closures, Reg 74(5) certificates, newspaper ads …) |
| **Results coming up** | NSE's board-meeting calendar, filtered to your holdings |
| **Time window**: since you last looked, 24 h, 3 days, week, month | Your choice; the default is 24 h |

Each kind of slip has its own anatomy:

- **Results** → the actual results filing is read (scanned PDFs too) and turned into **Revenue, EBITDA, PBT
  and PAT, with QoQ and YoY change** and the margin change in bps. Every column is **cross-checked against
  screener.in**: the new quarter once screener has it, and the previous and year-ago columns on results day,
  before it does. The slip says what matched. For lenders, EBITDA becomes pre-provision profit, because
  EBITDA means nothing for a bank.
- **Management change** → who is leaving, who is coming, the reason given, and a **short professional
  background of the incoming person**. It combines the profile SEBI requires in the filing with a web
  search (LinkedIn preferred) and links to the sources.
- **Unusual move** → % move, volume vs its trailing average, a 30-session sparkline, the **likely driver**
  (the most material filing since the previous close), and the effect on your book in bps.
- **Everything else** → one specific line ("Wins ₹2,140 cr order from NHPC …"), key facts and a "so what",
  with the amount expressed as a % of market cap.

Every slip says **why it's here** (e.g. *"results filing; exchange flagged as critical; 9% of your book"*),
so the ranking is never a black box.

**Triggered alerts (Watch).** You set the thresholds in plain sentences: *"Tell me when a holding moves
more than [4%] in a day, or trades more than [2×] its usual volume over the past [20 days]"*. Then you pick
the filing types that should always reach you. FINN checks every 15 minutes and sends **web push
notifications** to your phone. Each phone's subscription carries its own rules.

### Pull: Research (any NSE ticker)

Type any NSE symbol or company name. Within a few seconds the numbers sections appear; within about a minute
the model-written sections follow, with live progress shown.

1. **Financial snapshot.** TTM revenue, margins, PAT, ROE/ROCE, D/E and valuation, plus rule-written signals.
2. **Promised vs delivered.** Guidance extracted from the last four earnings calls, each item stamped
   *Delivered / Missed / Partly / Pending*, with a credibility score.
   - **Quantitative, company-level, rupee promises are scored by code** against reported quarterly and annual numbers.
   - Qualitative promises are judged by the model, citing the later call that settles them.
   - Constant-currency, USD and segment-level guidance is never forced against consolidated ₹ numbers.
3. **Narrative vs numbers.** What management says next to what the numbers show: agrees, diverges or mixed.
4. **Current guidance**, and a **bull vs bear** case where every point carries its evidence.
5. **Business snapshot** from the annual report: what the company does, segments and revenue mix, the
   auditor's opinion, key audit matters, contingent liabilities, and the risks management itself flags (all with page numbers).
6. **Trajectory, balance sheet, cash quality, ownership.** These are computed deterministically: CAGRs,
   margin trend, latest-quarter breaks, D/E, interest cover, the working-capital cycle, CFO/PAT conversion,
   FCF and promoter-stake changes.
7. **What analysts pressed on**, per call, with management's answer and the tone.
8. **Ask the documents.** Grounded Q&A over the transcripts and annual report (BM25 retrieval, every claim cited).

**Primary sources only.** The research reads the company's annual report and call transcripts, plus
standardised filed numbers from screener.in. It reads no news, no broker notes, and none of screener's
AI summaries. Prompts forbid model memory, and every claim carries a page reference.

---

## Architecture

```
              ┌──────────── data (all free, no keys) ────────────┐
 NSE  ────────┤ announcements · event calendar · annual reports  │
 BSE  ────────┤ announcements (fallback) · PDF archive           │──► cache (memory + disk, TTLs,
 screener.in ─┤ standardised financials · links to AR/transcripts│     single-flight de-dupe)
 Yahoo ───────┤ EOD price & volume (gaps patched from screener)  │
              └───────────────────────────────────────────────────┘
                          │
        ┌─────────────────┴──────────────────┐
        ▼                                    ▼
  PUSH ENGINE                          RESEARCH ENGINE (background job, progress)
  classify (rules → model)             numbers → deterministic sections (no LLM)
  materiality score                    docs → retrieve passages (keyword/TOC page selection)
  bundle (results, mgmt)               map: fast model reads each call + AR into JSON notes
  price/volume alerts                  check: CODE scores measurable promises
  enrich top items (lazy, cached)      reduce: smart model writes verdicts, NvN, bull/bear
        │                                    │
        └──────────────┬─────────────────────┘
                       ▼
            LLM ROUTER: tiers fast/smart · Gemini ⇄ Groq · model discovery
            · rate pacing · quota-aware fallback · content-hash cache · token meter
                       │
              FastAPI (/api/*) + static PWA (React, hand-built SVG charts)
              background loop: refresh every 15 min → enrich → web push
```

### Model routing and cost

| Step | Model | Why |
|---|---|---|
| Classify filings | **Rules** (exchange tags + keyword rules, 80+ tests on real filings) | Free, instant, deterministic; ~90% of filings never touch a model |
| Ambiguous filings, one-liners, results extraction, management changes, transcript and AR notes | **Fast tier**: Gemini Flash-Lite (native PDF, so scanned filings work), Groq fallback | Bulk reading |
| A results read that fails the checks | **Smart tier reads the page images** (Gemini Flash) | Scanned filings' OCR layers drop lines; pay for the better model only when the cheap read is wrong |
| Promise verdicts, narrative vs numbers, bull/bear | **Smart tier**: Gemini Flash, Groq gpt-oss-120b fallback | The one step where reasoning matters |
| Incoming executive's background | Gemini + Google Search grounding → Groq compound (built-in web search) → keyless web search + fast model | Needs the web; everything else must not. Free search quotas are small, so there are three routes |
| All arithmetic (growth, margins, EBITDA, promise scoring, CFO/PAT) | **Code** | Models don't do sums here |

- Model names are **discovered from each API key at startup** (Gemini's model list is ranked: aliases,
  then newest stable, then previews), so the app survives model renames.
- A 429 or a daily-quota error moves the call to the next model; daily quotas cool down for an hour.
- Every model call is cached by a content hash, forever. Filings and transcripts never change, so a
  second visit costs nothing.
- Only relevant pages are sent:
  - results: the 2 pages with the densest P&L statement (consolidated preferred);
  - annual reports: about 25 TOC/keyword-selected pages out of 300–500;
  - transcripts: opening remarks plus the most forward-looking Q&A passages.
- A typical research run is 6 model calls and about 70k tokens, which is **≈ $0.01 at list prices and ₹0 on free tiers**.
  *Watch → Under the hood* shows live token, cache and model counts.

### Resilience (so the demo runs)

- **Filings.** From Render's servers BSE's announcement API returns 403 and NSE answers, so the order is
  **NSE → BSE → screener**, adaptive at runtime: a failing feed drops to the back for 10 minutes.
- **Prices.** Yahoo has occasional holes (it may know a session happened but have no print). FINN fills
  them from screener's NSE series. If it can't, the move is labelled "since 23 Oct" instead of "in a day".
- **Results you can trust.** A read must satisfy the accounting identities (revenue + other income = total
  income; income − expenses ± exceptional = PBT) and match screener.in on every column screener has.
  - A clean power-of-ten mismatch is a misread unit (₹ million read as crore). It is corrected and labelled.
  - Any other mismatch triggers the second read from the page images. If that also disagrees, screener's
    standardised numbers are shown, labelled as such, with the size of the disagreement.
- **Degraded answers.** When a model or PDF is unavailable, FINN shows the deterministic version (the
  exchange headline, or screener's numbers labelled as such). Unverified and degraded cards are re-checked
  later; a bad answer is never cached forever.
- **Offline UI.** The UI keeps the last brief in local storage and shows it instantly while refreshing.

---

## Judgment calls and trade-offs

- **Category ≠ attention.** A newspaper ad of results is category *results* but tier *routine*.
  Materiality is scored, not looked up: a ₹1,234 cr order means more for a ₹500 cr company than for L&T.
- **The book matters.** A 5% fall in a 9% position outranks the same fall in a 4% position. Moves show their
  effect on the portfolio in bps.
- **One event, one card.** A CFO's resignation and their successor's appointment form one card; a results filing
  carries its deck, press release and newspaper ad.
- **Code over model for numbers.** Promise verdicts that arithmetic can decide are decided by arithmetic.
  The model can explain them, never overrule them.
- **Honest units.** EBITDA is not shown for banks. Guidance in USD or constant currency is not marked "missed"
  against rupee numbers. Intraday volume is not compared with full-day averages.
- **Interaction.** Swipe to clear, "since you last looked", plain-sentence alert rules, voice input, and a
  chat bar that routes before it chats.

## Deliberately left out

- Intraday tick data and live prices: the brief asks for EOD, and EOD is what "unusual" should be judged on.
- News, social sentiment and broker research: excluded by design from pull; push stays on exchange filings.
- User accounts: preferences live on the device; push subscriptions carry their own rules. A real product needs auth.
- Persistent storage: Render's free tier has an ephemeral disk, so caches and push subscriptions reset on redeploy.
  A small Postgres or Redis instance is the first thing to add.
- XBRL parsing of results: more exact than PDF reading, but NSE's XBRL host is unreliable from cloud IPs.
  Reading the PDF and cross-checking every column against screener works everywhere.

## Where it goes next

1. **A memory of management.** Persist every extracted promise across quarters, so the credibility score
   becomes a track record and the next call's guidance is checked against the last automatically.
2. **Results day mode.** Detect the results filing within minutes, push the four-number card, then push again
   when the transcript lands with "what changed in guidance".
3. **Portfolio-level push.** Thematic alerts across holdings, such as "three of your holdings guided margins
   down this week".
4. **Accounts, sync and a scheduled worker** separate from the web process; an XBRL path where reachable.

---

## Run it locally

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export GEMINI_API_KEY=...  GROQ_API_KEY=...      # either one is enough; neither = numbers-only mode
uvicorn app.main:app --reload --port 8765

# frontend (dev server proxies /api to :8765)
cd ../frontend && npm ci && npm run dev

# tests (no network needed): 99 unit tests
cd ../backend && python -m pytest -q
# 20 end-to-end UI flows (Playwright) against the mock server below
python tests/ui_flows.py

# UI without network or keys: real digest pipeline over captured NSE filings, canned model output
python -m tests.mock_server    # then open http://127.0.0.1:8765
```

**Deploy.** `render.yaml` is a Render Blueprint (free plan, Singapore). Set `GEMINI_API_KEY` and/or
`GROQ_API_KEY`. `frontend/dist` is committed, so Render only installs Python. A GitHub Action pings the
service every 10 minutes so it never sleeps. `/api/diag` reports which sources and models the deployment
can reach; `/api/usage` reports token and cache stats.

### Repo map

```
backend/app/
  sources/   nse.py bse.py screener.py prices.py pdfs.py      data connectors
  push/      classify.py alerts.py digest.py enrich.py notify.py
  research/  fundamentals.py documents.py analyst.py engine.py
  llm/       router.py prompts.py jsonutil.py
  main.py    API, background refresh, web push, static PWA
backend/tests/   classifier tests on real filings, engine maths, fixtures, mock server
frontend/src/    App, views (Brief, Book, Research, Watch), slips, charts, Ask bar
```

## Portfolio

- **Holdings (mock weights).** RELIANCE, ICICIBANK, INFY, BHARTIARTL, BAJFINANCE, LT, ETERNAL, MARUTI,
  HINDUNILVR, SUNPHARMA, NTPC, TITAN, DIXON, TATASTEEL, ULTRACEMCO, POLYCAB, ZENTEC, HAPPSTMNDS.
  - They span 15 sectors, from mega-caps to small-caps.
  - Most report Q2 results between 27 October and 5 November, so the digest is live during review.
- **Ad-hoc research set.** PERSISTENT, ASIANPAINT, TRENT, KAYNES. The engine works on any NSE ticker; the Watch tab lets
  you replace the whole portfolio.

## Build history

Built with Claude (Anthropic) as a coding agent: design, code, tests and live verification were done in one
long session, which is shared alongside this repository.
