"""
Diffs each of this run's two candidate lists (up-screen and distress-screen)
against its own permanent history log in the Google Sheet (the up-screen
uses the first tab, unchanged from before; the distress-screen gets its own
"Distress" tab), appends genuinely-new names (never seen before, per that
screen's own tab) with today's date, and writes new_this_week.csv /
new_this_week_distress.csv containing just those new names for the email
step.

Requires env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON -- full contents of the service account JSON key
  GOOGLE_SHEET_ID             -- the target sheet's ID (from its URL)
"""

import csv
import json
import os
import sys
import time
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

RETRY_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 3  # 3s, 6s, 12s between attempts


def with_retries(fn, *args, **kwargs):
    """Retry a Sheets API call on transient errors (e.g. 503, 429) with
    exponential backoff. Google's API occasionally has brief outages/rate
    limits that clear up within seconds -- not worth failing the whole
    weekly run over."""
    last_exc = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            last_exc = exc
            if attempt == RETRY_ATTEMPTS:
                break
            delay = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            print(f"  Sheets API error on attempt {attempt}/{RETRY_ATTEMPTS} ({exc}); retrying in {delay}s...")
            time.sleep(delay)
    raise last_exc

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

DISTRESS_WORKSHEET_TITLE = "Distress"

# Same column order as screener.py's write_results, plus first_seen_date.
FIELDNAMES = [
    "symbol", "companyName", "sector", "industry",
    "marketCap", "oneYearReturnPct", "cik",
    "capital_need_score", "cash_runway_component", "leverage_component",
    "capex_component", "sector_component", "active_unused_shelf",
    "profitability_override", "score_note", "first_seen_date",
]


def get_spreadsheet():
    creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    return with_retries(client.open_by_key, GOOGLE_SHEET_ID)


def get_up_worksheet(spreadsheet):
    return spreadsheet.sheet1


def get_or_create_distress_worksheet(spreadsheet):
    try:
        return with_retries(spreadsheet.worksheet, DISTRESS_WORKSHEET_TITLE)
    except gspread.exceptions.WorksheetNotFound:
        print(f"  '{DISTRESS_WORKSHEET_TITLE}' tab not found, creating it")
        return with_retries(spreadsheet.add_worksheet, title=DISTRESS_WORKSHEET_TITLE, rows=1000, cols=20)


def _is_effectively_empty(values):
    # A cleared/brand-new Google Sheet returns [[]] (one blank row), not [],
    # from get_all_values() -- treat any sheet with no non-blank rows as empty.
    return not any(row for row in values)


def load_known_symbols(ws):
    values = with_retries(ws.get_all_values)
    if _is_effectively_empty(values):
        return set()
    header = values[0]
    if "symbol" not in header:
        return set()
    symbol_col = header.index("symbol")
    return {row[symbol_col] for row in values[1:] if len(row) > symbol_col and row[symbol_col]}


def load_current_results(results_file):
    with open(results_file, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def process_screen(ws, results_file, new_this_week_file, label):
    print(f"--- {label} ---")
    known_symbols = load_known_symbols(ws)
    print(f"  History tab has {len(known_symbols)} known symbols")

    current_rows = load_current_results(results_file)
    print(f"  Current run has {len(current_rows)} qualifying companies")

    today = date.today().isoformat()
    new_rows = []
    for row in current_rows:
        if row["symbol"] in known_symbols:
            continue
        row = dict(row)
        row["first_seen_date"] = today
        new_rows.append(row)

    print(f"  {len(new_rows)} names are new (never seen before)")

    if not new_rows:
        print("  Nothing new to append.")
    else:
        rows_to_append = [[row.get(field, "") for field in FIELDNAMES] for row in new_rows]
        if _is_effectively_empty(with_retries(ws.get_all_values)):
            rows_to_append = [FIELDNAMES] + rows_to_append
        with_retries(ws.append_rows, rows_to_append, value_input_option="RAW")
        print(f"  Appended {len(new_rows)} new rows to the history tab")

    with open(new_this_week_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(new_rows, key=lambda r: r.get("capital_need_score") or 0, reverse=True):
            writer.writerow(row)
    print(f"  Wrote {len(new_rows)} rows to {new_this_week_file}")

    return new_rows


def main():
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID environment variables are required")

    spreadsheet = get_spreadsheet()

    process_screen(
        get_up_worksheet(spreadsheet),
        results_file="screener_results.csv",
        new_this_week_file="new_this_week.csv",
        label="Up-screen",
    )
    process_screen(
        get_or_create_distress_worksheet(spreadsheet),
        results_file="distress_results.csv",
        new_this_week_file="new_this_week_distress.csv",
        label="Distress-screen",
    )


if __name__ == "__main__":
    main()
