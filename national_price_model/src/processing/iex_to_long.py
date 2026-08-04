"""
iex_to_long.py

Parses every IEX Market Snapshot xlsx file (DAM, RTM, GDAM -- 2022 through
2026) into one long-format 15-minute-block table:
  timestamp, market_type, purchase_bid_mw, sell_bid_mw, mcv_mw,
  final_scheduled_volume_mw, mcp_rs_mwh, source_file

WHY COLUMN-NAME-BASED PARSING, NOT FIXED INDICES:
  RTM files have an extra "Session ID" column that DAM/GDAM files don't,
  shifting every column after "Hour" over by one. Reading real files (not
  assuming layout) confirmed this. Fixed-index parsing would silently read
  Sell Bid values into the Purchase Bid column for every RTM file -- so the
  header row is located dynamically and every value is read by column name.

WHY THE "TIME BLOCK" TEXT IS PARSED WITH A REGEX, NOT A FIXED SPLIT:
  DAM/GDAM files write it as "00:00 - 00:15" (spaces around the dash); RTM
  files write "00:00-00:15" (no spaces). A regex pulling the first HH:MM
  pattern out of the cell handles both without caring about the separator.

HOW SUMMARY ROWS (Total/Max/Min/Avg per day) ARE SKIPPED:
  Real data rows have a genuine "HH:MM" time block. Summary rows have text
  like "Total (MWh)" sitting in the Hour column and an empty/missing Time
  Block cell -- so any row whose Time Block cell doesn't match the HH:MM
  regex is treated as a non-data row and skipped (counted, not silently
  dropped).

WHY ROWS ARE CLIPPED TO EACH FILE'S OWN "Date: X to Y" HEADER RANGE:
  Every one of the 12 DAM_2023 monthly files (checked directly, all 12) has
  an extra 96-row block appended at the very end for the exact day these
  files were bulk-downloaded (2026-08-04) -- IEX's export apparently tacks
  a "today's live snapshot" section onto every report regardless of which
  historical month was selected. Confirmed empirically: e.g.
  DAM_Market Snapshot (1).xlsx is headed "Date: 01-01-2023 to 31-01-2023"
  but its raw rows include a trailing 2026-08-04 block with values
  identical to the other 11 files' trailing blocks. Trusting these rows
  would silently duplicate one real day into ~12+ months of the dataset.
  Fix: parse each file's own header range and drop any row whose date
  falls outside it, rather than trusting every row the sheet contains.

DUPLICATE-MONTH DOWNLOADS ARE DETECTED, NOT SILENTLY MERGED:
  RTM_2023 and GDAM_2023 each have TWO files (their "(8)" and "(9)") that
  both declare "Date: 01-08-2023 to 31-08-2023" -- i.e. August 2023 was
  downloaded twice under different sequence numbers, which almost
  certainly means September 2023 was never downloaded for those two
  markets (a real gap, not a code bug). main() detects and reports any
  two files in the same run whose header date-range is identical.

USAGE:
  python iex_to_long.py --in-dir "IEX Data" --out iex_long.csv
  python iex_to_long.py --in-dir "IEX Data" --pattern "*/DAM*/*.xlsx" --out iex_long.csv
"""

import argparse
import csv
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from python_calamine import CalamineWorkbook

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")

# Canonical column -> list of name variants seen across DAM/RTM/GDAM/years.
# GDAM breaks sell/MCV/FSV down by generation source (Solar/Non-Solar/Hydro
# in most files, Hydro/Wind/OtherRE/DRE in the Aug-2026 files after IEX
# changed the report layout) -- the "Total ..." column is the one common
# aggregate figure that exists in every variant, so that's what maps to our
# canonical sell_bid/mcv/final_scheduled fields for GDAM.
COLUMN_ALIASES = {
    "date": ["Date"],
    "time_block": ["Time Block"],
    "purchase_bid": ["Purchase Bid (MW)"],
    "sell_bid": ["Sell Bid (MW)", "Total Sell Bid (MW)"],
    "mcv": ["MCV (MW)", "Total MCV (MW)"],
    "final_scheduled": ["Final Scheduled Volume (MW)", "Total FSV (MW)"],
    "mcp": ["MCP (Rs/MWh) *", "MCP (Rs/MWh)"],
}


def find_header_row(data: list[list]) -> int | None:
    for i, row in enumerate(data[:10]):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if "Date" in cells and "Hour" in cells:
            return i
    return None


def build_col_index(header_row: list) -> dict:
    cells = [str(c).strip() if c is not None else "" for c in header_row]
    col_index = {}
    for canon, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in cells:
                col_index[canon] = cells.index(alias)
                break
        else:
            raise ValueError(f"column '{canon}' (aliases {aliases}) not found in header {cells}")
    return col_index


def parse_date_ddmmyyyy(s: str) -> tuple[int, int, int]:
    d, m, y = str(s).strip().split("-")
    return int(y), int(m), int(d)


def parse_block_start(cell) -> tuple[int, int] | None:
    m = TIME_RE.search(str(cell))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


HEADER_RANGE_RE = re.compile(r"Date:\s*(\d{2}-\d{2}-\d{4})\s*to\s*(\d{2}-\d{2}-\d{4})")
HEADER_SINGLE_RE = re.compile(r"Date:\s*(\d{2}-\d{2}-\d{4})\s*$")


def find_declared_range(data: list[list]) -> tuple[datetime, datetime] | None:
    """Read the file's own 'Date: X to Y' banner (row 2 in every file seen)
    so rows outside that range -- e.g. an appended 'today' preview block --
    can be dropped rather than trusted.

    Some files (all 12 in IEX_2023/DAM, confirmed by inspection) have a
    single-date banner like 'Date: 04-08-2026' instead of a range -- these
    turned out to be mis-exported files that contain ONLY today's live
    snapshot, mislabeled as if they were e.g. January 2023. A single-date
    banner is treated as a one-day range so these still get value-checked
    against their own (narrow, honest) declared content rather than being
    trusted wholesale."""
    for row in data[:5]:
        if not row:
            continue
        cell = row[0]
        if not isinstance(cell, str):
            continue
        m = HEADER_RANGE_RE.search(cell)
        if m:
            y1, m1, d1 = parse_date_ddmmyyyy(m.group(1))
            y2, m2, d2 = parse_date_ddmmyyyy(m.group(2))
            return datetime(y1, m1, d1), datetime(y2, m2, d2, 23, 59, 59)
        m = HEADER_SINGLE_RE.search(cell)
        if m:
            y1, m1, d1 = parse_date_ddmmyyyy(m.group(1))
            return datetime(y1, m1, d1), datetime(y1, m1, d1, 23, 59, 59)
    return None


YEAR_FOLDER_RE = re.compile(r"IEX_(\d{4})")


def expected_year_from_path(path: Path) -> int | None:
    for part in path.parts:
        m = YEAR_FOLDER_RE.match(part)
        if m:
            return int(m.group(1))
    return None


def process_file(path: Path, market_type: str) -> tuple[list[tuple], int, int, tuple | None]:
    """Returns (rows, n_skipped_nondata, n_dropped_out_of_range, declared_range)."""
    wb = CalamineWorkbook.from_path(str(path))
    ws = wb.get_sheet_by_name(wb.sheet_names[0])
    data = ws.to_python()

    hi = find_header_row(data)
    if hi is None:
        raise ValueError(f"{path.name}: could not locate header row (no row with both 'Date' and 'Hour')")
    col_index = build_col_index(data[hi])
    declared_range = find_declared_range(data)

    # Whole-file sanity check: IEX_2023/DAM/*.xlsx (all 12 files, confirmed
    # by inspection) declare 'Date: 04-08-2026' -- a mis-export that
    # captured today's live snapshot instead of their labeled 2023 month.
    # If a file's own declared range doesn't fall in the year implied by
    # its folder, none of its content can be trusted as that year's data --
    # reject the whole file rather than salvage a technically-valid-but-
    # wrong-year row.
    expected_year = expected_year_from_path(path)
    if declared_range and expected_year and declared_range[0].year != expected_year:
        return [], 0, 0, declared_range  # caller reports this as a rejected file, not silently dropped

    rows = []
    n_skipped = 0
    n_out_of_range = 0
    for row in data[hi + 1 :]:
        block = parse_block_start(row[col_index["time_block"]]) if col_index["time_block"] < len(row) else None
        if block is None:
            n_skipped += 1  # daily Total/Max/Min/Avg summary row, or blank
            continue
        try:
            y, m, d = parse_date_ddmmyyyy(row[col_index["date"]])
            hh, mm = block
            ts = datetime(y, m, d, hh, mm)
            purchase = float(row[col_index["purchase_bid"]])
            sell = float(row[col_index["sell_bid"]])
            mcv = float(row[col_index["mcv"]])
            final_sched = float(row[col_index["final_scheduled"]])
            mcp = row[col_index["mcp"]]
            mcp = float(mcp) if mcp not in (None, "") else None
        except (ValueError, TypeError, IndexError):
            n_skipped += 1
            continue

        if declared_range and not (declared_range[0] <= ts <= declared_range[1]):
            n_out_of_range += 1  # e.g. the appended "today" preview block seen in every DAM_2023 file
            continue

        rows.append((ts, market_type, purchase, sell, mcv, final_sched, mcp, path.name))
    return rows, n_skipped, n_out_of_range, declared_range


def main():
    parser = argparse.ArgumentParser(description="Parse IEX Market Snapshot xlsx files into one long 15-min table")
    parser.add_argument("--in-dir", type=Path, required=True, help="root folder containing DAM/RTM/GDAM subfolders (searched recursively)")
    parser.add_argument("--pattern", default="**/*.xlsx")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()

    files = sorted(args.in_dir.glob(args.pattern))
    print(f"Found {len(files)} xlsx files matching '{args.pattern}' under {args.in_dir}")

    all_rows = []
    seen_ranges: dict[tuple, list[str]] = {}  # (market_type, declared_range) -> [filenames] -- catches duplicate downloads
    for path in files:
        name_lower = path.name.lower()
        if name_lower.startswith("gdam"):
            market_type = "GDAM"
        elif name_lower.startswith("dam"):
            market_type = "DAM"
        elif name_lower.startswith("rtm"):
            market_type = "RTM"
        else:
            print(f"SKIP {path.name}: can't infer market type from filename")
            continue

        try:
            rows, n_skipped, n_out_of_range, declared_range = process_file(path, market_type)
        except Exception as e:  # noqa: BLE001 - log and keep going across the batch
            print(f"ERROR: {path.name}: {e}")
            continue

        expected_year = expected_year_from_path(path)
        if not rows and declared_range and expected_year and declared_range[0].year != expected_year:
            print(f"REJECTED {path.name} [{market_type}]: declares '{declared_range[0].date()}' which is not in {expected_year} -- whole file discarded, not usable as {expected_year} data")
            continue

        all_rows.extend(rows)
        note = f" ({n_skipped} summary rows skipped"
        note += f", {n_out_of_range} out-of-range rows dropped" if n_out_of_range else ""
        note += ")"
        print(f"{path.name} [{market_type}]: {len(rows)} 15-min rows{note}")

        if declared_range:
            key = (market_type, declared_range)
            seen_ranges.setdefault(key, []).append(path.name)

    dupes = {k: v for k, v in seen_ranges.items() if len(v) > 1}
    if dupes:
        print(f"\n{len(dupes)} duplicate-month download(s) detected (two files declaring the same date range -- likely means a different month was never downloaded):")
        for (market_type, (start, end)), fnames in dupes.items():
            print(f"  {market_type} {start.date()} to {end.date()}: {fnames}")

    # Final safety net: if the exact same (market_type, timestamp) shows up
    # from more than one file -- e.g. all 12 IEX_2023/DAM files turned out
    # to be mislabeled duplicates of the same single live day, confirmed by
    # inspection -- keep one copy. Only collapses rows whose values agree
    # across every duplicate; if they disagree this is a real conflict and
    # every copy is kept + flagged so it doesn't get silently resolved.
    by_key: dict[tuple, list[tuple]] = {}
    for r in all_rows:
        by_key.setdefault((r[1], r[0]), []).append(r)
    deduped = []
    n_collapsed = 0
    n_conflicting = 0
    for key, group in by_key.items():
        if len(group) == 1:
            deduped.append(group[0])
            continue
        value_sets = {g[2:7] for g in group}
        if len(value_sets) == 1:
            deduped.append(group[0])
            n_collapsed += len(group) - 1
        else:
            deduped.extend(group)
            n_conflicting += 1
    if n_collapsed:
        print(f"\nCollapsed {n_collapsed} exact-duplicate (market_type, timestamp) row(s) from overlapping/mislabeled files (values matched, so safe to drop the extras).")
    if n_conflicting:
        print(f"WARNING: {n_conflicting} (market_type, timestamp) key(s) had duplicates with DIFFERING values -- all copies kept, needs manual review.")
    all_rows = deduped

    all_rows.sort(key=lambda r: (r[1], r[0]))

    mode = "a" if args.append else "w"
    write_header = not (args.append and args.out.exists())
    with open(args.out, mode, newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "timestamp", "market_type", "purchase_bid_mw", "sell_bid_mw",
                "mcv_mw", "final_scheduled_volume_mw", "mcp_rs_mwh", "source_file",
            ])
        for ts, mtype, purchase, sell, mcv, final_sched, mcp, fname in all_rows:
            writer.writerow([
                ts.strftime("%Y-%m-%d %H:%M:%S"), mtype,
                f"{purchase:.2f}", f"{sell:.2f}", f"{mcv:.2f}", f"{final_sched:.2f}",
                f"{mcp:.2f}" if mcp is not None else "", fname,
            ])

    print(f"\nWrote {len(all_rows)} rows -> {args.out}")


if __name__ == "__main__":
    sys.exit(main())
