"""Ad-hoc mocked test: exercises capital_need_scoring.py's logic without hitting real APIs."""

import os
from unittest import mock

os.environ.setdefault("SEC_USER_AGENT", "Test test@example.com")

import capital_need_scoring  # noqa: E402
import screener  # noqa: E402


def _submissions(forms_dates):
    forms, dates = zip(*forms_dates) if forms_dates else ([], [])
    return {"filings": {"recent": {"form": list(forms), "filingDate": list(dates)}}}


class _FakeCashflow:
    class _Loc:
        def __init__(self, val):
            self.iloc = [val]

    def __init__(self, capex):
        self.index = ["Capital Expenditure"]
        self.loc = {"Capital Expenditure": self._Loc(capex)}


class _FakeTicker:
    def __init__(self, info, capex):
        self.info = info
        self.cashflow = _FakeCashflow(capex)


def test_get_fundamentals_fcf_ocf_cross_check():
    # MGNX-style bug: .info freeCashflow is positive but operatingCashflow
    # is strongly negative with small capex -- mathematically inconsistent.
    # Should prefer the recomputed value (OCF - |capex|).
    info = {
        "freeCashflow": 10_108_750,
        "operatingCashflow": -69_961_000,
        "totalCash": 154_228_992,
        "totalDebt": 0,
        "totalRevenue": 0,
        "ebitda": 0,
        "industry": "Biotechnology",
        "sector": "Healthcare",
        "dividendRate": None,
    }
    fake = _FakeTicker(info, capex=-1_914_000)
    with mock.patch.object(capital_need_scoring.yf, "Ticker", lambda s: fake):
        f = capital_need_scoring.get_fundamentals("MGNX")
    assert f["free_cash_flow"] < 0, f["free_cash_flow"]
    assert round(f["free_cash_flow"]) == -71875000

    # Consistent case: .info and recomputed value agree in sign -> keep .info's value
    info2 = dict(info, freeCashflow=-5_000_000, operatingCashflow=-3_000_000)
    fake2 = _FakeTicker(info2, capex=-1_000_000)
    with mock.patch.object(capital_need_scoring.yf, "Ticker", lambda s: fake2):
        f2 = capital_need_scoring.get_fundamentals("XYZ")
    assert f2["free_cash_flow"] == -5_000_000


def test_score_cash_runway():
    # burning cash, ~4 months of runway -> max urgency
    assert capital_need_scoring.score_cash_runway(
        {"free_cash_flow": -1_200_000, "cash": 400_000}
    ) == 1.0
    # profitable -> low urgency regardless of cash pile
    assert capital_need_scoring.score_cash_runway(
        {"free_cash_flow": 500_000, "cash": 100}
    ) == 0.1
    # missing data -> neutral default
    assert capital_need_scoring.score_cash_runway({"free_cash_flow": None, "cash": 100}) == 0.3
    import math
    assert capital_need_scoring.score_cash_runway({"free_cash_flow": float("nan"), "cash": 100}) == 0.3


def test_score_leverage():
    # heavily levered
    assert capital_need_scoring.score_leverage(
        {"total_debt": 6_000_000, "cash": 0, "ebitda": 1_000_000}
    ) == 1.0
    # net-cash position, negative EBITDA -> low risk
    assert capital_need_scoring.score_leverage(
        {"total_debt": 0, "cash": 1_000_000, "ebitda": -500_000}
    ) == 0.2


def test_score_capex_intensity():
    assert capital_need_scoring.score_capex_intensity(
        {"revenue": 1_000_000, "capex": -300_000}
    ) == 1.0
    assert capital_need_scoring.score_capex_intensity(
        {"revenue": 1_000_000, "capex": -20_000}
    ) == 0.15
    assert capital_need_scoring.score_capex_intensity({"revenue": 0, "capex": -20_000}) == 0.3


def test_score_sector_base_rate():
    assert capital_need_scoring.score_sector_base_rate("Biotechnology") == 0.95
    assert capital_need_scoring.score_sector_base_rate("Some Unknown Industry") == capital_need_scoring.DEFAULT_SECTOR_BASE_RATE


def test_has_active_unused_shelf():
    cutoff_ok = screener.months_ago(6).isoformat()  # well within 3yr lookback
    too_old = "2015-01-01"

    # S-3 filed recently, no takedown since -> active unused shelf
    subs_active = _submissions([("S-3", cutoff_ok), ("10-K", cutoff_ok)])
    assert capital_need_scoring.has_active_unused_shelf(subs_active) is True

    # S-3 filed recently, followed by a 424B5 takedown -> shelf already used
    subs_used = _submissions([("S-3", cutoff_ok), ("424B5", cutoff_ok)])
    assert capital_need_scoring.has_active_unused_shelf(subs_used) is False

    # only an old, expired S-3 -> no active shelf
    subs_expired = _submissions([("S-3", too_old)])
    assert capital_need_scoring.has_active_unused_shelf(subs_expired) is False

    # no S-3 at all
    subs_none = _submissions([("10-K", cutoff_ok)])
    assert capital_need_scoring.has_active_unused_shelf(subs_none) is False

    # no submissions data
    assert capital_need_scoring.has_active_unused_shelf(None) is False


def test_is_profitable_dividend_payer():
    assert capital_need_scoring.is_profitable_dividend_payer(
        {"operating_cash_flow": 140_000_000, "dividend_rate": 0.25}
    ) is True
    # negative FCF alone (e.g. capex-heavy E&P) shouldn't matter here --
    # only operating cash flow + dividend gate the override
    assert capital_need_scoring.is_profitable_dividend_payer(
        {"operating_cash_flow": 140_000_000, "dividend_rate": 0.25, "free_cash_flow": -76_000_000}
    ) is True
    # no dividend -> no override
    assert capital_need_scoring.is_profitable_dividend_payer(
        {"operating_cash_flow": 140_000_000, "dividend_rate": None}
    ) is False
    # dividend but operating-cash-flow negative -> no override
    assert capital_need_scoring.is_profitable_dividend_payer(
        {"operating_cash_flow": -5_000_000, "dividend_rate": 0.25}
    ) is False
    # missing data -> no override (conservative default)
    assert capital_need_scoring.is_profitable_dividend_payer({}) is False


def test_score_company_profitability_override():
    # Mirrors EGY: heavy capex/leverage would otherwise push the score
    # high, but it's operating-cash-flow-positive and pays a dividend.
    fake_fundamentals = {
        "free_cash_flow": -76_000_000,
        "operating_cash_flow": 140_000_000,
        "dividend_rate": 0.25,
        "capex": -200_000_000,
        "cash": 10_000_000,
        "total_debt": 300_000_000,
        "revenue": 150_000_000,
        "ebitda": 60_000_000,
        "industry": "Oil & Gas E&P",
        "sector": "Energy",
    }
    row = {"symbol": "EGYX", "cik": "0000000098", "_submissions": None}
    with mock.patch.object(capital_need_scoring, "get_fundamentals", lambda s: fake_fundamentals):
        scored = capital_need_scoring.score_company(row)

    assert scored["profitability_override"] is True
    assert scored["capital_need_score"] <= capital_need_scoring.PROFITABILITY_OVERRIDE_CAP
    # sanity check: without the override this profile would score well above the cap
    # (high leverage + high capex + E&P sector base rate, no shelf)
    assert scored["leverage_component"] >= 0.4
    assert scored["capex_component"] >= 0.7


def test_score_company_end_to_end():
    cutoff_ok = screener.months_ago(6).isoformat()
    fake_fundamentals = {
        "free_cash_flow": -2_000_000,
        "capex": -400_000,
        "cash": 1_000_000,
        "total_debt": 5_000_000,
        "revenue": 2_000_000,
        "ebitda": -1_000_000,
        "industry": "Biotechnology",
        "sector": "Healthcare",
    }
    row = {
        "symbol": "ZZZ",
        "cik": "0000000099",
        "_submissions": _submissions([("S-3", cutoff_ok)]),
    }
    with mock.patch.object(capital_need_scoring, "get_fundamentals", lambda s: fake_fundamentals):
        scored = capital_need_scoring.score_company(row)

    assert scored["capital_need_score"] is not None
    assert scored["active_unused_shelf"] is True
    assert scored["sector_component"] == 0.95  # Biotechnology base rate
    # cash burning, net-debt-positive, negative EBITDA, active shelf, biotech
    # -> should land in the upper half of the 0-100 scale
    assert scored["capital_need_score"] > 60


def main():
    test_get_fundamentals_fcf_ocf_cross_check()
    test_score_cash_runway()
    test_score_leverage()
    test_score_capex_intensity()
    test_score_sector_base_rate()
    test_has_active_unused_shelf()
    test_is_profitable_dividend_payer()
    test_score_company_profitability_override()
    test_score_company_end_to_end()
    print("ALL CAPITAL_NEED_SCORING MOCK ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
