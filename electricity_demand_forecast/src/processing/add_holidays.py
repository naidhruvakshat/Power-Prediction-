"""
add_holidays.py

Joins holidays_long.csv onto modeling_dataset.csv (on date + state), adding
is_national_holiday, is_state_holiday, and holiday_name columns. Dates with
no holiday get 0/0/"" (not a missing value -- most days genuinely aren't
holidays).

USAGE:
  python add_holidays.py \
      --dataset ../../data/processed/modeling_dataset.csv \
      --holidays ../../data/processed/holidays_long.csv \
      --out ../../data/processed/modeling_dataset.csv
"""

import argparse
import csv
from pathlib import Path


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--holidays", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    dataset_rows = load_csv(args.dataset)
    holiday_rows = load_csv(args.holidays)
    holiday_by_key = {(r["date"], r["state"]): r for r in holiday_rows}

    print(f"Loaded {len(dataset_rows)} dataset rows, {len(holiday_rows)} holiday rows")

    out_rows = []
    for r in dataset_rows:
        h = holiday_by_key.get((r["date"], r["state"]))
        new_row = dict(r)
        new_row["is_national_holiday"] = h["is_national_holiday"] if h else "0"
        new_row["is_state_holiday"] = h["is_state_holiday"] if h else "0"
        new_row["holiday_name"] = h["holiday_name"] if h else ""
        out_rows.append(new_row)

    out_rows.sort(key=lambda r: (r["state"], r["date"]))

    fieldnames = list(dataset_rows[0].keys()) + ["is_national_holiday", "is_state_holiday", "holiday_name"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    n_holidays = sum(1 for r in out_rows if r["is_national_holiday"] == "1" or r["is_state_holiday"] == "1")
    print(f"Wrote {len(out_rows)} rows -> {args.out}")
    print(f"  {n_holidays} rows flagged as a holiday ({n_holidays / len(out_rows) * 100:.1f}%)")


if __name__ == "__main__":
    main()
