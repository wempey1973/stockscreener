"""
Capital-need scoring for equity-raise candidates.

Takes the list of companies that survive screener.py (market cap + price
appreciation + no recent offering) and ranks them by how plausible it is
that they raise equity capital soon.

Score is 0-100, built from five weighted components. Higher = more likely
candidate for an equity raise pitch.

    30%  Cash runway        - shorter runway (if burning cash) = more urgent
    20%  Leverage           - higher net debt/EBITDA = harder to raise more
                               debt, more pressure toward equity
    15%  Capex intensity    - capital-hungry businesses raise more often
    20%  Sector base rate   - some sectors raise routinely regardless of
                               financial distress (biotech, mining, etc.)
    15%  Active unused shelf - an effective S-3 with no 424B takedown since
                               is a strong "primed and waiting" signal
"""

import logging
import math
import time
from datetime import datetime

import yfinance as yf

import screener

log = logging.getLogger(__name__)

YFINANCE_REQUEST_DELAY = 0.3

# Weights must sum to 1.0
WEIGHTS = {
    "cash_runway": 0.30,
    "leverage": 0.20,
    "capex_intensity": 0.15,
    "sector_base_rate": 0.20,
    "active_shelf": 0.15,
}

# Verified against live yfinance .info responses for real screener
# candidates (biotech/pharma/oil-services names matched these keys
# exactly). Anything not listed falls to DEFAULT_SECTOR_BASE_RATE.
SECTOR_BASE_RATE = {
    "Biotechnology": 0.95,
    "Drug Manufacturers - Specialty & Generic": 0.7,
    "Drug Manufacturers - General": 0.7,
    "Medical Devices": 0.45,
    "Medical Instruments & Supplies": 0.45,
    "Diagnostics & Research": 0.55,
    "Gold": 0.65,
    "Silver": 0.65,
    "Other Precious Metals & Mining": 0.65,
    "Oil & Gas E&P": 0.55,
    "Oil & Gas Equipment & Services": 0.45,
    "Semiconductors": 0.35,
    "Software - Application": 0.3,
    "Software - Infrastructure": 0.3,
}
DEFAULT_SECTOR_BASE_RATE = 0.25  # mature/asset-light industrials, retail, etc.

SHELF_LOOKBACK_YEARS = 3  # S-3 shelves are typically effective ~3 years
SHELF_FORM_TYPES = {"S-3", "S-3/A"}
TAKEDOWN_FORM_TYPES = {
    "424B1", "424B2", "424B3", "424B4", "424B5", "424B7", "424B8",
}


def _is_missing(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


# ---------------------------------------------------------------------------
# Fundamentals fetch
# ---------------------------------------------------------------------------

def get_fundamentals(symbol: str) -> dict | None:
    """Pull the pieces needed for scoring via yfinance."""
    try:
        t = yf.Ticker(symbol)
        info = t.info
        if not info:
            return None

        fcf = info.get("freeCashflow")
        ocf = info.get("operatingCashflow")

        capex = None
        try:
            cf = t.cashflow
            if cf is not None and "Capital Expenditure" in cf.index:
                val = cf.loc["Capital Expenditure"].iloc[0]
                if not _is_missing(val):
                    capex = float(val)
        except Exception as exc:
            log.warning("capex fetch failed for %s: %s", symbol, exc)

        # .info['freeCashflow'] has occasionally been observed disagreeing in
        # sign with operatingCashflow - |capex| (e.g. MGNX: freeCashflow
        # +$10.1M vs operatingCashflow -$70.0M with only -$1.9M capex, which
        # is not mathematically consistent). When both pieces are available
        # and they disagree in sign, trust the recomputed value.
        if not _is_missing(ocf) and not _is_missing(capex):
            recomputed_fcf = ocf - abs(capex)
            if not _is_missing(fcf) and (fcf >= 0) != (recomputed_fcf >= 0):
                log.warning(
                    "%s: .info freeCashflow (%.1fM) disagrees in sign with "
                    "operatingCashflow - |capex| (%.1fM); using recomputed value",
                    symbol, fcf / 1e6, recomputed_fcf / 1e6,
                )
                fcf = recomputed_fcf
            elif _is_missing(fcf):
                fcf = recomputed_fcf

        return {
            "free_cash_flow": fcf,
            "operating_cash_flow": ocf,
            "dividend_rate": info.get("dividendRate"),
            "capex": capex,
            "cash": info.get("totalCash"),
            "total_debt": info.get("totalDebt"),
            "revenue": info.get("totalRevenue"),
            "ebitda": info.get("ebitda"),
            "industry": info.get("industry"),
            "sector": info.get("sector"),
        }
    except Exception as e:
        log.warning("fundamentals fetch failed for %s: %s", symbol, e)
        return None


# ---------------------------------------------------------------------------
# Component scores (each returns 0-1)
# ---------------------------------------------------------------------------

def score_cash_runway(f: dict) -> float:
    """
    Shorter runway = higher score (more urgent need). Profitable companies
    (positive FCF) score low here -- they aren't burning cash.
    """
    fcf = f.get("free_cash_flow")
    cash = f.get("cash")
    if _is_missing(fcf) or _is_missing(cash):
        return 0.3  # neutral default when data is missing

    if fcf >= 0:
        return 0.1  # not burning cash, low urgency on this dimension

    annual_burn = abs(fcf)
    runway_months = (cash / annual_burn) * 12 if annual_burn else 999

    if runway_months < 6:
        return 1.0
    elif runway_months < 12:
        return 0.85
    elif runway_months < 18:
        return 0.6
    elif runway_months < 24:
        return 0.35
    else:
        return 0.1


def score_leverage(f: dict) -> float:
    """
    Higher net debt/EBITDA = harder to raise more debt cheaply = more
    pressure toward equity. Negative/near-zero EBITDA gets treated as
    high-leverage-risk by default rather than divide-by-zero.
    """
    debt = f.get("total_debt")
    cash = f.get("cash")
    ebitda = f.get("ebitda")
    if _is_missing(debt) or _is_missing(cash) or _is_missing(ebitda):
        return 0.3

    net_debt = debt - cash
    if ebitda <= 0:
        return 0.7 if net_debt > 0 else 0.2

    ratio = net_debt / ebitda
    if ratio > 5:
        return 1.0
    elif ratio > 3:
        return 0.7
    elif ratio > 1.5:
        return 0.4
    else:
        return 0.15


def score_capex_intensity(f: dict) -> float:
    revenue = f.get("revenue")
    capex = f.get("capex")
    if not revenue or _is_missing(capex):
        return 0.3

    intensity = abs(capex) / revenue
    if intensity > 0.25:
        return 1.0
    elif intensity > 0.15:
        return 0.7
    elif intensity > 0.08:
        return 0.4
    else:
        return 0.15


def score_sector_base_rate(industry: str) -> float:
    return SECTOR_BASE_RATE.get(industry, DEFAULT_SECTOR_BASE_RATE)


# Hard override for capital-intensive-but-profitable businesses (E&P,
# financials, utilities, etc.) that the capex/leverage components -- built
# around cash-burning biotechs -- otherwise penalize. Gated on operating
# cash flow rather than free cash flow: capex-heavy names (e.g. E&P
# drilling spend) can be strongly cash-generative from operations while
# still showing negative FCF once capex is subtracted, and a sustained
# dividend from an op-cash-flow-positive business is itself evidence
# against an imminent equity raise.
PROFITABILITY_OVERRIDE_CAP = 25.0


def is_profitable_dividend_payer(f: dict) -> bool:
    ocf = f.get("operating_cash_flow")
    dividend_rate = f.get("dividend_rate")
    return not _is_missing(ocf) and ocf > 0 and not _is_missing(dividend_rate) and dividend_rate > 0


# ---------------------------------------------------------------------------
# Active unused shelf check
# ---------------------------------------------------------------------------
# Originally designed around EDGAR's full-text search API, but that API
# doesn't support comma/repeated-param OR'ing of multiple form types (each
# distinct call only matches one exact form value), which made the S-3 vs.
# S-3/A and multi-variant 424B checks silently return zero hits. Rebuilt on
# the SEC submissions API instead -- the same source screener.py's
# no-recent-offering check already uses, so `submissions` here is the
# payload screener.py already fetched per surviving candidate (no extra
# per-company SEC round-trip).

def has_active_unused_shelf(submissions: dict) -> bool:
    """
    True if the company has an effective S-3 filed within the lookback
    window with no subsequent 424B takedown -- i.e. registered to raise
    but hasn't pulled the trigger yet.

    This is a heuristic, not a certainty: an S-3 can also register
    secondary sales by existing holders (resale shelf) rather than primary
    capital raising. Distinguishing that would require inspecting each
    filing's "Calculation of Registration Fee" table, which isn't done here.
    """
    if submissions is None:
        return False
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    cutoff = screener.months_ago(SHELF_LOOKBACK_YEARS * 12)

    latest_shelf_date = None
    for form, date_str in zip(forms, dates):
        if form.upper() in SHELF_FORM_TYPES:
            filing_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if filing_date >= cutoff:
                if latest_shelf_date is None or filing_date > latest_shelf_date:
                    latest_shelf_date = filing_date

    if latest_shelf_date is None:
        return False

    for form, date_str in zip(forms, dates):
        if form.upper() in TAKEDOWN_FORM_TYPES:
            filing_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if filing_date >= latest_shelf_date:
                return False

    return True


# ---------------------------------------------------------------------------
# Combine into final score
# ---------------------------------------------------------------------------

def score_company(row: dict) -> dict:
    """
    row must have: symbol, cik, and (if available) _submissions -- the raw
    SEC submissions payload screener.py already fetched for the
    no-recent-offering check, reused here for the shelf check.
    """
    symbol = row["symbol"]
    f = get_fundamentals(symbol)

    if f is None:
        row["capital_need_score"] = None
        row["score_note"] = "insufficient fundamentals data"
        return row

    c_runway = score_cash_runway(f)
    c_leverage = score_leverage(f)
    c_capex = score_capex_intensity(f)
    c_sector = score_sector_base_rate(f.get("industry") or row.get("industry", ""))

    try:
        c_shelf = 1.0 if has_active_unused_shelf(row.get("_submissions")) else 0.0
    except Exception as e:
        log.warning("shelf check failed for %s: %s", symbol, e)
        c_shelf = 0.0

    total = (
        WEIGHTS["cash_runway"] * c_runway
        + WEIGHTS["leverage"] * c_leverage
        + WEIGHTS["capex_intensity"] * c_capex
        + WEIGHTS["sector_base_rate"] * c_sector
        + WEIGHTS["active_shelf"] * c_shelf
    )
    score = round(total * 100, 1)

    profitable_dividend_payer = is_profitable_dividend_payer(f)
    if profitable_dividend_payer and score > PROFITABILITY_OVERRIDE_CAP:
        score = PROFITABILITY_OVERRIDE_CAP

    row.update({
        "capital_need_score": score,
        "cash_runway_component": round(c_runway, 2),
        "leverage_component": round(c_leverage, 2),
        "capex_component": round(c_capex, 2),
        "sector_component": round(c_sector, 2),
        "active_unused_shelf": bool(c_shelf),
        "profitability_override": profitable_dividend_payer,
    })
    return row


def score_all(rows: list[dict]) -> list[dict]:
    print(f"Step 3: scoring {len(rows)} candidates for capital-need likelihood...")
    scored = []
    for i, row in enumerate(rows, 1):
        scored.append(score_company(row))
        symbol = row["symbol"]
        score = row.get("capital_need_score")
        label = f"{score}" if score is not None else "N/A"
        print(f"  [{i}/{len(rows)}] {symbol}: capital_need_score={label}")
        time.sleep(YFINANCE_REQUEST_DELAY)
    scored.sort(key=lambda r: (r["capital_need_score"] is not None, r["capital_need_score"]), reverse=True)
    return scored
