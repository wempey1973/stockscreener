"""Ad-hoc mocked test: exercises update_history.py's diff logic without hitting the real Google Sheets API."""

import csv
import os
from unittest import mock

os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", '{"fake": "creds"}')
os.environ.setdefault("GOOGLE_SHEET_ID", "fake-sheet-id")

import update_history  # noqa: E402

CURRENT_RESULTS = [
    {"symbol": "AAA", "companyName": "Alpha Corp", "sector": "Technology", "industry": "Software",
     "marketCap": "500000000", "oneYearReturnPct": "50.0", "cik": "0000000001",
     "capital_need_score": "60.0", "cash_runway_component": "0.5", "leverage_component": "0.5",
     "capex_component": "0.5", "sector_component": "0.5", "active_unused_shelf": "True",
     "profitability_override": "False", "score_note": ""},
    {"symbol": "BBB", "companyName": "Beta Inc", "sector": "Industrials", "industry": "Machinery",
     "marketCap": "600000000", "oneYearReturnPct": "40.0", "cik": "0000000002",
     "capital_need_score": "30.0", "cash_runway_component": "0.3", "leverage_component": "0.3",
     "capex_component": "0.3", "sector_component": "0.3", "active_unused_shelf": "False",
     "profitability_override": "False", "score_note": ""},
]


class FakeWorksheet:
    def __init__(self, existing_rows):
        self._values = existing_rows

    def get_all_values(self):
        return self._values

    def append_rows(self, rows, value_input_option="RAW"):
        # A brand-new/cleared real Google Sheet holds [[]], not [] -- if our
        # fake still has that placeholder blank row, drop it once real rows
        # land, mirroring how the actual API behaves.
        if self._values == [[]]:
            self._values = []
        self._values.extend(rows)


def _write_results_csv(rows):
    with open(update_history.RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
        fieldnames = [k for k in rows[0].keys()]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_first_run_no_history():
    _write_results_csv(CURRENT_RESULTS)
    # [[]] matches what a real brand-new/cleared Google Sheet returns --
    # NOT [] -- this is the exact shape that caused the header-row bug.
    ws = FakeWorksheet(existing_rows=[[]])
    with mock.patch.object(update_history, "get_worksheet", lambda: ws):
        update_history.main()

    with open(update_history.NEW_THIS_WEEK_FILE, encoding="utf-8") as f:
        new_rows = list(csv.DictReader(f))
    assert {r["symbol"] for r in new_rows} == {"AAA", "BBB"}
    for r in new_rows:
        assert r["first_seen_date"]
    # header + 2 data rows now in the fake sheet
    all_values = ws.get_all_values()
    assert len(all_values) == 3, all_values
    assert all_values[0] == update_history.FIELDNAMES, all_values[0]


def test_only_genuinely_new_names_appended():
    _write_results_csv(CURRENT_RESULTS)
    header = list(update_history.FIELDNAMES)
    aaa_row = ["AAA", "Alpha Corp", "Technology", "Software", "500000000", "50.0",
               "0000000001", "60.0", "0.5", "0.5", "0.5", "0.5", "True", "False", "", "2026-08-01"]
    ws = FakeWorksheet(existing_rows=[header, aaa_row])
    with mock.patch.object(update_history, "get_worksheet", lambda: ws):
        update_history.main()

    with open(update_history.NEW_THIS_WEEK_FILE, encoding="utf-8") as f:
        new_rows = list(csv.DictReader(f))
    assert {r["symbol"] for r in new_rows} == {"BBB"}
    # sheet should now have header + AAA (pre-existing) + BBB (newly appended)
    all_values = ws.get_all_values()
    assert len(all_values) == 3
    symbols_in_sheet = {row[0] for row in all_values[1:]}
    assert symbols_in_sheet == {"AAA", "BBB"}


def test_no_new_names():
    _write_results_csv(CURRENT_RESULTS)
    header = list(update_history.FIELDNAMES)
    rows = [
        ["AAA"] + [""] * (len(header) - 1),
        ["BBB"] + [""] * (len(header) - 1),
    ]
    ws = FakeWorksheet(existing_rows=[header] + rows)
    with mock.patch.object(update_history, "get_worksheet", lambda: ws):
        update_history.main()

    with open(update_history.NEW_THIS_WEEK_FILE, encoding="utf-8") as f:
        new_rows = list(csv.DictReader(f))
    assert new_rows == []
    # nothing appended -- sheet unchanged
    assert len(ws.get_all_values()) == 3


class FakeResponse:
    def __init__(self, code, message):
        self._code, self._message = code, message

    def json(self):
        return {"error": {"code": self._code, "message": self._message, "status": "UNAVAILABLE"}}

    @property
    def text(self):
        return self._message


def _make_api_error(code=503, message="The service is currently unavailable."):
    import gspread.exceptions
    return gspread.exceptions.APIError(FakeResponse(code, message))


def test_with_retries_recovers_from_transient_error():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _make_api_error()
        return "ok"

    with mock.patch.object(update_history.time, "sleep", lambda *_: None):
        result = update_history.with_retries(flaky)
    assert result == "ok"
    assert calls["n"] == 3


def test_with_retries_gives_up_after_max_attempts():
    def always_fails():
        raise _make_api_error()

    with mock.patch.object(update_history.time, "sleep", lambda *_: None):
        try:
            update_history.with_retries(always_fails)
            assert False, "expected APIError to propagate"
        except Exception as exc:
            assert "unavailable" in str(exc).lower() or "503" in str(exc)


def main():
    test_with_retries_recovers_from_transient_error()
    test_with_retries_gives_up_after_max_attempts()
    with mock.patch.object(update_history, "RESULTS_FILE", "test_update_history_results.csv"), \
         mock.patch.object(update_history, "NEW_THIS_WEEK_FILE", "test_update_history_new.csv"):
        try:
            test_first_run_no_history()
            test_only_genuinely_new_names_appended()
            test_no_new_names()
        finally:
            for f in (update_history.RESULTS_FILE, update_history.NEW_THIS_WEEK_FILE):
                if os.path.exists(f):
                    os.remove(f)
    print("ALL UPDATE_HISTORY MOCK ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
