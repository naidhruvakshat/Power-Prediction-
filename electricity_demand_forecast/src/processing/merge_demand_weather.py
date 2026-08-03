"""
merge_demand_weather.py

Joins weekly_energy_long.csv (demand, from Grid India) with
weather_all_states.csv (weather, from Open-Meteo) on (date, state) into one
modeling-ready table.

WHY INNER JOIN FROM THE DEMAND SIDE:
  Demand is the prediction target -- a row with weather but no demand value
  is useless for training, so there's no reason to keep it. We report how
  many demand rows DIDN'T find a weather match (should be ~0, since weather
  was fetched for a superset of demand states) so a real gap is visible
  rather than silently dropped.

USAGE:
  python merge_demand_weather.py \
      --demand weekly_energy_long.csv \
      --weather weather/weather_all_states.csv \
      --out modeling_dataset.csv
"""

import argparse
import csv
from pathlib import Path


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demand", type=Path, default=Path("weekly_energy_long.csv"))
    parser.add_argument("--weather", type=Path, default=Path("weather/weather_all_states.csv"))
    parser.add_argument("--out", type=Path, default=Path("modeling_dataset.csv"))
    args = parser.parse_args()

    demand_rows = load_csv(args.demand)
    weather_rows = load_csv(args.weather)
    print(f"Loaded {len(demand_rows)} demand rows, {len(weather_rows)} weather rows")

    weather_by_key = {(r["date"], r["state"]): r for r in weather_rows}
    weather_cols = [c for c in weather_rows[0].keys() if c not in ("date", "state")] if weather_rows else []

    merged = []
    unmatched = []
    for d in demand_rows:
        key = (d["date"], d["state"])
        w = weather_by_key.get(key)
        if w is None:
            unmatched.append(d)
            continue
        row = {
            "date": d["date"],
            "state": d["state"],
            "energy_mu": d["energy_mu"],
        }
        for c in weather_cols:
            row[c] = w[c]
        merged.append(row)

    out_cols = ["date", "state", "energy_mu"] + weather_cols
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_cols)
        writer.writeheader()
        writer.writerows(merged)

    print(f"\nMerged {len(merged)} rows -> {args.out}")
    if unmatched:
        by_state = {}
        for d in unmatched:
            by_state[d["state"]] = by_state.get(d["state"], 0) + 1
        print(f"\n{len(unmatched)} demand row(s) had NO weather match (dropped) -- by state: {by_state}")
        print("If this list is non-empty, check that state names match exactly between the two files.")
    else:
        print("Every demand row found a matching weather row. Clean join.")

    states = sorted({r["state"] for r in merged})
    dates = sorted({r["date"] for r in merged})
    print(f"\nFinal dataset: {len(states)} states, {len(dates)} dates ({dates[0]} to {dates[-1]}), {len(merged)} rows")


if __name__ == "__main__":
    main()
