"""
Diffs the current run's screener_results.csv against the permanent history
log in a Google Sheet, appends genuinely-new names (never seen before) with
today's date, and writes new_this_week.csv containing just those new names
for the email step.

Requires env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON -- full contents of the service account JSON key
  GOOGLE_SHEET_ID             -- the target sheet's ID (from its URL)
"""

import csv
import json
import os
import sys
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

RESULTS_FILE = "screener_results.csv"
NEW_THIS_WEEK_FILE = "new_this_week.csv"

# Same column order as screener.py's write_results, plus first_seen_date.
FIELDNAMES = [
    "symbol", "companyName", "sector", "industry",
    "marketCap", "oneYearReturnPct", "cik",
    "capital_need_score", "cash_runway_component", "leverage_component",
    "capex_component", "sector_component", "active_unused_shelf",
    "profitability_override", "score_note", "first_seen_date",
]


def get_worksheet():
    creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_ID)
    return sheet.sheet1


def _is_effectively_empty(values):
    # A cleared/brand-new Google Sheet returns [[]] (one blank row), not [],
    # from get_all_values() -- treat any sheet with no non-blank rows as empty.
    return not any(row for row in values)


def load_known_symbols(ws):
    values = ws.get_all_values()
    if _is_effectively_empty(values):
        return set()
    header = values[0]
    if "symbol" not in header:
        return set()
    symbol_col = header.index("symbol")
    return {row[symbol_col] for row in values[1:] if len(row) > symbol_col and row[symbol_col]}


def load_current_results():
    with open(RESULTS_FILE, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID environment variables are required")

    ws = get_worksheet()
    known_symbols = load_known_symbols(ws)
    print(f"History sheet has {len(known_symbols)} known symbols")

    current_rows = load_current_results()
    print(f"Current run has {len(current_rows)} qualifying companies")

    today = date.today().isoformat()
    new_rows = []
    for row in current_rows:
        if row["symbol"] in known_symbols:
            continue
        row = dict(row)
        row["first_seen_date"] = today
        new_rows.append(row)

    print(f"{len(new_rows)} names are new (never seen before)")

    if not new_rows:
        print("Nothing new to append.")
    else:
        rows_to_append = [[row.get(field, "") for field in FIELDNAMES] for row in new_rows]
        if _is_effectively_empty(ws.get_all_values()):
            rows_to_append = [FIELDNAMES] + rows_to_append
        ws.append_rows(rows_to_append, value_input_option="RAW")
        print(f"Appended {len(new_rows)} new rows to the history sheet")

    with open(NEW_THIS_WEEK_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(new_rows, key=lambda r: r.get("capital_need_score") or 0, reverse=True):
            writer.writerow(row)
    print(f"Wrote {len(new_rows)} rows to {NEW_THIS_WEEK_FILE}")


if __name__ == "__main__":
    main()
