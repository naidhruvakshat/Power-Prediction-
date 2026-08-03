"""
generate_holidays.py

Generates a (date, state) holiday flag table for every state in the demand
dataset, covering both national holidays (e.g. Republic Day, Independence
Day -- observed everywhere) and state-specific holidays (e.g. Chhatrapati
Shivaji Maharaj Jayanti in Maharashtra, Pongal in Tamil Nadu).

WHY THE `holidays` PYTHON LIBRARY INSTEAD OF SCRAPING:
  Each Indian state government publishes its own annual holiday notification
  as a separate PDF/webpage -- scraping 32 different, inconsistently
  formatted government sites would repeat the entire Grid India ordeal
  32 times over. The `holidays` package (pip install holidays) computes
  Indian national and state-subdivision holidays algorithmically/from
  maintained calendar data, entirely offline, with explicit per-state
  subdivision support that maps directly onto our state list.

CAVEATS WORTH KNOWING:
  - Fixed-date holidays (Republic Day, Independence Day, Gandhi Jayanti) are
    exact every year.
  - Lunar/lunisolar-calendar holidays (Diwali, Holi, Eid, Ram Navami, etc.)
    are computed astronomically for future years. This is standard practice
    and generally correct, but the exact day can occasionally shift by one
    depending on regional moon-sighting conventions (this mainly affects
    Islamic holidays like Eid) -- worth a spot check against an official
    calendar if a specific date matters a lot to your analysis.
  - "State holiday" here means what the library tracks as that state's
    gazetted/restricted holidays; it may not be perfectly exhaustive for
    every state's every local observance, but covers the major ones that
    would plausibly affect electricity demand (offices/industry closed).

OUTPUT: data/processed/holidays_long.csv with columns:
  date, state, is_national_holiday, is_state_holiday, holiday_name

USAGE:
  python generate_holidays.py --start 2024-04-01 --end 2026-04-05 \
      --out ../../data/processed/holidays_long.csv
"""

import argparse
import csv
from datetime import date
from pathlib import Path

import holidays

STATE_TO_SUBDIV = {
    "Andhra Pradesh": "AP",
    "Arunachal Pradesh": "AR",
    "Assam": "AS",
    "Bihar": "BR",
    "Chhattisgarh": "CG",
    "Goa": "GA",
    "Gujarat": "GJ",
    "Haryana": "HR",
    "Himachal Pradesh": "HP",
    "Jharkhand": "JH",
    "Karnataka": "KA",
    "Kerala": "KL",
    "Madhya Pradesh": "MP",
    "Maharashtra": "MH",
    "Manipur": "MN",
    "Meghalaya": "ML",
    "Mizoram": "MZ",
    "Nagaland": "NL",
    "Odisha": "OD",
    "Punjab": "PB",
    "Rajasthan": "RJ",
    "Sikkim": "SK",
    "Tamil Nadu": "TN",
    "Telangana": "TS",
    "Tripura": "TR",
    "Uttar Pradesh": "UP",
    "Uttarakhand": "UK",
    "West Bengal": "WB",
    "Delhi": "DL",
    "Puducherry": "PY",
    "Jammu and Kashmir": "JK",
    "Chandigarh": "CH",
}


def main():
    parser = argparse.ArgumentParser(description="Generate national + state holiday flags for every state/date")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out", type=Path, default=Path("holidays_long.csv"))
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    years = list(range(start.year, end.year + 1))

    # National-only calendar, used to distinguish "national" from "state" below.
    national = holidays.India(years=years)

    rows = []
    for state, subdiv in sorted(STATE_TO_SUBDIV.items()):
        state_cal = holidays.India(years=years, subdiv=subdiv)
        d = start
        while d <= end:
            name = state_cal.get(d)
            if name:
                is_national = d in national
                rows.append((d.isoformat(), state, int(is_national), int(not is_national), name))
            d = date.fromordinal(d.toordinal() + 1)

    rows.sort(key=lambda r: (r[1], r[0]))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "state", "is_national_holiday", "is_state_holiday", "holiday_name"])
        writer.writerows(rows)

    n_national = sum(1 for r in rows if r[2])
    n_state = sum(1 for r in rows if r[3])
    print(f"Wrote {len(rows)} holiday rows -> {args.out}")
    print(f"  ({n_national} national-holiday rows, {n_state} state-specific-holiday rows, across {len(STATE_TO_SUBDIV)} states)")


if __name__ == "__main__":
    main()
