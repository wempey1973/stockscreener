#!/usr/bin/env python3
"""
Create or update one-pagers/<date>/00_summary.docx for one batch's results.

Always ADDS: creates the file fresh if it doesn't exist, otherwise appends
this batch's new Keep rows to the accumulated Keep table and appends a new,
separately-headed disposition table for this batch -- never touches any
earlier batch's rows or tables.

Usage:
    python update_summary.py <folder> batch.json

batch.json shape:
{
  "week_of": "2026-08-30",
  "batch_label": "Batch 2 (rescheduled): 35 distress names",
  "new_keep_rows": [
    ["Distress", "LNSR", "$102.0M", "$20.0M", "19.6%", "Medium-High"],
    ...
  ],
  "disposition_rows": [
    ["Distress", "LNSR", "53.0", "Keep", "Thin core cash runway ..."],
    ...
  ]
}

new_keep_rows only needs rows for names THIS batch newly kept (not the whole
accumulated table -- those already exist if the file existed). disposition_rows
must cover every ticker in this batch's own input, including Drops/Ambiguous.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from summary_lib import load_or_create, append_keep_rows, append_disposition_section, save


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: python update_summary.py <folder> batch.json")
    folder, batch_path = sys.argv[1], sys.argv[2]
    with open(batch_path, encoding="utf-8") as f:
        batch = json.load(f)

    for req in ("week_of", "batch_label", "disposition_rows"):
        if req not in batch:
            sys.exit(f"batch.json is missing required field: {req}")

    doc, path, keep_table, existed = load_or_create(folder, batch["week_of"])
    if batch.get("new_keep_rows"):
        append_keep_rows(keep_table, batch["new_keep_rows"])
    append_disposition_section(doc, batch["batch_label"], batch["disposition_rows"])
    save(doc, path)
    print(f"{'updated' if existed else 'created'} {path}")
    print(f"  +{len(batch.get('new_keep_rows', []))} Keep rows, "
          f"+{len(batch['disposition_rows'])} disposition rows ({batch['batch_label']})")


if __name__ == "__main__":
    main()
