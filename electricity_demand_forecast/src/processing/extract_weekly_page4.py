"""
extract_weekly_page4.py

Extracts the "Energy Consumption in States (MUs)" table from each downloaded
Grid India weekly report PDF. Outputs one long-format CSV with a TRUE DAILY
row per state per day (not a weekly aggregate):
  date, state, energy_mu, week_start, week_end, source_file

WHY THIS ISN'T A SIMPLE "FIND THE STATE, GRAB ONE NUMBER" SCRIPT (v2 notes):
  The first version of this script assumed one energy_mu value per state per
  week. Inspecting real files (both the old English-only report template and
  the newer bilingual Hindi/English template) showed that's wrong: the table
  actually has ONE COLUMN PER DAY of the week (7 date columns), e.g.:

    Region  States    01-04-2024  02-04-2024  ...  07-04-2024
    NR      Punjab    138.0       142.7       ...  148.8

  So each report file gives 7 real daily values per state, not 1. The first
  version only grabbed the first numeric cell per row -- meaning even the
  files it marked "successful" were silently discarding 6 of every 7 days
  of real data. This version reads the header row's actual dates and pairs
  every day's value with its real date, so nothing is thrown away.

TWO MORE THINGS THE REAL DATA REVEALED THAT A NAIVE PARSER WOULD GET WRONG:
  1. State names are not always plain English. Newer (bilingual) reports
     write each state as "<Hindi label> <English name/abbreviation>" in one
     cell, e.g. "पर्ां ि Punjab" or "उिर प्रिेश UP". Matching requires pulling
     the trailing Latin-script label out of the cell rather than comparing
     the whole cell text.
  2. Several rows in this table are NOT states -- they're large industrial/
     utility consumers with their own direct grid connection that Grid India
     reports alongside states in the same table: Railways (traction power),
     DVC (Damodar Valley Corporation, spans Jharkhand/West Bengal), BALCO,
     AMNSIL, DNHDDPDCL. These are recognised and explicitly skipped (logged,
     not silently dropped) rather than left to fail a name match silently.

HOW IT FINDS THE TABLE:
  Rather than assuming a fixed page number, it scans pages (page index 3 --
  the 4th page -- first, since that's where it's been every time so far,
  then falls back to every other page) looking for a table whose header row
  has 5+ cells matching a DD-MM-YYYY date pattern. That's a much more
  reliable signature than a page number, since report layouts have already
  been observed to shift between report eras.

USAGE:
  python extract_weekly_page4.py --in-dir weekly_reports --out weekly_energy_long.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import pdfplumber

# Canonical state/UT names. Extended with common abbreviations actually seen
# in real Grid India report tables (UP, HP, MP, J&K/JK, WB, Pondy) -- these
# show up as-is in the "States" column, not spelled out.
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
    "hp": "Himachal Pradesh",
    "jharkhand": "Jharkhand",
    "karnataka": "Karnataka",
    "kerala": "Kerala",
    "madhya pradesh": "Madhya Pradesh",
    "mp": "Madhya Pradesh",
    "maharashtra": "Maharashtra",
    "manipur": "Manipur",
    "meghalaya": "Meghalaya",
    "mizoram": "Mizoram",
    "nagaland": "Nagaland",
    "odisha": "Odisha",
    "orissa": "Odisha",
    "punjab": "Punjab",
    "rajasthan": "Rajasthan",
    "sikkim": "Sikkim",
    "tamil nadu": "Tamil Nadu",
    "telangana": "Telangana",
    "tripura": "Tripura",
    "uttar pradesh": "Uttar Pradesh",
    "up": "Uttar Pradesh",
    "uttarakhand": "Uttarakhand",
    "west bengal": "West Bengal",
    "wb": "West Bengal",
    "delhi": "Delhi",
    "puducherry": "Puducherry",
    "pondy": "Puducherry",
    "pondicherry": "Puducherry",
    "jammu and kashmir": "Jammu and Kashmir",
    "j&k": "Jammu and Kashmir",
    "jk": "Jammu and Kashmir",
    "ladakh": "Ladakh",
    "chandigarh": "Chandigarh",
}

# Large direct-connected consumers that appear as rows in this table but are
# NOT states -- recognised so they're logged as an intentional skip instead
# of showing up as a confusing "unmatched row" warning.
NON_STATE_PREFIXES = ("railways", "dnhddpdcl", "amnsil", "balco", "dvc", "ril")

DATE_HEADER_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
ASCII_RUN_RE = re.compile(r"[a-z][a-z&.\- ]*[a-z]|[a-z]")

# For extracting the reporting week from the filename, e.g.
# "Weekly 300326 to 050426_544.pdf" -> 30-Mar-2026 to 05-Apr-2026.
FILENAME_DDMMYY_RANGE_RE = re.compile(
    r"Weekly\D*(\d{2})(\d{2})(\d{2})\D+to\D*(\d{2})(\d{2})(\d{2})", re.IGNORECASE
)


def normalise(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip().lower()


def match_state(cell) -> str | None:
    """Pull every contiguous run of Latin letters out of a (possibly
    bilingual) cell and check each against the alias table, longest run
    first -- the English name/abbreviation is usually the longest Latin run
    in a Hindi+English cell."""
    text = normalise(cell)
    if not text:
        return None
    runs = sorted(ASCII_RUN_RE.findall(text), key=len, reverse=True)
    for run in runs:
        run = run.strip()
        if run in STATE_NAMES:
            return STATE_NAMES[run]
    return None


def is_known_non_state(cell) -> bool:
    text = normalise(cell)
    return any(p in text for p in NON_STATE_PREFIXES)


def normalise_ddmmyyyy(s: str) -> str:
    d, m, y = s.split("-")
    return f"{y}-{m}-{d}"


def find_state_col(header_row: list, date_cols: list[int]) -> int:
    for ci, c in enumerate(header_row):
        if c and "state" in normalise(c):
            return ci
    # Fallback: the column immediately left of the first date column.
    return max(date_cols[0] - 1, 0)


def find_energy_table(pdf) -> tuple[int, list, int, list[int]] | None:
    """Search pages for a table whose header row has >=5 DD-MM-YYYY cells.
    Tries page index 3 (4th page) first since that's matched every real file
    seen so far, then falls back to scanning every page."""
    n_pages = len(pdf.pages)
    page_order = [3] + [i for i in range(n_pages) if i != 3] if n_pages > 3 else list(range(n_pages))
    for pi in page_order:
        page = pdf.pages[pi]
        for table in page.extract_tables():
            for ri, row in enumerate(table):
                date_cols = [ci for ci, c in enumerate(row) if c and DATE_HEADER_RE.match(str(c).strip())]
                if len(date_cols) >= 5:
                    return pi, table, ri, date_cols
    return None


def guess_week_range_from_filename(path: Path) -> tuple[str, str] | None:
    m = FILENAME_DDMMYY_RANGE_RE.search(path.stem)
    if not m:
        return None
    d1, m1, y1, d2, m2, y2 = m.groups()
    y1 = "20" + y1 if len(y1) == 2 else y1
    y2 = "20" + y2 if len(y2) == 2 else y2
    return f"{y1}-{m1}-{d1}", f"{y2}-{m2}-{d2}"


def process_pdf(path: Path) -> tuple[list[tuple], list[str], str]:
    """Returns (rows, skipped_non_state_labels, warning).
    rows = (date, state, energy_mu)."""
    with pdfplumber.open(path) as pdf:
        found = find_energy_table(pdf)
        week_range = guess_week_range_from_filename(path) or ("", "")

        if found is None:
            return [], [], "could not find an Energy Consumption table (no header row with >=5 date columns on any page)"

        page_idx, table, header_ri, date_cols = found
        header_row = table[header_ri]
        header_dates = [normalise_ddmmyyyy(str(header_row[c]).strip()) for c in date_cols]
        state_col = find_state_col(header_row, date_cols)

        rows = []
        skipped = []
        unmatched = []
        for row in table[header_ri + 1 :]:
            state_cell = row[state_col] if state_col < len(row) else None
            state = match_state(state_cell)
            if not state:
                if state_cell and is_known_non_state(state_cell):
                    skipped.append(normalise(state_cell))
                elif state_cell and normalise(state_cell):
                    unmatched.append(str(state_cell))
                continue
            for date_str, ci in zip(header_dates, date_cols):
                raw = row[ci] if ci < len(row) else None
                val_text = normalise(raw)
                if NUMERIC_RE.match(val_text):
                    rows.append((date_str, state, float(val_text)))

        warning = ""
        if page_idx != 3:
            warning += f"NOTE: table found on page index {page_idx}, not the usual page index 3 -- verify this file. "
        if unmatched:
            warning += f"{len(unmatched)} row(s) had text in the state column that didn't match any known state/alias: {unmatched[:5]}. "
        n_states_found = len({r[1] for r in rows})
        if 0 < n_states_found < 25:
            warning += f"only matched {n_states_found} distinct states (expected ~28-34) -- table shape may differ, spot check this file. "

        return rows, skipped, warning.strip()


def main():
    parser = argparse.ArgumentParser(description="Extract daily state-wise energy consumption from Grid India weekly report PDFs")
    parser.add_argument("--in-dir", type=Path, default=Path("weekly_reports"))
    parser.add_argument("--out", type=Path, default=Path("weekly_energy_long.csv"))
    args = parser.parse_args()

    files = sorted(args.in_dir.glob("*.pdf"))
    print(f"Found {len(files)} PDFs in {args.in_dir}")

    all_rows = []
    warnings = []
    all_skipped = set()

    for path in files:
        try:
            rows, skipped, warning = process_pdf(path)
        except Exception as e:  # noqa: BLE001 - log and keep going across the batch
            print(f"ERROR: {path.name}: {e}")
            continue

        week_range = guess_week_range_from_filename(path) or ("", "")
        for date_str, state, value in rows:
            all_rows.append((date_str, state, value, week_range[0], week_range[1], path.name))
        all_skipped.update(skipped)

        if warning:
            warnings.append(f"{path.name}: {warning}")
        n_days = len({r[0] for r in rows})
        n_states = len({r[1] for r in rows})
        print(
            f"{path.name}: {len(rows)} values ({n_states} states x up to {n_days} days)"
            + (f"  [{warning}]" if warning else "")
        )

    # IMPORTANT: files were processed in alphabetical filename order (e.g.
    # "010424" then "010724"), which is NOT chronological -- filenames start
    # with the day, not the year, so plain string sort scrambles the row
    # order. Sort the actual output rows by (state, date) before writing --
    # grouped alphabetically by state, chronological within each state --
    # regardless of the order files were processed in.
    all_rows.sort(key=lambda r: (r[1], r[0]))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "state", "energy_mu", "week_start", "week_end", "source_file"])
        writer.writerows(all_rows)

    unique_dates = len({r[0] for r in all_rows})
    print(f"\nWrote {len(all_rows)} rows covering {unique_dates} unique dates -> {args.out}")

    if all_skipped:
        print(f"\nNon-state rows recognised and intentionally skipped (not states, so excluded): {sorted(all_skipped)}")

    if warnings:
        print(f"\n{len(warnings)} file(s) worth a manual look:")
        for w in warnings:
            print(" ", w)


if __name__ == "__main__":
    sys.exit(main())
