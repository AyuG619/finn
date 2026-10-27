"""Classifier tests built from real BSE filings captured on 26 Oct 2026 (subjects/headlines verbatim)."""
import pytest

from app.push.classify import amounts_in_crore, classify, materiality, tier
from app.sources.bse import clean_subject

CASES = [
    # (exchange category, sub-category, subject, detail, critical, mcap_cr, expected category, expected tier)
    ("Result", "Financial Results", "Financial Results Of The Bank For The Quarter And Half Year Ended 30.09.2026",
     "Financial results of the Bank for the Quarter and half year ended 30.09.2026", False, None, "results", "high"),
    ("Result", "Financial Results", "Outcome Of Board Meeting Held On Monday, 26Th October, 2026.",
     "Consideration and approval of Un-audited financials for half year ended 30th September, 2026.", False, None, "results", "high"),
    ("Board Meeting", "Board Meeting", "Board Meeting Intimation for Considering And Approving Unaudited Financial Statements For The Quarter And Half Year E",
     "Saksoft Ltdhas informed BSE that the meeting of the Board of Directors of the Company is scheduled on 04/11/2026 ,inter alia, to consider and approve Letter attached", True, None, "results", "routine"),
    ("Board Meeting", "Outcome of Board Meeting", "Board Meeting Outcome for Outcome Of The Board Meeting Held On Monday, October 26, 2026",
     "Outcome of the Board Meeting held on Monday, October 26, 2026", False, None, "unclassified", "notable"),
    ("Company Update", "Change in Management", "Change in Management", "As enclosed", False, None, "management_change", "notable"),
    ("Company Update", "Change in Management", "Change in Management",
     "The Board of Directors at the Board Meeting held on Monday, October 26, 2026 has appointed CS Shraddha Pachare as a Cheif Financial Officer (CFO) of the company with effect from Oc", False, None, "management_change", "high"),
    ("Company Update", "Resignation of Chief Financial Officer (CFO)", "Resignation of Chief Financial Officer (CFO)",
     "Pursuant to the subject, we hereby inform that Mrs. Rupali Ghosh has resigned from the post of Chief Financial Officer (CFO). The same has been accepted by the Board with effect fr", False, None, "management_change", "high"),
    ("Company Update", "Appointment of Company Secretary / Compliance Officer", "Appointment of Company Secretary and Compliance Officer",
     "Appointment of Mrs. Sarita Singh as the Company Secretary and Compliance Officer of the Company w.e.f. October 27, 2026", False, None, "management_change", "routine"),
    ("Company Update", "Change in Directorate", "Change in Directorate", "Appointment of an Independent Director", False, None, "management_change", "notable"),
    ("Company Update", "Credit Rating", "Credit Rating", "Intimation of Credit Rating assigned by ICRA Limited", True, None, "credit_rating", "routine"),
    ("Company Update", "Credit Rating", "Credit Rating", "ICRA has downgraded the long-term rating to [ICRA]A from [ICRA]A+", True, None, "credit_rating", "high"),
    ("Company Update", "Acquisition", "Acquisition",
     "Intimation of execution of Business Transfer Agreement for acquisition of business undertaking by wholly owned subsidaries of the Company.", True, None, "m_and_a", "high"),
    ("Company Update", "Award of Order / Receipt of Order", "Award Of Order Receipt Of Order",
     "We hereby inform the Exchange of the receipt of Purchase Order of Rs. 1234.10 Lakhs by the Subsidiary Company, M/s N S Engineering Projects Pvt Ltd.", True, 500, "press_release", "notable"),
    ("Insider Trading / SAST", "Closure of Trading Window", "Closure of Trading Window", "Intimation of Closure of Trading Window", False, None, "unclassified", "routine"),
    ("Insider Trading / SAST", "Disclosures under Reg. 31(1) and 31(2) of SEBI (SAST) Regulations, 2011",
     "Disclosures of reasons for encumbrance by promoter of listed companies under Reg. 31(1) read with Regulation 2",
     "The Exchange has received the Disclosures of reasons for encumbrance by promoter of listed companies", False, None, "pledging", "notable"),
    ("Insider Trading / SAST", "Disclosures under Reg. 29(2) of SEBI (SAST) Regulations, 2011", "Disclosures under Reg. 29(2) of SEBI (SAST) Regulations, 2011",
     "The Exchange has received the disclosure under Regulation 29(2) of SEBI (Substantial Acquisition of Shares & Takeovers) Regulations, 2011 for Krishan Khadaria", False, None, "insider_trading", "notable"),
    ("Company Update", "General", "Grant Of Options Under Employee Stock Option Scheme 2026", "Grant of options under Employee Stock Option Scheme 2026.", False, None, "unclassified", "routine"),
    ("Corp. Action", "Dividend", "Corporate Action-Board approves Dividend",
     "The Board Declared interim Dividend for FY''27@200% i.e. Rs 4 per Equity Share. The Record date for the purpose is 30th October 2026.", True, None, "dividend", "notable"),
    ("Corp. Action", "Record Date", "Record Date For 2Nd Interim Dividend For The Financial Year 2026-27.", "Record Date of 2nd Interim Dividend for the financial year 2026-27.", False, None, "dividend", "routine"),
    ("Company Update", "Newspaper Publication", "Newspaper Publication",
     "Newspaper Publication of Unaudited Financial Results of Bharat Coking Coal Limited (Standalone) for the 2nd Quarter and Half Year ended 30th Sep, 2026.", False, None, "results", "routine"),
    ("Company Update", "Investor Presentation", "Investor Presentation", "Investor Presentation on Financial Results of the Company for Q2 & H1FY27", False, None, "results", "routine"),
    ("Company Update", "Earnings Call Transcript", "Earnings Call Transcript",
     "Transcript of Earnings Conference Call on results of the Company for the quarter and half year ended September 30, 2026", False, None, "earnings_call", "notable"),
    ("Company Update", "Analyst / Investor Meet", "Analyst / Investor Meet - Intimation", "Intimation of Investors Conference Call on Thursday, October 29, 2026.", False, None, "earnings_call", "routine"),
    ("Company Update", "Analyst / Investor Meet", "Analyst / Investor Meet - Intimation", "Intimation of Schedule of Analyst/Institutional Investor Meet", False, None, "investor_meetings", "routine"),
    ("Company Update", "Clarification", "Clarification sought from Kolte-Patil Developers Ltd",
     "The Exchange has sought clarification from Kolte-Patil Developers Ltd on October 26, 2026, with reference to Movement in Volume. The reply is awaited.", True, None, "press_release", "notable"),
    ("Company Update", "Updates - Corporate Insolvency Resolution Process  (CIRP)", "Updates - Corporate Insolvency Resolution Process (CIRP)",
     "Intimation with respect to Modification in Resolution Plan", False, None, "litigation", "high"),
    ("Company Update", "Resignation of Statutory Auditors", "Resignation of Statutory Auditors", "Intimation regarding resignation of Statutory Auditor", False, None, "management_change", "high"),
    ("Company Update", "Raising of Funds", "Raising of Funds",
     "Raising of Funds through the issuance of unsecured, non-convertible perpetual debentures for an amount not exceeding Rs.800 Crores on private placement basis.", False, 10000, "fund_raise", "high"),
    ("Company Update", "Press Release / Media Release", "Press Release / Media Release", "Press Release for Q2 financial results", False, None, "results", "routine"),
    ("Company Update", "Scheme of Arrangement", "Scheme of Arrangement",
     "Scheme of arrangement for amalgamation of Prolijune Lifescinces Private Limited (WOS) with Mankind Pharma Limited", False, None, "m_and_a", "notable"),
    ("Company Update", "Allotment of ESOP / ESPS", "Allotment of ESOP / ESPS", "Allotment of 84705 equity shares on October 26, 2026", False, None, "unclassified", "routine"),
    ("Company Update", "Certificate under Reg. 74 (5) of SEBI (DP) Regulations, 2018", "Certificate under Reg. 74 (5) of SEBI (DP) Regulations, 2018",
     "Certificate under Regulation 74(5) of the SEBI (Depository and Participants) Regulations, 2018 for the Quarter ended September 30, 2026.", False, None, "unclassified", "routine"),
    ("Corp Action", "Daily Buy Back of equity shares", "Daily Buy Back of equity shares", "We enclose herewith the daily report for the equity shares bought back on October 26, 2026", False, None, "fund_raise", "routine"),
    # NSE-style free-text categories (no sub-category)
    ("Credit Rating", "", "Credit Rating", "Reliance Industries Limited has informed the Exchange about Credit Rating", False, None, "credit_rating", "notable"),
    ("Outcome of Board Meeting", "", "Outcome of Board Meeting", "Infosys Limited has informed the Exchange regarding Board meeting held on October 23, 2026 for approval of financial results for the quarter ended September 30, 2026", False, None, "results", "high"),
    ("Change in Directors/ Key Managerial Personnel/ Auditor/ Compliance Officer/ Share Transfer Agent", "",
     "Change in Directors/ Key Managerial Personnel/ Auditor/ Compliance Officer/ Share Transfer Agent",
     "XYZ Limited has informed the Exchange about resignation of Mr. A as Chief Executive Officer", False, None, "management_change", "high"),
]


@pytest.mark.parametrize("cat,sub,subject,detail,critical,mcap,exp_cat,exp_tier", CASES)
def test_real_filings(cat, sub, subject, detail, critical, mcap, exp_cat, exp_tier):
    f = {"exchange_category": cat, "exchange_subcategory": sub, "subject": subject, "detail": detail, "critical": critical}
    c = classify(f)
    score, why = materiality(f, c, mcap)
    assert c.category == exp_cat, (c, why)
    assert tier(score) == exp_tier, (c, score, why)


def test_amounts():
    assert amounts_in_crore("order of Rs. 1234.10 Lakhs") == [12.34]
    assert amounts_in_crore("not exceeding Rs.800 Crores") == [800.0]
    assert amounts_in_crore("USD 120 million") == [1056.0]
    assert amounts_in_crore("dividend of Rs 4 per share") == []
    assert amounts_in_crore("₹3.16 crore rate contract") == [3.16]


def test_clean_subject():
    assert clean_subject("Persistent Systems Ltd - 533179 - Announcement under Regulation 30 (LODR)-Allotment") == "Allotment"
    assert clean_subject("Announcement under Regulation 30 (LODR)-Credit Rating") == "Credit Rating"
    assert clean_subject("Initial Public Offer Of Jio Platforms Limited - Update") == "Initial Public Offer Of Jio Platforms Limited - Update"
    assert clean_subject("Bajel Projects Ltd - 544042 - Announcement under Regulation 30 (LODR)-Award_of_Order_Receipt_of_Order") == "Award of Order Receipt of Order"
