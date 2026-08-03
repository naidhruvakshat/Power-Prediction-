"""
parse_grid_india_reports.py

Converts downloaded Grid India PSP reports (xls preferred, pdf fallback) into
a single long-format CSV: date, state, demand_mw.

HOW IT FINDS THE DATA (read before trusting the output):
  Rather than hard-coding "row 12, column 4" -- which will silently break the
  moment a report's layout shifts by one row -- this script scans every cell
  in every sheet/table for a match against a canonical state-name list, and
  takes the first numeric value to the RIGHT of that match on the same row as
  the demand figure. This is deliberately conservative and inspectable: every
  extraction is logged, and rows where no numeric value is found are reported
  as gaps rather than silently skipped.

  This heuristic assumes the state name and its demand-met value sit on the
  same row, which is the standard PSP report layout (state-wise demand table,
  one row per state, columns for max demand met / time / energy met / etc.).
  If a report has multiple numeric columns after the state name (e.g. "Max
  Demand Met (MW)" then "Time" then "Energy Met (MU)"), this takes the FIRST
  one -- confirm with inspect_grid_india_report.py that this is the column
  you want (typically "Max Demand Met"), and adjust VALUE_COLUMN_OFFSET below
  if not.

USAGE:
  python parse_grid_india_reports.py --in-dir data/raw/demand --out demand_long.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path

# Canonical state/UT names as they should appear in your final dataset.
# Keys are lowercase, whitespace-normalised, for matching; values are the
# canonical display name to write to output.
STATE_NAMES = {
    "andhra pradesh": "Andhra Pradesh",
    "arunachal pradesh": "Arunachal Pradesh",
    "assam": "Assam",
    "bihar": "Bihar",
    "chhattisgarh": "Chhattisgarh",
    "goa": "Goa",
    "gujarat": "Gujarat",
    "haryana": "Haryana",
    "himachal pradesh": "Himachal Pradesh",
    "jharkhand": "Jharkhand",
    "karnataka": "Karnataka",
    "kerala": "Kerala",
    "madhya pradesh": "Madhya Pradesh",
    "maharashtra": "Maharashtra",
    "manipur": "Manipur",
    "meghalaya": "Meghalaya",
    "mizoram": "Mizoram",
    "nagaland": "Nagaland",
    "odisha": "Odisha",
    "orissa": "Odisha",  # older reports may use the old spelling
    "punjab": "Punjab",
    "rajasthan": "Rajasthan",
    "sikkim": "Sikkim",
    "tamil nadu": "Tamil Nadu",
    "telangana": "Telangana",
    "tripura": "Tripura",
    "uttar pradesh": "Uttar Pradesh",
    "uttarakhand": "Uttarakhand",
    "west bengal": "West Bengal",
    "delhi": "Delhi",
    "puducherry": "Puducherry",
    "jammu and kashmir": "Jammu and Kashmir",
    "j&k": "Jammu and Kashmir",
    "ladakh": "Ladakh",
    "chandigarh": "Chandigarh",
}

# Which numeric cell (0-indexed, counting only cells to the right of the
# matched state-name cell) to take as the demand value. Verify against
# inspect_grid_india_report.py output before trusting this -- see docstring.
VALUE_COLUMN_OFFSET = 0

NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def normalise(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip().lower()


def extract_rows_from_grid(grid: list[list], report_date: str) -> tuple[list[tuple], list[str]]:
    """grid: list of rows, each a list of cell values (already stringified ok).
    Returns (extracted rows as (date, state, value), warnings)."""
    extracted = []
    warnings = []
    for row in grid:
        for ci, cell in enumerate(row):
            key = normalise(cell)
            if key in STATE_NAMES:
                state = STATE_NAMES[key]
                numeric_cells = [c for c in row[ci + 1 :] if NUMERIC_RE.match(normalise(c))]
                if len(numeric_cells) > VALUE_COLUMN_OFFSET:
                    value = float(numeric_cells[VALUE_COLUMN_OFFSET])
                    extracted.append((report_date, state, value))
                else:
                    warnings.append(f"{report_date}: matched state '{state}' but found no numeric value on row: {row}")
                break  # only take the first state match per row
    return extracted, warnings


def date_from_filename(path: Path) -> str:
    # files are named <YYYY-MM-DD>_NLDC_PSP.<ext> by grid_india_download.py
    return path.stem.split("_NLDC_PSP")[0]


def parse_xls(path: Path) -> tuple[list[tuple], list[str]]:
    import pandas as pd

    report_date = date_from_filename(path)
    sheets = pd.read_excel(path, sheet_name=None, header=None)
    all_rows, all_warnings = [], []
    for _name, df in sheets.items():
        grid = df.astype(object).where(df.notna(), None).values.tolist()
        rows, warnings = extract_rows_from_grid(grid, report_date)
        all_rows.extend(rows)
        all_warnings.extend(warnings)
    return all_rows, all_warnings


def parse_pdf(path: Path) -> tuple[list[tuple], list[str]]:
    import pdfplumber

    report_date = date_from_filename(path)
    all_rows, all_warnings = [], []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                rows, warnings = extract_rows_from_grid(table, report_date)
                all_rows.extend(rows)
                all_warnings.extend(warnings)
    return all_rows, all_warnings


def main():
    parser = argparse.ArgumentParser(description="Parse downloaded Grid India PSP reports into long format")
    parser.add_argument("--in-dir", type=Path, default=Path("data/raw/demand"))
    parser.add_argument("--out", type=Path, default=Path("demand_long.csv"))
    args = parser.parse_args()

    files = sorted(list(args.in_dir.glob("*.xls")) + list(args.in_dir.glob("*.xlsx")) + list(args.in_dir.glob("*.pdf")))
    print(f"Found {len(files)} report files in {args.in_dir}")

    all_rows = []
    all_warnings = []
    dates_seen = set()

    for path in files:
        try:
            if path.suffix.lower() in (".xls", ".xlsx"):
                rows, warnings = parse_xls(path)
            else:
                rows, warnings = parse_pdf(path)
        except Exception as e:  # noqa: BLE001 - log and keep going across a whole batch
            print(f"ERROR parsing {path}: {e}")
            continue

        if not rows:
            print(f"WARNING: no states matched in {path} -- layout may differ, inspect this file manually")
        all_rows.extend(rows)
        all_warnings.extend(warnings)
        if rows:
            dates_seen.add(rows[0][0])

    # Dedupe: if a state matched more than once on a page (shouldn't happen,
    # but be defensive), keep the first occurrence per (date, state).
    seen = set()
    deduped = []
    for r in all_rows:
        key = (r[0], r[1])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "state", "demand_mw"])
        writer.writerows(deduped)

    print(f"\nExtracted {len(deduped)} (date, state) rows across {len(dates_seen)} dates -> {args.out}")
    if all_warnings:
        print(f"{len(all_warnings)} warnings (states matched with no numeric value found). First 10:")
        for w in all_warnings[:10]:
            print(" ", w)
    missing_states = set(STATE_NAMES.values()) - {r[1] for r in deduped}
    if missing_states and dates_seen:
        print(f"\nStates never matched in any file (check spelling/layout): {sorted(missing_states)}")


if __name__ == "__main__":
    sys.exit(main())
