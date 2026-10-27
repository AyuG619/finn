"""Classify exchange filings into the reference categories and score their materiality.

Rules first, model second. The exchange already tags most filings (BSE sub-categories); keyword
rules disambiguate the generic buckets ("General", "Outcome of Board Meeting", NSE free-text
descriptions). Only what is still ambiguous goes to a cheap model, batched into one call.

Category answers "what is this?"; tier answers "does it deserve my attention?". A newspaper ad of
results is category *results* but tier *routine*.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CATEGORIES: dict[str, str] = {
    "results": "Results",
    "m_and_a": "M&A",
    "fund_raise": "Fund raise",
    "management_change": "Management change",
    "litigation": "Litigation & regulatory",
    "insider_trading": "Insider trading",
    "earnings_call": "Earnings call",
    "investor_meetings": "Investor meetings",
    "credit_rating": "Credit rating",
    "dividend": "Dividend",
    "bonus_split": "Bonus / split",
    "capex": "Capex",
    "press_release": "Press release",
    "postal_ballot": "Postal ballot",
    "agm_egm": "AGM / EGM",
    "pledging": "Pledging",
    "unclassified": "Routine paperwork",
}

TIER_HIGH = 70
TIER_NOTABLE = 40

# (category, subtype, base score) for BSE sub-categories. None subtype = generic.
SUBCATEGORY_MAP: dict[str, tuple[str, str | None, int]] = {
    "financial results": ("results", "filing", 92),
    "earnings call transcript": ("earnings_call", "transcript", 42),
    "investor presentation": ("results", "deck", 38),
    "credit rating": ("credit_rating", None, 52),
    "change in management": ("management_change", None, 60),
    "change in directorate": ("management_change", None, 55),
    "resignation of chief financial officer (cfo)": ("management_change", None, 82),
    "appointment of chief financial officer (cfo)": ("management_change", None, 78),
    "resignation of director": ("management_change", None, 55),
    "cessation": ("management_change", None, 45),
    "appointment of company secretary / compliance officer": ("management_change", "cs", 22),
    "resignation of company secretary / compliance officer": ("management_change", "cs", 25),
    "demise": ("management_change", None, 50),
    "resignation of statutory auditors": ("management_change", "auditor_exit", 72),
    "appointment of statutory auditor/s": ("unclassified", None, 12),
    "acquisition": ("m_and_a", None, 74),
    "updates on acquisition": ("m_and_a", None, 62),
    "scheme of arrangement": ("m_and_a", None, 66),
    "restructuring": ("m_and_a", None, 62),
    "memorandum of understanding /agreements": ("m_and_a", "agreement", 52),
    "joint venture": ("m_and_a", None, 66),
    "disinvestment": ("m_and_a", None, 70),
    "raising of funds": ("fund_raise", None, 68),
    "issue of securities": ("fund_raise", None, 55),
    "allotment of equity shares": ("fund_raise", "allotment", 35),
    "buy back": ("fund_raise", "buyback", 72),
    "daily buy back of equity shares": ("fund_raise", "buyback_daily", 8),
    "qualified institutional placement": ("fund_raise", None, 74),
    "preferential issue": ("fund_raise", None, 68),
    "rights issue": ("fund_raise", None, 72),
    "dividend": ("dividend", None, 36),
    "dividend updates": ("dividend", None, 30),
    "record date": ("dividend", "record_date", 22),
    "bonus": ("bonus_split", None, 62),
    "stock split": ("bonus_split", None, 62),
    "sub-division": ("bonus_split", None, 62),
    "award of order / receipt of order": ("press_release", "order", 50),
    "press release / media release": ("press_release", None, 42),
    "press release / media release (revised)": ("press_release", None, 30),
    "clarification": ("press_release", "clarification", 55),
    "postal ballot": ("postal_ballot", None, 14),
    "agm": ("agm_egm", None, 14),
    "egm": ("agm_egm", None, 20),
    "court convened meeting": ("agm_egm", None, 30),
    "analyst / investor meet": ("investor_meetings", None, 14),
    "updates - corporate insolvency resolution process  (cirp)": ("litigation", "insolvency", 72),
    "updates - corporate insolvency resolution process (cirp)": ("litigation", "insolvency", 72),
    "intimation of meeting of committee of creditors": ("litigation", "insolvency", 55),
    "closure of trading window": ("unclassified", None, 3),
    "allotment of esop / esps": ("unclassified", None, 5),
    "certificate under reg. 74 (5) of sebi (dp) regulations, 2018": ("unclassified", None, 2),
    "reg. 54 - asset cover details": ("unclassified", None, 3),
    "monitoring agency report": ("unclassified", None, 6),
    "reg. 32 (1), (3) - statement of deviation & variation": ("unclassified", None, 8),
    "reg. 57 (1) - certificate of interest payment/principal in case of ncd": ("unclassified", None, 3),
    "intimation of repayment of commercial paper (cp)": ("unclassified", None, 4),
    "amendments to memorandum & articles of association": ("unclassified", None, 12),
    "meeting updates": ("unclassified", None, 10),
    "newspaper publication": ("unclassified", "newspaper", 4),
    "board meeting rescheduled": ("unclassified", "board_meeting", 18),
    "board meeting cancelled": ("unclassified", "board_meeting", 20),
}

# NSE's own announcement descriptions (used when NSE is the feed; there is no sub-category).
NSE_DESC_MAP: dict[str, tuple[str, str | None, int]] = {
    "financial result updates": ("results", "filing", 85),
    "integrated filing- financial": ("results", "filing", 85),
    "integrated filing - financial": ("results", "filing", 85),
    "investor presentation": ("results", "deck", 38),
    "copy of newspaper publication": ("unclassified", "newspaper", 4),
    "shareholders meeting": ("agm_egm", None, 14),
    "corrigendum": ("unclassified", None, 10),
    "certificate under sebi (depositories and participants) regulations, 2018": ("unclassified", None, 2),
    "monitoring agency report": ("unclassified", None, 6),
    "trading window": ("unclassified", None, 3),
    "record date": ("dividend", "record_date", 22),
    "allotment of securities": ("fund_raise", "allotment", 35),
    "corporate insolvency resolution process": ("litigation", "insolvency", 72),
    "esop/esos/esps": ("unclassified", None, 5),
    "options to purchase securities": ("unclassified", None, 5),
    "statement of deviation(s) or variation(s) under reg. 32": ("unclassified", None, 8),
    "bagging/receiving of orders/contracts": ("press_release", "order", 50),
    "issue of securities": ("fund_raise", None, 55),
    "amendment to aoa/moa": ("unclassified", None, 12),
    "committee meeting updates": ("unclassified", None, 15),
    "dividend": ("dividend", None, 36),
    "credit rating": ("credit_rating", None, 52),
    "credit rating- new": ("credit_rating", None, 45),
    "credit rating- revision": ("credit_rating", None, 62),
    "capacity addition": ("capex", None, 62),
    "commencement of commercial production/operations": ("capex", None, 62),
    "extension  of financial year": ("unclassified", None, 20),
    "extension of financial year": ("unclassified", None, 20),
    "giving guarantees/indemnity/ becoming a surety for third party": ("unclassified", None, 30),
    "effect(s) on listed entity due to changed regulatory  framework applicable": ("litigation", "regulatory", 52),
    "effect(s) on listed entity due to changed regulatory framework applicable": ("litigation", "regulatory", 52),
    "pendency of litigation(s)/dispute(s) or the outcome impacting the company": ("litigation", None, 65),
    "action(s) initiated or orders passed": ("litigation", None, 66),
    "product launch": ("press_release", None, 40),
    "buyback": ("fund_raise", "buyback", 70),
    "acquisition": ("m_and_a", None, 70),
    "amalgamation/merger": ("m_and_a", None, 66),
    "scheme of arrangement": ("m_and_a", None, 66),
    "agreements": ("m_and_a", "agreement", 50),
    "sale or disposal-xbrl": ("m_and_a", None, 62),
    "qualified institutional placement": ("fund_raise", None, 74),
    "preferential issue": ("fund_raise", None, 68),
    "rights issue": ("fund_raise", None, 72),
    "bonus": ("bonus_split", None, 62),
    "spurt in volume": ("press_release", "clarification", 55),
    "clarification - financial results": ("results", "ancillary", 40),
}
NSE_MGMT_DESCS = {
    "appointment", "resignation", "cessation", "change in management", "change in director(s)", "demise",
    "resignation of director/kmp/smp", "change in directors/ key managerial personnel/ auditor/ compliance officer/ share transfer agent",
}

# Keyword rules for generic buckets: first match wins, so order matters (specific before broad).
_R = lambda p: re.compile(p, re.I)  # noqa: E731
KEYWORD_RULES: list[tuple[re.Pattern, str, str | None, int]] = [
    (_R(r"newspaper|news paper|publication of (the )?(un)?audited|e-?paper"), "unclassified", "newspaper", 4),
    (_R(r"trading window"), "unclassified", None, 3),
    (_R(r"\b74 ?\(5\)|compliance certificate|loss of share certificate|duplicate share|investor (grievance|complaint)|asset cover|monitoring agency|statement of deviation|regulation 57|esop|esos|esps|employee stock option"), "unclassified", None, 5),
    (_R(r"pledg|encumbrance"), "pledging", None, 55),
    (_R(r"sast|substantial acquisition|regulation 7 ?\(2\)|reg\.? ?7\(2\)|insider trading|prohibition of insider"), "insider_trading", None, 40),
    (_R(r"transcript"), "earnings_call", "transcript", 42),
    (_R(r"audio recording|video recording|earnings call|conference call|con-?call|investors? call"), "earnings_call", "notice", 18),
    (_R(r"analyst|institutional investor|investor meet|investor conference|roadshow|non-deal"), "investor_meetings", None, 14),
    (_R(r"credit rating|rating agency|\bcrisil\b|\bicra\b|care ratings|care edge|india ratings|acuite|brickwork|fitch|moody|s&p global"), "credit_rating", None, 52),
    (_R(r"postal ballot"), "postal_ballot", None, 14),
    (_R(r"annual general meeting|\bagm\b|extra-?ordinary general|\begm\b"), "agm_egm", None, 14),
    (_R(r"\bbonus\b|sub-?division|stock split|split of (equity )?shares"), "bonus_split", None, 62),
    (_R(r"buy-?back"), "fund_raise", "buyback", 70),
    (_R(r"\bqip\b|qualified institution|preferential (issue|allotment)|rights issue|raising of funds|fund ?rais|issue of (non-convertible )?debentures|\bncds?\b|commercial paper|warrants"), "fund_raise", None, 55),
    (_R(r"insolvency|\bcirp\b|\bnclt\b|\bnclat\b|show cause|penalty|\bfine\b|demand order|tax demand|gst demand|income tax (demand|order|search)|search and seizure|litigation|arbitra|court|tribunal|regulatory action|sebi order|adjudicat|enforcement directorate"), "litigation", None, 62),
    (_R(r"acqui|merger|amalgamat|demerger|joint venture|\bjv\b|share purchase agreement|stake (sale|purchase|acquisition)|divest|disinvest|scheme of arrangement|incorporation of (a )?(wholly owned )?(step-?down )?subsidiar"), "m_and_a", None, 66),
    (_R(r"resign|appoint|cessation|re-?appoint|re-?designat|retire|demise|key managerial|\bkmp\b|\bceo\b|\bcfo\b|managing director|whole[- ]?time director|chairman|chairperson|change in (director|management)"), "management_change", None, 50),
    (_R(r"capex|capacity expansion|new (plant|facility|unit)|greenfield|brownfield|commission(ing|ed)|capital expenditure|expansion"), "capex", None, 60),
    (_R(r"dividend"), "dividend", None, 34),
    (_R(r"record date"), "dividend", "record_date", 22),
    (_R(r"order|contract|letter of award|\bloa\b|bagging|bags|secures?|wins?\b"), "press_release", "order", 48),
    (_R(r"clarification|spurt in (price|volume)|price movement|movement in (price|volume)"), "press_release", "clarification", 55),
    (_R(r"press release|media release|launch|partnership|collaborat|tie-?up|alliance|approval from|usfda|patent"), "press_release", None, 42),
]

_RESULTS_RE = _R(
    r"financial results?|(un)?audited (standalone|consolidated)?\s*(and|&)?\s*(standalone|consolidated)?\s*(financial )?results|"
    r"results? (for|of) (the )?(quarter|half|year|period|nine months)|quarterly results|q[1-4] ?fy ?\d{2} results"
)
_RESULTS_DATE_RE = _R(r"(to consider|consider and approve|scheduled on|board meeting (will be )?(held|scheduled)|intimation of board meeting|prior intimation)")
_RESULTS_ANCILLARY_RE = _R(r"press release|investor presentation|earnings presentation|newspaper|publication|media release|shareholder letter|fact ?sheet")
_GENERIC_DETAIL_RE = _R(r"^(as enclosed|as attached|please find (the )?(attached|enclosed)|enclosed herewith|attached herewith|kindly find|copy of|intimation|disclosure|announcement)\W*$")

_TOP_ROLE_RE = _R(r"\b(ceo|chief executive|managing director|\bmd\b|cfo|chief financial|chairman|chairperson|whole[- ]?time director|executive director|joint managing|deputy managing|group president|chief operating|\bcoo\b)\b")
_INDEP_RE = _R(r"independent director")
_DIRECTOR_RE = _R(r"\bdirector\b")
_CS_RE = _R(r"company secretary|compliance officer")
_EXIT_RE = _R(r"resign|cessation|retire|demise|step(ping)? down|relinquish|cease")
_ENTRY_RE = _R(r"appoint|joins|elevat|re-?designat|nominat|take(s)? charge")


@dataclass
class Classification:
    category: str
    subtype: str | None
    base: int
    reason: str
    needs_model: bool = False


def _text(f: dict) -> str:
    return f"{f.get('subject', '')} || {f.get('detail', '')}"


def management_role(text: str) -> tuple[str, str]:
    """(role_bucket, direction) for a management-change filing."""
    t = text.lower()
    if re.search(r"statutory auditor|\bauditors?\b", t) and _EXIT_RE.search(t):
        role = "auditor"
    elif _CS_RE.search(t) and not _TOP_ROLE_RE.search(t):
        role = "company_secretary"
    elif _TOP_ROLE_RE.search(t):
        role = "top"
    elif _INDEP_RE.search(t):
        role = "independent_director"
    elif _DIRECTOR_RE.search(t):
        role = "director"
    else:
        role = "other"
    direction = "exit" if _EXIT_RE.search(t) and not _ENTRY_RE.search(t) else ("entry" if _ENTRY_RE.search(t) else "change")
    if _EXIT_RE.search(t) and _ENTRY_RE.search(t):
        direction = "change"
    return role, direction


MGMT_SCORES = {"top": 84, "auditor": 72, "director": 52, "independent_director": 42, "company_secretary": 22, "other": 40}


def classify(f: dict) -> Classification:
    sub = (f.get("exchange_subcategory") or "").strip().lower()
    cat = (f.get("exchange_category") or "").strip().lower()
    text = _text(f)
    low = text.lower()

    # 1. Results are special: the same words appear in intimations, ads, decks and the filing itself.
    if sub == "financial results" or (cat == "result" and sub in ("", "financial results")):
        if _RESULTS_ANCILLARY_RE.search(f.get("subject", "")):
            return Classification("results", "ancillary", 30, "results-related publication")
        return Classification("results", "filing", 92, "results filing (exchange category)")
    if sub in ("board meeting", "board meeting rescheduled") or (
        cat.startswith("board meeting") and "outcome" not in cat and sub != "outcome of board meeting"
    ):
        if _RESULTS_RE.search(low) or re.search(r"result|financial statement|financials|quarter|half[- ]year|year ended", low):
            return Classification("results", "date", 34, "board meeting to approve results")
        if _RESULTS_DATE_RE.search(low) and re.search(r"dividend|bonus|split|buy-?back|fund|qip|preferential|rights", low):
            kw = classify_text(low)
            if kw:
                return Classification(kw.category, "board_intimation", max(20, kw.base - 25), "board meeting will consider " + CATEGORIES[kw.category].lower())
        return Classification("unclassified", "board_meeting", 15, "board meeting intimation")
    if sub == "outcome of board meeting" or "outcome of board meeting" in low or "board meeting outcome" in low:
        if _RESULTS_RE.search(low):
            return Classification("results", "outcome", 80, "board approved results")
        kw = classify_text(low)
        if kw:
            return Classification(kw.category, kw.subtype, kw.base, "board outcome: " + kw.reason)
        # During results season a bare "outcome" is often the results themselves; read the filing.
        return Classification("unclassified", "board_outcome", 45, "board meeting outcome (contents in filing)", needs_model=True)

    # 2. Exchange sub-category mapping.
    if sub in SUBCATEGORY_MAP:
        category, subtype, base = SUBCATEGORY_MAP[sub]
        if category == "results" and subtype == "deck" and not _RESULTS_RE.search(low) and "quarter" not in low:
            return Classification("investor_meetings", "deck", 20, "investor presentation")
        if sub == "newspaper publication" and _RESULTS_RE.search(low):
            return Classification("results", "ancillary", 6, "results published in newspapers")
        if category == "press_release" and subtype is None and _RESULTS_RE.search(low):
            return Classification("results", "ancillary", 35, "results press release")
        if category == "management_change" and subtype is None:
            role, _ = management_role(text)
            if role == "other":
                # The exchange says "change in management" but the text doesn't say who: read the filing.
                return Classification(category, role, base, "management change (role in filing)", needs_model=True)
            return Classification(category, role, MGMT_SCORES[role], f"management change ({role.replace('_', ' ')})")
        if sub == "analyst / investor meet" and re.search(r"earnings|conference call|con-?call|results", low):
            return Classification("earnings_call", "notice", 18, "earnings call scheduled")
        return Classification(category, subtype, base, f"exchange tag: {f.get('exchange_subcategory')}")

    # 2b. NSE descriptions.
    if not sub and cat in NSE_MGMT_DESCS:
        role, _ = management_role(f.get("detail") or "")
        if role == "other" or re.search(r"director/kmp/smp|about (resignation|appointment|cessation|change in management)\s*$", low):
            return Classification("management_change", "other", 60, "management change (role in filing)", needs_model=True)
        return Classification("management_change", role, MGMT_SCORES[role], f"management change ({role.replace('_', ' ')})")
    if not sub and cat in NSE_DESC_MAP:
        category, subtype, base = NSE_DESC_MAP[cat]
        if category == "results" and subtype == "deck" and not _RESULTS_RE.search(low) and "quarter" not in low:
            return Classification("investor_meetings", "deck", 20, "investor presentation")
        if subtype == "allotment" and re.search(r"qualified institution|\bqip\b|preferential|rights", low):
            return Classification("fund_raise", None, 60, "allotment completing a fund raise")
        return Classification(category, subtype, base, f"exchange tag: {f.get('exchange_category')}")

    # 3. Insider / SAST bucket on BSE.
    if cat.startswith("insider trading") or "sast" in sub:
        if re.search(r"31 ?\(1\)|31\(2\)|encumbrance|pledg", low):
            return Classification("pledging", None, 50, "promoter encumbrance disclosure")
        if "trading window" in low:
            return Classification("unclassified", None, 3, "trading window")
        return Classification("insider_trading", None, 40, "insider / substantial holder disclosure")
    if cat in ("corp. action", "corp action"):
        kw = classify_text(low)
        if kw:
            return kw
        return Classification("dividend", None, 30, "corporate action")
    if cat == "agm/egm":
        return Classification("agm_egm", None, 14, "shareholder meeting")

    # 4. Keyword rules on free text (BSE "General"/"Others", NSE descriptions).
    if _RESULTS_RE.search(low) and not _RESULTS_ANCILLARY_RE.search(low) and not _RESULTS_DATE_RE.search(low):
        return Classification("results", "filing", 85, "results (text)")
    if _RESULTS_RE.search(low):
        if _RESULTS_DATE_RE.search(low):
            return Classification("results", "date", 34, "results date (text)")
        return Classification("results", "ancillary", 30, "results-related publication")
    kw = classify_text(low)
    if kw:
        if kw.category == "management_change":
            role, _ = management_role(text)
            return Classification("management_change", role, MGMT_SCORES[role], f"management change ({role.replace('_', ' ')})")
        return kw
    return Classification("unclassified", None, 18, "unrecognised filing", needs_model=True)


def classify_text(low: str) -> Classification | None:
    for pat, category, subtype, base in KEYWORD_RULES:
        if pat.search(low):
            return Classification(category, subtype, base, f"keyword: {pat.pattern[:40]}")
    return None


# ------------------------------------------------------------------ amounts & modifiers

_AMOUNT_RE = re.compile(
    r"(?:(rs\.?|inr|₹|usd|us\$|\$|eur|€)\s*)([\d,]+(?:\.\d+)?)\s*(crores?|cr\b\.?|lakhs?|lacs?|lakh|million|mn\b|billion|bn\b|thousand)?",
    re.I,
)
USD_INR = 88.0
EUR_INR = 102.0


def amounts_in_crore(text: str) -> list[float]:
    """Monetary amounts mentioned in a filing, converted to INR crore (approximate for FX)."""
    out = []
    for cur, num, unit in _AMOUNT_RE.findall(text or ""):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        if v <= 0:
            continue
        cur = cur.lower()
        unit = (unit or "").lower().rstrip(".")
        fx = USD_INR if cur in ("usd", "us$", "$") else EUR_INR if cur in ("eur", "€") else 1.0
        if unit.startswith("cr"):
            crore = v
        elif unit.startswith(("lakh", "lac")):
            crore = v / 100
        elif unit in ("million", "mn"):
            crore = v * 1e6 / 1e7
        elif unit in ("billion", "bn"):
            crore = v * 1e9 / 1e7
        elif unit == "thousand":
            crore = v * 1e3 / 1e7
        else:
            if fx == 1.0 and v < 1e5:
                continue  # "Rs. 10 per share", "Rs 4" etc. are not deal sizes
            crore = v / 1e7
        out.append(round(crore * fx, 2))
    return out


def rating_direction(low: str) -> str | None:
    if re.search(r"downgrad|revised (to|from).*negative|outlook.*negative|placed on (rating )?watch with negative", low):
        return "downgrade"
    if re.search(r"upgrad|outlook.*positive|revised.*positive", low):
        return "upgrade"
    if re.search(r"reaffirm|re-?affirm|retain|assigned|continues", low):
        return "reaffirm"
    if re.search(r"withdraw", low):
        return "withdrawn"
    return None


def materiality(f: dict, c: Classification, market_cap_cr: float | None = None) -> tuple[int, list[str]]:
    """Final 0-100 materiality score and the human-readable reasons behind it."""
    score = c.base
    why = [c.reason]
    low = _text(f).lower()
    if f.get("critical"):
        score += 4
        why.append("exchange flagged as critical")
    if c.category in ("m_and_a", "fund_raise", "press_release", "litigation", "capex") and market_cap_cr:
        amts = amounts_in_crore(_text(f))
        if amts:
            biggest = max(amts)
            pct = biggest / market_cap_cr * 100
            f["amount_cr"] = biggest
            f["amount_pct_mcap"] = round(pct, 2)
            if pct >= 5:
                score += 22
            elif pct >= 2:
                score += 14
            elif pct >= 0.5:
                score += 6
            elif pct < 0.05:
                score -= 12
            why.append(f"₹{biggest:,.0f} cr ≈ {pct:.2f}% of market cap")
    if c.category == "credit_rating":
        d = rating_direction(low)
        if d == "downgrade":
            score += 25
            why.append("rating downgrade")
        elif d == "upgrade":
            score += 12
            why.append("rating upgrade")
        elif d == "reaffirm":
            score -= 22
            why.append("rating reaffirmed")
    if c.category == "management_change" and c.subtype == "top" and re.search(r"resign|step(ping)? down", low):
        score += 6
        why.append("senior exit")
    if c.subtype == "auditor_exit" or (c.category == "management_change" and c.subtype == "auditor"):
        why.append("mid-term auditor exits are a governance red flag")
    return max(0, min(100, score)), why


def tier(score: int) -> str:
    if score >= TIER_HIGH:
        return "high"
    if score >= TIER_NOTABLE:
        return "notable"
    return "routine"


def informative_detail(detail: str) -> bool:
    d = (detail or "").strip()
    return len(d) >= 30 and not _GENERIC_DETAIL_RE.match(d)
