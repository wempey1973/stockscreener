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

DISTRESS_RESULTS = [
    {"symbol": "ZZZ", "companyName": "Zulu Corp", "sector": "Consumer Discretionary", "industry": "Retail",
     "marketCap": "700000000", "oneYearReturnPct": "-30.0", "cik": "0000000009",
     "capital_need_score": "70.0", "cash_runway_component": "0.7", "leverage_component": "0.7",
     "capex_component": "0.7", "sector_component": "0.7", "active_unused_shelf": "True",
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


class FakeSpreadsheet:
    def __init__(self, sheet1, other_tabs=None):
        self.sheet1 = sheet1
        self._tabs = dict(other_tabs or {})

    def worksheet(self, title):
        import gspread.exceptions
        if title not in self._tabs:
            raise gspread.exceptions.WorksheetNotFound(title)
        return self._tabs[title]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(existing_rows=[[]])
        self._tabs[title] = ws
        return ws


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_process_screen_first_run_no_history():
    _write_csv("test_up_results.csv", CURRENT_RESULTS)
    # [[]] matches what a real brand-new/cleared Google Sheet returns --
    # NOT [] -- this is the exact shape that caused the header-row bug.
    ws = FakeWorksheet(existing_rows=[[]])
    new_rows = update_history.process_screen(
        ws, "test_up_results.csv", "test_new_this_week.csv", "test"
    )

    assert {r["symbol"] for r in new_rows} == {"AAA", "BBB"}
    for r in new_rows:
        assert r["first_seen_date"]
    with open("test_new_this_week.csv", encoding="utf-8") as f:
        file_rows = list(csv.DictReader(f))
    assert {r["symbol"] for r in file_rows} == {"AAA", "BBB"}
    # header + 2 data rows now in the fake sheet
    all_values = ws.get_all_values()
    assert len(all_values) == 3, all_values
    assert all_values[0] == update_history.FIELDNAMES, all_values[0]
    for f in ("test_up_results.csv", "test_new_this_week.csv"):
        os.remove(f)


def test_process_screen_only_genuinely_new_names_appended():
    _write_csv("test_up_results.csv", CURRENT_RESULTS)
    header = list(update_history.FIELDNAMES)
    aaa_row = ["AAA", "Alpha Corp", "Technology", "Software", "500000000", "50.0",
               "0000000001", "60.0", "0.5", "0.5", "0.5", "0.5", "True", "False", "", "2026-08-01"]
    ws = FakeWorksheet(existing_rows=[header, aaa_row])
    new_rows = update_history.process_screen(
        ws, "test_up_results.csv", "test_new_this_week.csv", "test"
    )

    assert {r["symbol"] for r in new_rows} == {"BBB"}
    all_values = ws.get_all_values()
    assert len(all_values) == 3
    symbols_in_sheet = {row[0] for row in all_values[1:]}
    assert symbols_in_sheet == {"AAA", "BBB"}
    for f in ("test_up_results.csv", "test_new_this_week.csv"):
        os.remove(f)


def test_process_screen_no_new_names():
    _write_csv("test_up_results.csv", CURRENT_RESULTS)
    header = list(update_history.FIELDNAMES)
    rows = [
        ["AAA"] + [""] * (len(header) - 1),
        ["BBB"] + [""] * (len(header) - 1),
    ]
    ws = FakeWorksheet(existing_rows=[header] + rows)
    new_rows = update_history.process_screen(
        ws, "test_up_results.csv", "test_new_this_week.csv", "test"
    )

    assert new_rows == []
    assert len(ws.get_all_values()) == 3
    for f in ("test_up_results.csv", "test_new_this_week.csv"):
        os.remove(f)


def test_main_processes_both_screens_and_creates_distress_tab():
    _write_csv("screener_results.csv", CURRENT_RESULTS)
    _write_csv("distress_results.csv", DISTRESS_RESULTS)

    up_ws = FakeWorksheet(existing_rows=[[]])
    spreadsheet = FakeSpreadsheet(sheet1=up_ws)  # no "Distress" tab yet -- must be auto-created

    with mock.patch.object(update_history, "get_spreadsheet", lambda: spreadsheet):
        update_history.main()

    with open("new_this_week.csv", encoding="utf-8") as f:
        up_new = list(csv.DictReader(f))
    assert {r["symbol"] for r in up_new} == {"AAA", "BBB"}

    with open("new_this_week_distress.csv", encoding="utf-8") as f:
        distress_new = list(csv.DictReader(f))
    assert {r["symbol"] for r in distress_new} == {"ZZZ"}

    distress_ws = spreadsheet.worksheet(update_history.DISTRESS_WORKSHEET_TITLE)
    distress_values = distress_ws.get_all_values()
    assert len(distress_values) == 2, distress_values  # header + ZZZ
    assert distress_values[0] == update_history.FIELDNAMES

    for f in ("screener_results.csv", "distress_results.csv", "new_this_week.csv", "new_this_week_distress.csv"):
        os.remove(f)


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


def test_with_retries_also_retries_non_apierror_gspread_exceptions():
    # SpreadsheetNotFound (raised by client.open_by_key on a 404) does NOT
    # inherit from APIError -- a narrower `except APIError` would silently
    # skip retries for it. This is exactly the gap a real run hit (a
    # corrupted GOOGLE_SHEET_ID secret produced a 404 that propagated on
    # the first attempt with no retry at all).
    import gspread.exceptions
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise gspread.exceptions.SpreadsheetNotFound("not found (yet)")
        return "ok"

    with mock.patch.object(update_history.time, "sleep", lambda *_: None):
        result = update_history.with_retries(flaky)
    assert result == "ok"
    assert calls["n"] == 3


def test_with_retries_dont_retry_bypasses_immediately():
    # WorksheetNotFound (checking whether a tab exists yet) is expected
    # control flow, not a failure -- dont_retry should propagate it on the
    # very first attempt, not burn through the full backoff schedule.
    import gspread.exceptions
    calls = {"n": 0}

    def always_not_found():
        calls["n"] += 1
        raise gspread.exceptions.WorksheetNotFound("no such tab")

    with mock.patch.object(update_history.time, "sleep", lambda *_: None):
        try:
            update_history.with_retries(
                always_not_found, dont_retry=(gspread.exceptions.WorksheetNotFound,)
            )
            assert False, "expected WorksheetNotFound to propagate"
        except gspread.exceptions.WorksheetNotFound:
            pass
    assert calls["n"] == 1, f"expected exactly 1 call (no retries), got {calls['n']}"


def main():
    test_with_retries_recovers_from_transient_error()
    test_with_retries_gives_up_after_max_attempts()
    test_with_retries_also_retries_non_apierror_gspread_exceptions()
    test_with_retries_dont_retry_bypasses_immediately()
    test_process_screen_first_run_no_history()
    test_process_screen_only_genuinely_new_names_appended()
    test_process_screen_no_new_names()
    test_main_processes_both_screens_and_creates_distress_tab()
    print("ALL UPDATE_HISTORY MOCK ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
