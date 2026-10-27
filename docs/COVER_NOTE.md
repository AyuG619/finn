Subject: ENAM - IIT screening - 2027

Hello ENAM team,

Please find my submission for the FINN build challenge below.

- **Live prototype (mobile-first, open on a phone):** https://finn-rvpm.onrender.com
  (iPhone: Share → Add to Home Screen to receive push alerts)
- **Repository:** https://github.com/AyuG619/finn
- **AI build history:** [link to the shared Claude session]

**What I built**

FINN is a phone-first PWA built around one idea: an investor's scarce resource is attention, so the product
is for triage, not browsing.

- *Push.* The Brief ranks every NSE/BSE filing and unusual EOD move across an 18-stock mock portfolio for a
  window you choose ("since you last looked", 24 h, 3 days, week, month). The few material items become slips
  you swipe away; the next tier is one line each; routine compliance paperwork is folded into counts. Each slip
  is shaped to its event:
  - Results → Revenue, EBITDA, PBT and PAT, QoQ and YoY, read from the actual results filing (scanned PDFs
    included) and cross-checked against screener.in.
  - Management changes → who is in and who is out, with a short web-grounded background of the incoming person.
  - Price/volume breaches → the likely driver filing and the effect on the portfolio in bps.

  Thresholds are set in plain sentences ("tell me when a holding moves more than 4%…") and delivered as real
  web-push notifications.
- *Pull.* Research works on any NSE ticker. It reads the last four earnings-call transcripts and the latest
  annual report, then produces:
  - a promise ledger that stamps each piece of guidance Delivered, Missed, Partly or Pending;
  - narrative vs numbers, current guidance, a bull/bear case with evidence, and the business, balance-sheet
    and cash-quality sections;
  - a Q&A box that answers only from those documents, with page citations.

**Key decisions and trade-offs**

1. **Rules first, models second.** About 90% of filings are classified by rules over exchange tags (80+ tests
   on real filings); a cheap model only reads what's ambiguous or material. Materiality is scored, not looked
   up: deal size as a % of market cap and position weight move the rank.
2. **Code decides arithmetic.** Promise verdicts that numbers can settle are computed in code against filed
   results; the model explains but cannot overrule them. Guidance in USD, constant currency or for a segment
   is never scored against consolidated rupee numbers.
3. **Model routing for cost.** Gemini Flash-Lite reads filings and transcripts; Flash writes only the
   reasoning step; Groq is the fallback. Models are discovered from the key at startup, calls are cached by
   content hash, and only selected pages are sent. A full research run is about 6 calls and 70k tokens,
   roughly $0.01 at list prices and ₹0 on free tiers.
4. **Robust to real data.** BSE's API blocks cloud IPs, so the feed falls back NSE → BSE → screener.
   Yahoo's missing sessions are patched from NSE prices. A results read that breaks accounting identities or
   disagrees with screener's table is rejected, never shown as fact.

**Deliberately left out**

User accounts and a database: preferences live on the device, and caches reset on redeploy. Also news,
social and broker inputs, which are excluded by design. And XBRL parsing, which is more exact than PDF
reading but unreliable from cloud hosts.

**Where I'd take it next**

- Persist every promise across quarters, so credibility becomes a track record.
- A "results day" mode that pushes the numbers card, then the guidance changes once the transcript lands.
- Portfolio-level alerts, e.g. "three holdings guided margins down this week".
- A separate scheduled worker with real storage.

Thank you for your time.

Regards,
Ayush
