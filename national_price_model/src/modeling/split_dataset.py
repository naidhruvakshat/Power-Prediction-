"""
split_dataset.py

Splits national_price_dataset_features.csv into train/validation/test by
DATE, not by randomly sampling rows. This is a time-series dataset -- a
random row split would let the model train on rows from the same day (even
the same hour) it's tested on, which leaks future information back into
training and makes the evaluation meaningless. The standard approach for
time series is a chronological holdout: train on the oldest data, validate
on the next slice, test on the most recent slice.

Cutoffs are fixed calendar dates (not recomputed per run) so every rerun
produces the exact same split -- 70% train / 15% validation / 15% test by
day-count across the full 2022-04-01 to 2025-06-24 span:
  train:      2022-04-01 to 2024-07-04
  validation: 2024-07-05 to 2024-12-28
  test:       2024-12-29 to 2025-06-24

Splits are applied the same way across all three market_types (DAM/RTM/
GDAM) -- each split file contains all three markets, just restricted to
that date window.

USAGE:
  python split_dataset.py --in national_price_dataset_features.csv --out-dir splits/
"""

import argparse
import csv
from pathlib import Path

TRAIN_END = "2024-07-05"       # exclusive
VALIDATION_END = "2024-12-29"  # exclusive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    with open(args.in_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    print(f"Loaded {len(rows)} rows")

    train, validation, test = [], [], []
    for r in rows:
        date_str = r["timestamp"][:10]
        if date_str < TRAIN_END:
            train.append(r)
        elif date_str < VALIDATION_END:
            validation.append(r)
        else:
            test.append(r)

    for name, split in [("train", train), ("validation", validation), ("test", test)]:
        out_path = args.out_dir / f"{name}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(fieldnames)
            writer.writerows([r[c] for c in fieldnames] for r in split)

        by_market = {}
        for r in split:
            by_market[r["market_type"]] = by_market.get(r["market_type"], 0) + 1
        dates = sorted(r["timestamp"] for r in split)
        date_range = f"{dates[0]} to {dates[-1]}" if dates else "empty"
        print(f"{name}: {len(split)} rows ({date_range}) -> {out_path}")
        print(f"  by market: {by_market}")


if __name__ == "__main__":
    main()
