"""
validate_weather.py

Sanity-checks weather_all_states.csv before merging it with demand data.
Checks: state/date coverage, missing values per column, and physically
plausible ranges for each variable (catches unit mistakes or API weirdness,
not just missing data).

USAGE:
  python validate_weather.py --in weather/weather_all_states.csv
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

# (min, max) plausible bounds for India. Generous on purpose -- this is a
# sanity check for gross errors (wrong units, API mix-ups), not a strict
# climatological filter.
PLAUSIBLE_RANGES = {
    "temperature_2m_max": (0, 55),
    "temperature_2m_min": (-10, 45),
    "temperature_2m_mean": (-5, 50),
    "apparent_temperature_mean": (-10, 55),
    "relative_humidity_2m_mean": (0, 100),
    "precipitation_sum": (0, 1000),
    "rain_sum": (0, 1000),
    "wind_speed_10m_max": (0, 250),
    "wind_gusts_10m_max": (0, 300),
    "cloud_cover_mean": (0, 100),
    "surface_pressure_mean": (700, 1100),
    "shortwave_radiation_sum": (0, 40),
    "et0_fao_evapotranspiration": (0, 20),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", type=Path, default=Path("weather/weather_all_states.csv"))
    args = parser.parse_args()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loaded {len(rows)} rows from {args.in_path}\n")

    states = sorted({r["state"] for r in rows})
    dates = sorted({r["date"] for r in rows})
    print(f"=== Coverage: {len(states)} states, {len(dates)} unique dates ({dates[0]} to {dates[-1]}) ===")

    counts = defaultdict(int)
    for r in rows:
        counts[r["state"]] += 1
    uneven = {s: c for s, c in counts.items() if c != len(dates)}
    if uneven:
        print(f"  States with a different row count than expected ({len(dates)}): {uneven}")
    else:
        print("  Every state has exactly the same number of rows. Clean.")
    print()

    # Missing / blank values per column.
    if rows:
        columns = [c for c in rows[0].keys() if c not in ("date", "state")]
        missing_counts = {c: 0 for c in columns}
        for r in rows:
            for c in columns:
                if r[c] is None or r[c].strip() == "" or r[c].lower() == "none":
                    missing_counts[c] += 1
        print("=== Missing values per column ===")
        any_missing = False
        for c, n in missing_counts.items():
            if n > 0:
                any_missing = True
                pct = 100 * n / len(rows)
                print(f"  {c}: {n} missing ({pct:.1f}%)")
        if not any_missing:
            print("  None -- every column fully populated.")
        print()

        # Plausibility check.
        print("=== Out-of-range values (possible unit/parsing errors) ===")
        any_bad = False
        for c in columns:
            if c not in PLAUSIBLE_RANGES:
                continue
            lo, hi = PLAUSIBLE_RANGES[c]
            bad = []
            for r in rows:
                v = r[c]
                if not v or v.lower() == "none":
                    continue
                try:
                    fv = float(v)
                except ValueError:
                    bad.append((r["date"], r["state"], v))
                    continue
                if fv < lo or fv > hi:
                    bad.append((r["date"], r["state"], fv))
            if bad:
                any_bad = True
                print(f"  {c} (expected {lo} to {hi}): {len(bad)} out-of-range value(s), e.g. {bad[:5]}")
        if not any_bad:
            print("  None -- every value in every checked column is within a plausible range.")


if __name__ == "__main__":
    main()
