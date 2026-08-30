"""Ad-hoc mocked test: exercises screener.py's logic without hitting real APIs."""

import os
from unittest import mock

import pandas as pd

os.environ.setdefault("SEC_USER_AGENT", "Test test@example.com")

import screener  # noqa: E402

NASDAQ_ROWS = [
    {"symbol": "AAA", "name": "Alpha Corp Common Stock", "country": "United States",
     "marketCap": "500000000", "sector": "Technology", "industry": "Software"},
    {"symbol": "BBB", "name": "Beta Inc Common Stock", "country": "United States",
     "marketCap": "900000000", "sector": "Industrials", "industry": "Machinery"},
    {"symbol": "CCC", "name": "Gamma LLC Common Stock", "country": "United States",
     "marketCap": "1200000000", "sector": "Healthcare", "industry": "Biotech"},
    {"symbol": "DDD", "name": "Delta Warrant", "country": "United States",
     "marketCap": "300000000", "sector": "Technology", "industry": "Software"},  # excluded: warrant
    {"symbol": "EEE", "name": "Epsilon Corp Common Stock", "country": "Canada",
     "marketCap": "400000000", "sector": "Technology", "industry": "Software"},  # excluded: not US
    {"symbol": "FFF", "name": "Foxtrot Corp Common Stock", "country": "United States",
     "marketCap": "5000000000", "sector": "Technology", "industry": "Software"},  # excluded: cap too high
    {"symbol": "MRG", "name": "Merger Corp Common Stock", "country": "United States",
     "marketCap": "700000000", "sector": "Financials", "industry": "Property-Casualty Insurers"},
    {"symbol": "OFR", "name": "Offering Corp Common Stock", "country": "United States",
     "marketCap": "700000000", "sector": "Financials", "industry": "Specialty Insurers"},
    {"symbol": "BNK", "name": "Bank Holding Co Common Stock", "country": "United States",
     "marketCap": "700000000", "sector": "Finance", "industry": "Major Banks"},  # excluded: bank
    {"symbol": "RET", "name": "Realty Trust Common Stock", "country": "United States",
     "marketCap": "700000000", "sector": "Real Estate",
     "industry": "Real Estate Investment Trusts"},  # excluded: REIT
    {"symbol": "CEF", "name": "Global Income Fund Inc. Common Stock", "country": "United States",
     "marketCap": "700000000", "sector": "Finance", "industry": "Investment Managers"},  # excluded: closed-end fund
    {"symbol": "HOLD", "name": "D/B/A Diversified Holdings Shares of Beneficial Interest",
     "country": "United States", "marketCap": "700000000", "sector": "Consumer Discretionary",
     "industry": "Home Furnishings"},  # kept: operating co, not Finance sector despite "Beneficial Interest"
    {"symbol": "DUPA", "name": "Delta Holdings Inc. Class A Common Stock", "country": "United States",
     "marketCap": "800000000", "sector": "Technology", "industry": "Software"},
    {"symbol": "DUPB", "name": "Delta Holdings Inc. Class B Common Stock", "country": "United States",
     "marketCap": "600000000", "sector": "Technology", "industry": "Software"},  # excluded: dup share class, lower cap
    {"symbol": "BDC1", "name": "Business Development Corp Common Stock", "country": "United States",
     "marketCap": "700000000", "sector": "Finance",
     "industry": "Finance/Investors Services"},  # excluded: ambiguous finance industry (BDC), no override
    {"symbol": "VALU", "name": "Value Line Inc. Common Stock", "country": "United States",
     "marketCap": "700000000", "sector": "Finance",
     "industry": "Investment Managers"},  # kept: ambiguous industry but on the operating-company override list
    {"symbol": "PIPE", "name": "Pipeline Corp Common Stock", "country": "United States",
     "marketCap": "700000000", "sector": "Technology", "industry": "Software"},
]

# 1-year returns: AAA +52.3%, BBB +12.1% (filtered out), CCC +41.0%, MRG/OFR +40%
CLOSES = {
    "AAA": [100, 152.3], "BBB": [100, 112.1], "CCC": [100, 141.0],
    "MRG": [100, 140.0], "OFR": [100, 140.0], "PIPE": [100, 145.0],
}

TICKER_TO_CIK = {
    "AAA": "0000000001", "CCC": "0000000003", "MRG": "0000000004",
    "OFR": "0000000005", "PIPE": "0000000006",
}

# AAA has a recent 424B4 (real offering) -> should be excluded in step 2
# CCC has only an old S-3 shelf and no recent 424B/S-1 -> should survive
SEC_FILINGS = {
    "0000000001": {
        "filings": {"recent": {
            "form": ["424B4", "10-Q"],
            "filingDate": ["2026-02-01", "2025-11-01"],
            "accessionNumber": ["0000000001-26-000001", "0000000001-25-000002"],
            "primaryDocument": ["aaa_424b4.htm", "aaa_10q.htm"],
        }}
    },
    "0000000003": {
        "filings": {"recent": {
            "form": ["S-3", "10-K"],
            "filingDate": ["2022-01-01", "2025-08-01"],
            "accessionNumber": ["0000000003-22-000001", "0000000003-25-000002"],
            "primaryDocument": ["ccc_s3.htm", "ccc_10k.htm"],
        }}
    },
    # MRG's only recent filing is a 424B3 -- but it's a merger prospectus,
    # not a capital raise, so it should NOT count as an offering.
    "0000000004": {
        "filings": {"recent": {
            "form": ["424B3"],
            "filingDate": ["2026-01-15"],
            "accessionNumber": ["0000000004-26-000001"],
            "primaryDocument": ["mrg_424b3.htm"],
        }}
    },
    # OFR's only recent filing is a 424B3 that IS a genuine primary offering.
    "0000000005": {
        "filings": {"recent": {
            "form": ["424B3"],
            "filingDate": ["2026-01-15"],
            "accessionNumber": ["0000000005-26-000001"],
            "primaryDocument": ["ofr_424b3.htm"],
        }}
    },
    # PIPE did a Regulation D private placement -- should be excluded even
    # though Form D never shows up as an S-1/424B filing.
    "0000000006": {
        "filings": {"recent": {
            "form": ["D", "10-Q"],
            "filingDate": ["2026-01-10", "2025-11-01"],
            "accessionNumber": ["0000000006-26-000001", "0000000006-25-000002"],
            "primaryDocument": ["pipe_formd.htm", "pipe_10q.htm"],
        }}
    },
}

MRG_424B3_HTML = """<html><body><p>Filed Pursuant to Rule 424(b)(3)</p>
<p>PROXY STATEMENT/PROSPECTUS</p>
<p>This proxy statement/prospectus relates to the Agreement and Plan of Merger,
dated as of January 1, 2026, providing for the merger of Target Bank into
Merger Corp. A special meeting of shareholders of Target Bank will be held
to vote on the merger agreement.</p></body></html>"""

OFR_424B3_HTML = """<html><body><p>Filed Pursuant to Rule 424(b)(3)</p>
<p>PROSPECTUS SUPPLEMENT</p>
<p>We are offering 2,000,000 shares of our common stock. The public offering
price is $10.00 per share. Net proceeds to us, after underwriting discount,
will be used for general corporate purposes.</p></body></html>"""


class FakeResponse:
    def __init__(self, json_data=None, text_data=None, status_code=200):
        self._json = json_data
        self.text = text_data
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def fake_requests_get(url, headers=None, params=None, timeout=None):
    if "nasdaq.com" in url:
        return FakeResponse(json_data={"data": {"rows": NASDAQ_ROWS}})
    if "mrg_424b3.htm" in url:
        return FakeResponse(text_data=MRG_424B3_HTML)
    if "ofr_424b3.htm" in url:
        return FakeResponse(text_data=OFR_424B3_HTML)
    raise AssertionError(f"unexpected requests.get call: {url}")


def fake_yf_download(chunk, **kwargs):
    cols = pd.MultiIndex.from_product([chunk, ["Close"]], names=["Ticker", "Price"])
    df = pd.DataFrame(index=range(2), columns=cols, dtype=float)
    for symbol in chunk:
        if symbol in CLOSES:
            df[(symbol, "Close")] = CLOSES[symbol]
    return df


def fake_load_ticker_to_cik_map():
    return TICKER_TO_CIK


def fake_sec_get(url, retries=3):
    for cik, data in SEC_FILINGS.items():
        if cik in url:
            return data
    return None


def main():
    with mock.patch.object(screener.requests, "get", fake_requests_get), \
         mock.patch.object(screener.yf, "download", fake_yf_download), \
         mock.patch.object(screener, "load_ticker_to_cik_map", fake_load_ticker_to_cik_map), \
         mock.patch.object(screener, "sec_get", fake_sec_get), \
         mock.patch.object(screener, "OUTPUT_FILE", "test_screener_mock_output.csv"), \
         mock.patch("time.sleep", lambda *_: None):

        candidates = screener.get_candidate_universe()
        candidate_symbols = {c["symbol"] for c in candidates}
        assert candidate_symbols == {
            "AAA", "BBB", "CCC", "MRG", "OFR", "HOLD", "DUPA", "VALU", "PIPE",
        }, candidate_symbols
        assert "BNK" not in candidate_symbols, "bank should be excluded at step 1a"
        assert "RET" not in candidate_symbols, "REIT should be excluded at step 1a"
        assert "CEF" not in candidate_symbols, "closed-end fund should be excluded at step 1a"
        assert "DUPB" not in candidate_symbols, "lower-cap duplicate share class should be excluded at step 1a"
        assert "BDC1" not in candidate_symbols, "BDC in ambiguous finance industry (no override) should be excluded"
        assert "VALU" in candidate_symbols, "VALU is on the operating-company override list, should be kept"

        survivors = screener.filter_by_appreciation(candidates)
        survivor_symbols = {c["symbol"] for c in survivors}
        assert survivor_symbols == {"AAA", "CCC", "MRG", "OFR", "PIPE"}, survivor_symbols

        final = screener.filter_by_no_offering(survivors)
        final_symbols = {c["symbol"] for c in final}
        assert final_symbols == {"CCC", "MRG"}, final_symbols
        assert "PIPE" not in final_symbols, "Form D private placement should count as a recent offering"

        screener.write_results(final)
        with open(screener.OUTPUT_FILE, encoding="utf-8") as f:
            content = f.read()
        assert "CCC" in content and "MRG" in content
        assert "AAA" not in content and "OFR" not in content and "PIPE" not in content
        os.remove(screener.OUTPUT_FILE)

    print("ALL MOCK ASSERTIONS PASSED")
    print("DDD (warrant), EEE (non-US), FFF (cap too high), BNK (bank), RET (REIT), CEF (closed-end fund) "
          "correctly excluded at step 1a")
    print("HOLD correctly KEPT despite 'Shares of Beneficial Interest' naming (not Finance sector)")
    print("DUPB correctly excluded as a lower-market-cap duplicate share class of DUPA (same company)")
    print("BBB correctly dropped at step 1b (only 12.1% < 35.0% threshold)")
    print("AAA correctly dropped at step 2 (recent 424B4 primary offering)")
    print("MRG correctly SURVIVED despite a recent 424B3 (it's a merger prospectus, not an offering)")
    print("PIPE correctly EXCLUDED for a Form D private placement")
    print("OFR correctly EXCLUDED for a recent 424B3 that IS a genuine primary offering")
    print("CCC correctly survived both steps")


if __name__ == "__main__":
    main()
