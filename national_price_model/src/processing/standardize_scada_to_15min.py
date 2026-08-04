"""
standardize_scada_to_15min.py

Reads every raw SCADA/net-load xlsx file (whatever native resolution it
happens to be in) and standardizes it into one common 15-minute-block table:
  timestamp, demand_mw, wind_mw, solar_mw, net_load_mw,
  source_resolution_min, is_interpolated, source_file

THE GENERAL RULE (this is deliberately resolution-agnostic, not per-file
special-cased, per instruction from the team):
  - Native resolution FINER than 15 min (e.g. 5-min, 10-sec) -> AVERAGE DOWN.
    Every native reading in a 15-minute window is averaged into one block.
    This never fabricates information -- it's a straightforward mean of
    real readings that already happened inside that window.
  - Native resolution COARSER than 15 min (e.g. hourly) -> LINEAR
    INTERPOLATE UP. The three intermediate 15-min points between two native
    readings are filled by a straight line between them. This assumes the
    native reading is an instantaneous snapshot, not an hourly average --
    see the caveat in the module docstring below and in the README.
  - Native resolution EXACTLY 15 min -> pass through unchanged.

  Resolution is measured empirically (median gap between consecutive
  timestamps in the file), not guessed from the filename or era -- so this
  keeps working correctly even if a future file turns out to be some other
  resolution entirely.

OPEN CAVEAT (documented, not silently assumed away):
  The Jan2024-Jun2025 combined file's hourly figures might be an
  instantaneous reading at the top of the hour, or an hourly average. We
  don't have an overlapping window against finer-grained data to test this
  empirically (the fine-grained files stop in Dec 2023, this one starts Jan
  2024). Per team guidance, we default to linear interpolation (the correct
  choice if instantaneous, and a low-stakes approximation either way since
  the 15-min price lags carry the real sub-hour signal). The untouched
  original hourly rows are preserved in the output as
  is_interpolated=0 rows -- nothing is overwritten, so this can be revisited.

TWO SOURCE COLUMN LAYOUTS HANDLED:
  Era A (Sept 2021 - Dec 2023 monthly files, "Sheet1"):
    raw SCADA tag columns -- Time, NLDC_DEMAND|P, ALL_INDIA_WIND|P,
    ALL_IND_SOLAR|P (Total is a derived sum, not used directly here).
  Era B (Jan2024-Jun2025 combined file, "Report" sheet):
    already-clean columns -- Timestamp, Demand (MW), Wind (MW), Solar (MW).

USAGE:
  python standardize_scada_to_15min.py --in-dir raw/scada --out combined_15min_netload.csv
"""

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median

from python_calamine import CalamineWorkbook

ERA_A_SHEET = "Sheet1"
ERA_B_SHEET = "Report"

ERA_A_COLS = {
    "demand": "NLDC_DEMAND|P",
    "wind": "ALL_INDIA_WIND|P",
    "solar": "ALL_IND_SOLAR|P",
}
ERA_B_COLS = {
    "demand": "Demand (MW)",
    "wind": "Wind (MW)",
    "solar": "Solar (MW)",
}


def normalise_timestamp(raw) -> datetime:
    """Calamine returns a bare datetime.date (not datetime.datetime) for
    any cell that happens to land exactly on midnight -- Excel doesn't
    distinguish '2023-01-01' from '2023-01-01 00:00:00' internally, and
    calamine's type inference collapses the former to a date. Without this,
    every midnight row would crash the (t1 - t0) arithmetic downstream."""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day)
    return datetime.strptime(str(raw).strip(), "%d-%m-%Y %H:%M:%S")


def load_era_a(path: Path, sheet) -> list[tuple[datetime, float, float, float]]:
    """Sept2021-Dec2023 monthly files: Sheet1, raw SCADA tag columns."""
    data = sheet.to_python()
    tag_header, name_header = data[0], data[1]
    col_index = {name: i for i, name in enumerate(name_header) if name}

    for key, col_name in ERA_A_COLS.items():
        if col_name not in col_index:
            raise ValueError(f"{path.name}: expected column '{col_name}' not found in header {name_header}")

    di, wi, si = col_index["NLDC_DEMAND|P"], col_index["ALL_INDIA_WIND|P"], col_index["ALL_IND_SOLAR|P"]

    out = []
    for row in data[2:]:
        ts = row[0]
        if ts is None or ts == "":
            continue
        demand, wind, solar = row[di], row[wi], row[si]
        if demand is None or demand == "":
            continue
        out.append((normalise_timestamp(ts), float(demand), float(wind or 0.0), float(solar or 0.0)))
    return out


def load_era_b(path: Path, sheet) -> list[tuple[datetime, float, float, float]]:
    """Jan2024-Jun2025 combined file: 'Report' sheet, already-clean columns."""
    data = sheet.to_python()
    header = data[0]
    col_index = {name: i for i, name in enumerate(header) if name}
    for key, col_name in ERA_B_COLS.items():
        if col_name not in col_index:
            raise ValueError(f"{path.name}: expected column '{col_name}' not found in header {header}")

    ti = col_index["Timestamp"]
    di, wi, si = col_index["Demand (MW)"], col_index["Wind (MW)"], col_index["Solar (MW)"]

    out = []
    n_skipped = 0
    for row in data[1:]:
        raw_ts = row[ti]
        if raw_ts is None or raw_ts == "":
            continue
        # Trailing summary rows (Minimum/Maximum/Average/Sum + their own
        # "Timestamp"-labelled rows showing when each extreme occurred) sit
        # below the real data with garbage in this column -- skip anything
        # that doesn't parse as a real timestamp rather than crash the file.
        try:
            ts = normalise_timestamp(raw_ts)
            demand, wind, solar = float(row[di]), float(row[wi] or 0.0), float(row[si] or 0.0)
        except (ValueError, TypeError):
            n_skipped += 1
            continue
        out.append((ts, demand, wind, solar))
    if n_skipped:
        print(f"  ({path.name}: skipped {n_skipped} trailing summary row(s) -- Minimum/Maximum/Average/Sum footer)")
    return out


def load_file(path: Path) -> list[tuple[datetime, float, float, float]]:
    wb = CalamineWorkbook.from_path(str(path))
    if ERA_B_SHEET in wb.sheet_names:
        return load_era_b(path, wb.get_sheet_by_name(ERA_B_SHEET))
    if ERA_A_SHEET in wb.sheet_names:
        return load_era_a(path, wb.get_sheet_by_name(ERA_A_SHEET))
    raise ValueError(f"{path.name}: no recognised sheet ('{ERA_A_SHEET}' or '{ERA_B_SHEET}') among {wb.sheet_names}")


def detect_resolution_minutes(rows: list[tuple]) -> float:
    """Median gap between consecutive timestamps, in minutes -- measured
    empirically rather than assumed from filename/era, so this keeps
    working even if a file's real resolution surprises us."""
    times = sorted(r[0] for r in rows)
    gaps = [(times[i + 1] - times[i]).total_seconds() / 60.0 for i in range(len(times) - 1)]
    gaps = [g for g in gaps if g > 0]
    return median(gaps) if gaps else 15.0


def floor_to_15(ts: datetime) -> datetime:
    minute_block = (ts.minute // 15) * 15
    return ts.replace(minute=minute_block, second=0, microsecond=0)


def average_down(rows: list[tuple]) -> list[tuple]:
    """Native resolution finer than 15 min -> mean of every real reading
    that falls inside each 15-minute window."""
    buckets: dict[datetime, list[tuple]] = {}
    for ts, d, w, s in rows:
        key = floor_to_15(ts)
        buckets.setdefault(key, []).append((d, w, s))

    out = []
    for key in sorted(buckets):
        vals = buckets[key]
        n = len(vals)
        d_avg = sum(v[0] for v in vals) / n
        w_avg = sum(v[1] for v in vals) / n
        s_avg = sum(v[2] for v in vals) / n
        out.append((key, d_avg, w_avg, s_avg, 0))  # is_interpolated = 0 (real average)
    return out


def interpolate_up(rows: list[tuple]) -> list[tuple]:
    """Native resolution coarser than 15 min -> linear interpolation between
    consecutive native points to fill the intermediate 15-min slots."""
    rows = sorted(rows, key=lambda r: r[0])
    out = []
    for i in range(len(rows) - 1):
        t0, d0, w0, s0 = rows[i]
        t1, d1, w1, s1 = rows[i + 1]
        span_s = (t1 - t0).total_seconds()
        out.append((t0, d0, w0, s0, 0))  # original point, not interpolated
        t = t0 + timedelta(minutes=15)
        while t < t1:
            frac = (t - t0).total_seconds() / span_s
            d = d0 + (d1 - d0) * frac
            w = w0 + (w1 - w0) * frac
            s = s0 + (s1 - s0) * frac
            out.append((t, d, w, s, 1))  # interpolated
            t += timedelta(minutes=15)
    # last native point
    out.append((rows[-1][0], rows[-1][1], rows[-1][2], rows[-1][3], 0))
    return out


def standardize_file(path: Path) -> tuple[list[tuple], float]:
    raw_rows = load_file(path)
    if not raw_rows:
        return [], 0.0
    res_min = detect_resolution_minutes(raw_rows)

    if res_min < 14.9:
        standardized = average_down(raw_rows)
    elif res_min > 15.1:
        standardized = interpolate_up(raw_rows)
    else:
        standardized = [(ts, d, w, s, 0) for ts, d, w, s in sorted(raw_rows, key=lambda r: r[0])]

    return standardized, res_min


def main():
    parser = argparse.ArgumentParser(description="Standardize SCADA net-load files of any resolution to 15-min blocks")
    parser.add_argument("--in-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pattern", default="*.xlsx", help="glob pattern to select a subset of files (for chunked runs)")
    parser.add_argument("--append", action="store_true", help="append to --out instead of overwriting (no header row written)")
    args = parser.parse_args()

    files = sorted(args.in_dir.glob(args.pattern))
    print(f"Found {len(files)} xlsx files in {args.in_dir} matching '{args.pattern}'")

    all_rows = []
    for path in files:
        try:
            standardized, res_min = standardize_file(path)
        except Exception as e:  # noqa: BLE001 - log and keep going across the batch
            print(f"ERROR: {path.name}: {e}")
            continue

        for ts, d, w, s, is_interp in standardized:
            net_load = d - w - s
            all_rows.append((ts, d, w, s, net_load, res_min, is_interp, path.name))

        action = "averaged down" if res_min < 14.9 else ("interpolated up" if res_min > 15.1 else "passed through")
        print(f"{path.name}: native ~{res_min:.2f} min -> {action} -> {len(standardized)} 15-min rows")

    all_rows.sort(key=lambda r: r[0])

    mode = "a" if args.append else "w"
    write_header = not (args.append and args.out.exists())
    with open(args.out, mode, newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "timestamp", "demand_mw", "wind_mw", "solar_mw", "net_load_mw",
                "source_resolution_min", "is_interpolated", "source_file",
            ])
        for ts, d, w, s, nl, res_min, is_interp, fname in all_rows:
            writer.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), f"{d:.3f}", f"{w:.3f}", f"{s:.3f}", f"{nl:.3f}", f"{res_min:.2f}", is_interp, fname])

    print(f"\nWrote {len(all_rows)} rows -> {args.out}")
    if all_rows:
        n_interp = sum(1 for r in all_rows if r[6] == 1)
        dates = sorted({r[0].date() for r in all_rows})
        print(f"  {n_interp} rows ({n_interp/len(all_rows)*100:.1f}%) are interpolated (from the hourly era)")
        print(f"  Date range: {dates[0]} to {dates[-1]}")


if __name__ == "__main__":
    sys.exit(main())
