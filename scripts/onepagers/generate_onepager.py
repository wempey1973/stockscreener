#!/usr/bin/env python3
"""
Generate one <TICKER>_one_pager.docx from a JSON data file.

Usage:
    python generate_onepager.py data.json output_path.docx

data.json shape (all values strings unless noted):
{
  "ticker": "GETY",
  "company_name": "Getty Images Holdings Inc",
  "sector_industry": "Consumer Discretionary / Business Services",
  "market_cap": "$105.9M",
  "raise_size": "$42.4M",
  "pct_market_cap": "40.0%",
  "urgency": "High",
  "thesis": "...",
  "use_of_proceeds": "...",
  "timing": "...",
  "why_raise_now": "...",
  "risks": "...",
  "summary_note": "optional -- e.g. a sizing-cap disclosure"
}
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from onepager_lib import create_one_pager

REQUIRED = [
    "ticker", "company_name", "sector_industry", "market_cap", "raise_size",
    "pct_market_cap", "urgency", "thesis", "use_of_proceeds", "timing",
    "why_raise_now", "risks",
]


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: python generate_onepager.py data.json output_path.docx")
    data_path, output_path = sys.argv[1], sys.argv[2]
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    missing = [k for k in REQUIRED if not data.get(k)]
    if missing:
        sys.exit(f"data.json is missing required field(s): {', '.join(missing)}")
    create_one_pager(data, output_path)
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
