"""
Two parallel screens over the same US market-cap universe ($100M-$3B,
via NASDAQ's free public screener), each narrowed by SEC EDGAR to exclude
companies that already filed a primary stock offering (S-1/F-1, a priced
424B prospectus, or a Form D private placement):

  1. Up-screen: appreciated more than 35% over the trailing year (1-year
     return from yfinance), no offering in the past 15 months. Opportunistic
     equity-raise candidates -- stock strength makes a raise less dilutive.
  2. Distress-screen: declined more than 25% over the trailing year, no
     offering in the past 6 months (shorter lookback -- distress situations
     move faster, and a 15-month-old raise says little about current need).
     Companies that plausibly need capital despite unfavorable pricing.

Requires env var SEC_USER_AGENT (SEC requires a descriptive User-Agent
like "Name contact@email.com" on every request).
"""

import csv
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
import yfinance as yf

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT")

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks?download=true"
NASDAQ_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json",
}

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

MARKET_CAP_MIN = 100_000_000
MARKET_CAP_MAX = 3_000_000_000
MIN_1Y_RETURN_PCT = 35.0
MAX_1Y_DECLINE_PCT = -25.0
OFFERING_LOOKBACK_MONTHS = 15
DISTRESS_OFFERING_LOOKBACK_MONTHS = 6

# Filings that reliably indicate an actual (or IPO-style) primary offering,
# as opposed to an S-3 shelf registration (which only reserves capacity and
# does not by itself mean shares were sold).
OFFERING_FORM_TYPES = {
    "S-1", "S-1/A", "F-1", "F-1/A",
    "424B1", "424B2", "424B4", "424B5", "424B7", "424B8",
    "D", "D/A",  # Regulation D private placements (e.g. PIPE deals) --
                 # a primary capital raise, just exempt from full registration.
}

# 424B3 is overloaded: SEC also uses it for merger/reorg share-issuance
# prospectuses, dividend-reinvestment plan updates, and resale-only
# prospectuses, none of which are the company doing a primary offering.
# These get classified by inspecting the actual filing text instead of
# trusting the form type alone.
AMBIGUOUS_FORM_TYPES = {"424B3"}

NON_OFFERING_TEXT_SIGNALS = (
    "agreement and plan of merger",
    "plan of merger",
    "proxy statement/prospectus",
    "special meeting of shareholders",
    "special meeting of stockholders",
    "dividend reinvestment and stock purchase plan",
    "dividend reinvestment plan",
)
OFFERING_TEXT_SIGNALS = (
    "we are offering",
    "public offering price",
    "underwriting discount",
    "net proceeds to us",
)

# Skip non-common-stock listings (warrants, units, preferreds, notes, etc.)
EXCLUDED_NAME_KEYWORDS = ("Warrant", "Unit", "Right", "Preferred", " Note", "Depositary")

# Skip banks/thrifts (e.g. NASDAQ industries "Major Banks", "Commercial Banks",
# "Banks", "Savings Institutions") and REITs ("Real Estate Investment Trusts").
EXCLUDED_INDUSTRY_KEYWORDS = ("bank", "savings institution", "real estate investment trust")

# Bank holding companies that NASDAQ misclassifies outside any "bank"-labeled
# industry (e.g. Plumas Bancorp shows up under industry "Finance Companies").
EXCLUDED_NAME_BANK_KEYWORDS = ("bancorp", "bancshares", "bancorporation")

# Closed-end funds/trusts: gated on sector == Finance so real operating
# companies that happen to use fund/trust naming or "Shares of Beneficial
# Interest" (e.g. Compass Diversified Holdings, an operating holding
# company) aren't caught -- those get classified under their own business
# sector, not Finance.
CLOSED_END_FUND_NAME_KEYWORDS = ("fund", "trust", "beneficial interest")

# These NASDAQ industries are dominated by closed-end funds and BDCs
# (business development companies -- publicly traded pooled investment
# vehicles) but also contain a handful of genuine operating companies
# (asset managers, specialty finance operators) that got swept into the
# same bucket. Exclude the industry, then explicitly allow back in the
# names verified to be real operating businesses rather than funds.
AMBIGUOUS_FINANCE_INDUSTRIES = {
    "Investment Managers",
    "Finance/Investors Services",
    "Finance Companies",
    "Trusts Except Educational Religious and Charitable",
}
OPERATING_COMPANY_OVERRIDES = {
    "ABX",   # Abacus Global Management -- life-settlement asset manager, not a fund
    "ALTI",  # AlTi Global -- wealth management firm
    "JCAP",  # Jefferson Capital -- consumer debt recovery operator
    "RILY",  # BRC Group Holdings (fka B. Riley Financial) -- diversified financial services
    "RPC",   # Ridgepost Capital (fka P10) -- alternative asset manager
    "VALU",  # Value Line -- investment research publisher
    "VRTS",  # Virtus Investment Partners -- asset manager
    "WHG",   # Westwood Holdings Group -- asset manager
}

# Strips share-class qualifiers ("Class A", "Class B Common Stock", "Series
# C", ...) so multiple share classes of the same company normalize to the
# same base name for dedup.
SHARE_CLASS_RE = re.compile(
    r"\s*[-,]?\s*(class|series)\s+[a-z0-9]+\b.*$", re.IGNORECASE
)
STOCK_TYPE_SUFFIX_RE = re.compile(
    r"\s+(common|capital|ordinary)\s+(stock|shares).*$|\s+shares of beneficial interest.*$",
    re.IGNORECASE,
)


def normalize_company_name(name):
    base = SHARE_CLASS_RE.sub("", name)
    base = STOCK_TYPE_SUFFIX_RE.sub("", base)
    return base.strip().lower()

YFINANCE_CHUNK_SIZE = 150
YFINANCE_CHUNK_DELAY = 2.0
SEC_REQUEST_DELAY = 0.15

OUTPUT_FILE = "screener_results.csv"
DISTRESS_OUTPUT_FILE = "distress_results.csv"


def months_ago(months):
    now = datetime.now(timezone.utc)
    year = now.year
    month = now.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(now.day, 28)
    return datetime(year, month, day, tzinfo=timezone.utc).date()


def sec_get(url, retries=3):
    headers = {"User-Agent": SEC_USER_AGENT}
    for attempt in range(1, retries + 1):
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 429:
            time.sleep(2 * attempt)
            continue
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    return None


def get_candidate_universe():
    print("Step 1a: pulling US market-cap universe from NASDAQ screener...")
    resp = requests.get(NASDAQ_SCREENER_URL, headers=NASDAQ_HEADERS, timeout=30)
    resp.raise_for_status()
    rows = resp.json()["data"]["rows"]

    candidates = {}
    for row in rows:
        if row.get("country") != "United States":
            continue
        symbol = row["symbol"]
        name = row.get("name") or ""
        name_lower = name.lower()
        if any(kw in name for kw in EXCLUDED_NAME_KEYWORDS):
            continue
        if any(kw in name_lower for kw in EXCLUDED_NAME_BANK_KEYWORDS):
            continue
        industry = row.get("industry") or ""
        if any(kw in industry.lower() for kw in EXCLUDED_INDUSTRY_KEYWORDS):
            continue
        sector = row.get("sector") or ""
        if sector.strip().lower() == "finance" and any(
            kw in name_lower for kw in CLOSED_END_FUND_NAME_KEYWORDS
        ):
            continue
        if industry in AMBIGUOUS_FINANCE_INDUSTRIES and symbol not in OPERATING_COMPANY_OVERRIDES:
            continue
        market_cap_raw = row.get("marketCap")
        if not market_cap_raw:
            continue
        try:
            market_cap = float(market_cap_raw)
        except ValueError:
            continue
        if not (MARKET_CAP_MIN <= market_cap <= MARKET_CAP_MAX):
            continue

        base_name = normalize_company_name(name)
        existing = candidates.get(base_name)
        if existing is not None and existing["marketCap"] >= market_cap:
            continue
        candidates[base_name] = {
            "symbol": row["symbol"],
            "companyName": name,
            "sector": row.get("sector"),
            "industry": row.get("industry"),
            "marketCap": market_cap,
        }
    result = list(candidates.values())
    print(f"  found {len(result)} candidates in market cap range")
    return result


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def compute_returns(candidates):
    """One yfinance pass over the whole candidate universe, annotating each
    candidate dict with oneYearReturnPct. Feeds both the appreciation
    (up) and decline (distress) screens so price history is only fetched
    once per symbol regardless of how many direction filters run after it."""
    print(f"Step 1b: checking 1-year return for {len(candidates)} candidates via yfinance...")
    by_symbol = {c["symbol"]: c for c in candidates}
    symbols = list(by_symbol.keys())
    computed = []

    for chunk in chunked(symbols, YFINANCE_CHUNK_SIZE):
        try:
            data = yf.download(chunk, period="1y", group_by="ticker",
                                threads=True, progress=False, auto_adjust=True)
        except Exception as exc:
            print(f"  chunk download failed ({exc}), skipping {len(chunk)} symbols")
            continue

        for symbol in chunk:
            try:
                closes = data[symbol]["Close"].dropna()
            except (KeyError, TypeError):
                continue
            if len(closes) < 2:
                continue
            ret = (closes.iloc[-1] / closes.iloc[0] - 1) * 100
            c = by_symbol[symbol]
            c["oneYearReturnPct"] = round(float(ret), 2)
            computed.append(c)
        time.sleep(YFINANCE_CHUNK_DELAY)

    print(f"  computed 1-year return for {len(computed)} candidates")
    return computed


def filter_by_appreciation(candidates_with_returns):
    survivors = [c for c in candidates_with_returns if c["oneYearReturnPct"] > MIN_1Y_RETURN_PCT]
    for c in survivors:
        print(f"  {c['symbol']}: +{c['oneYearReturnPct']:.1f}% -> kept (up-screen)")
    print(f"  {len(survivors)} companies appreciated more than {MIN_1Y_RETURN_PCT}% over 1 year")
    return survivors


def filter_by_decline(candidates_with_returns):
    survivors = [c for c in candidates_with_returns if c["oneYearReturnPct"] < MAX_1Y_DECLINE_PCT]
    for c in survivors:
        print(f"  {c['symbol']}: {c['oneYearReturnPct']:.1f}% -> kept (distress-screen)")
    print(f"  {len(survivors)} companies declined more than {abs(MAX_1Y_DECLINE_PCT)}% over 1 year")
    return survivors


def load_ticker_to_cik_map():
    print("Step 2a: loading SEC ticker-to-CIK map...")
    resp = requests.get(SEC_TICKER_MAP_URL, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}


def classify_424b3(cik, accession_number, primary_document):
    accession_no_dashes = accession_number.replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{accession_no_dashes}/{primary_document}")
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    if resp.status_code != 200:
        return False
    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = re.sub(r"\s+", " ", text).lower()[:20000]
    if any(signal in text for signal in NON_OFFERING_TEXT_SIGNALS):
        return False
    return any(signal in text for signal in OFFERING_TEXT_SIGNALS)


def fetch_submissions(cik):
    return sec_get(SEC_SUBMISSIONS_URL.format(cik=cik))


def has_recent_offering(cik, submissions, cutoff_date):
    if submissions is None:
        return None
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    for form, date_str, accession, doc in zip(forms, dates, accessions, docs):
        filing_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        if filing_date < cutoff_date:
            continue
        form_upper = form.upper()
        if form_upper in OFFERING_FORM_TYPES:
            return True
        if form_upper in AMBIGUOUS_FORM_TYPES:
            time.sleep(SEC_REQUEST_DELAY)
            if classify_424b3(cik, accession, doc):
                return True
    return False


def filter_by_no_offering(survivors, ticker_map, lookback_months):
    cutoff = months_ago(lookback_months)
    print(f"Step 2b: excluding companies with an offering-related filing since {cutoff}...")
    final = []
    for i, c in enumerate(survivors, 1):
        symbol = c.get("symbol")
        cik = ticker_map.get(symbol.upper())
        if not cik:
            print(f"  [{i}/{len(survivors)}] {symbol}: no CIK found, skipping")
            continue
        submissions = fetch_submissions(cik)
        offered = has_recent_offering(cik, submissions, cutoff)
        time.sleep(SEC_REQUEST_DELAY)
        if offered is None:
            print(f"  [{i}/{len(survivors)}] {symbol}: SEC lookup failed, skipping")
            continue
        if not offered:
            c["cik"] = cik
            c["_submissions"] = submissions
            final.append(c)
            print(f"  [{i}/{len(survivors)}] {symbol}: no recent offering -> kept")
        else:
            print(f"  [{i}/{len(survivors)}] {symbol}: recent offering found -> excluded")
    return final


def write_results(rows, path):
    fieldnames = [
        "symbol", "companyName", "sector", "industry",
        "marketCap", "oneYearReturnPct", "cik",
        "capital_need_score", "cash_runway_component", "leverage_component",
        "capex_component", "sector_component", "active_unused_shelf",
        "profitability_override", "score_note",
    ]

    def sort_key(row):
        score = row.get("capital_need_score")
        has_score = score is not None
        return (has_score, score if has_score else 0)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=sort_key, reverse=True):
            writer.writerow(row)
    print(f"Wrote {len(rows)} results to {path}")


def main():
    if not SEC_USER_AGENT:
        sys.exit("SEC_USER_AGENT environment variable is required (e.g. 'Name contact@email.com')")

    import capital_need_scoring

    candidates = get_candidate_universe()
    with_returns = compute_returns(candidates)
    ticker_map = load_ticker_to_cik_map()

    print("=== Up-screen: appreciation >35%, no offering in 15 months ===")
    up_candidates = filter_by_appreciation(with_returns)
    up_final = filter_by_no_offering(up_candidates, ticker_map, OFFERING_LOOKBACK_MONTHS)
    up_scored = capital_need_scoring.score_all(up_final)
    write_results(up_scored, OUTPUT_FILE)

    print("=== Distress-screen: decline >25%, no offering in 6 months ===")
    down_candidates = filter_by_decline(with_returns)
    down_final = filter_by_no_offering(down_candidates, ticker_map, DISTRESS_OFFERING_LOOKBACK_MONTHS)
    down_scored = capital_need_scoring.score_all(down_final)
    write_results(down_scored, DISTRESS_OUTPUT_FILE)


if __name__ == "__main__":
    main()
