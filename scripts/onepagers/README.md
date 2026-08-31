# One-pager / summary doc generation

Reusable scripts for the weekly capital-raise research routine, replacing the
ad-hoc docx-generation code that got rewritten from scratch (with the same
bugs re-discovered each time) across the August 2026 catch-up batches.

Requires `python-docx` (`pip install python-docx` -- not persisted between
routine sessions, so this is a one-time install per run, same as before).

## Generate one one-pager

```
python scripts/onepagers/generate_onepager.py data.json one-pagers/<date>/<TICKER>_one_pager.docx
```

`data.json` fields: `ticker`, `company_name`, `sector_industry`, `market_cap`,
`raise_size`, `pct_market_cap`, `urgency`, `thesis`, `use_of_proceeds`,
`timing`, `why_raise_now`, `risks`, and optionally `summary_note` (e.g. a
sizing-cap disclosure). See the docstring in `generate_onepager.py` for the
full shape.

## Create or update 00_summary.docx for a batch

```
python scripts/onepagers/update_summary.py one-pagers/<date> batch.json
```

Creates the file fresh if it doesn't exist yet in that folder; if it does,
appends this batch's new Keep rows to the accumulated Keep table and appends
a new, separately-headed disposition table for this batch -- never touches
any earlier batch's rows or tables. Safe to call once per batch, including
across multiple batches/sessions on the same folder the same day.

See the docstring in `update_summary.py` for `batch.json`'s shape.

## Why these exist instead of writing docx code per-run

python-docx's by-name style lookup (`doc.styles["Heading 2"]`,
`table.style = "Table Grid"`) reliably raises `KeyError` against every docx
these routines have generated (docx-js and python-docx alike) so far --
`w:name` gets written as `"Heading 2"` (matching the display name) but
python-docx's lookup translates the key to the lowercase internal form
`"heading 2"` first and does an exact-match XPath, so it never finds it, even
though the style is right there and reading it back
(`paragraph.style.name`) works fine. Every batch in the catch-up run hit this
independently and worked around it differently. `onepager_lib.py` /
`summary_lib.py` sidestep it entirely: headings and table borders are
applied via direct run/cell formatting rather than named-style lookups, so
generated documents are immune to this regardless of what any given docx's
`styles.xml` does or doesn't define.
