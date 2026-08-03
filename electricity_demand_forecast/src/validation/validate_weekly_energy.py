"""
validate_weekly_energy.py

Sanity-checks weekly_energy_long.csv before it becomes an input to anything
else. Three things this checks, and why each matters:

1. Duplicate (date, state) rows. Weekly report windows shouldn't overlap,
   but if two source PDFs ever do cover the same date for the same state,
   you'd silently get two different energy_mu values for the same row when
   this gets merged with weather data later -- better to catch and decide
   how to resolve it now (keep first, average, flag) than discover it as a
   confusing bug three steps downstream.
2. Which of the ~34 expected states/UTs never appeared. The extractor found
   32 -- knowing exactly which 2 are missing (report never lists them vs.
   name variant not in the alias table) tells us whether that's expected or
   a fixable gap.
3. Value sanity: negative or implausibly large energy_mu values, which would
   indicate a parsing error rather than a real reading.

USAGE:
  python validate_weekly_energy.py --in weekly_energy_long.csv
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

EXPECTED_STATES = {
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal", "Delhi", "Puducherry", "Jammu and Kashmir", "Ladakh",
    "Chandigarh",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", type=Path, default=Path("weekly_energy_long.csv"))
    args = parser.parse_args()

    rows = []
    with open(args.in_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    print(f"Loaded {len(rows)} rows from {args.in_path}\n")

    # 1. Duplicate (date, state) check.
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r["date"], r["state"])].append(r)
    dupes = {k: v for k, v in by_key.items() if len(v) > 1}
    print(f"=== Duplicate (date, state) rows: {len(dupes)} ===")
    if dupes:
        conflicting = 0
        for (date, state), group in list(dupes.items())[:20]:
            values = {g["energy_mu"] for g in group}
            sources = [g["source_file"] for g in group]
            flag = "CONFLICTING VALUES" if len(values) > 1 else "same value, harmless duplicate"
            if len(values) > 1:
                conflicting += 1
            print(f"  {date} / {state}: {[g['energy_mu'] for g in group]} from {sources}  [{flag}]")
        if len(dupes) > 20:
            print(f"  ... and {len(dupes) - 20} more")
        print(f"  Of {len(dupes)} duplicated keys shown, watch for how many say CONFLICTING VALUES above.")
    else:
        print("  None -- every (date, state) pair is unique. Clean.")
    print()

    # 2. Missing expected states.
    seen_states = {r["state"] for r in rows}
    missing = EXPECTED_STATES - seen_states
    extra = seen_states - EXPECTED_STATES
    print(f"=== State coverage: {len(seen_states)} distinct states/UTs found ===")
    if missing:
        print(f"  Expected but never matched: {sorted(missing)}")
    else:
        print("  All expected states/UTs are present.")
    if extra:
        print(f"  Present but not in the expected list (double check these): {sorted(extra)}")
    print()

    # 3. Value sanity.
    bad = []
    for r in rows:
        try:
            v = float(r["energy_mu"])
        except ValueError:
            bad.append((r, "not a number"))
            continue
        if v < 0:
            bad.append((r, f"negative value: {v}"))
        elif v > 3000:
            bad.append((r, f"implausibly large for a single state-day: {v}"))
    print(f"=== Value sanity: {len(bad)} suspicious row(s) ===")
    for r, reason in bad[:20]:
        print(f"  {r['date']} / {r['state']}: {reason} (source: {r['source_file']})")
    if len(bad) > 20:
        print(f"  ... and {len(bad) - 20} more")

    # 4. Date range coverage.
    dates = sorted({r["date"] for r in rows})
    if dates:
        print(f"\n=== Date range: {dates[0]} to {dates[-1]} ({len(dates)} unique dates) ===")


if __name__ == "__main__":
    main()
