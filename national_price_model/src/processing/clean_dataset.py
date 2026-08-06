"""
clean_dataset.py

Cleans national_price_dataset.csv before feature engineering. NOT a blanket
"negatives -> 0" pass -- checked every numeric column first (see README)
and only one actually needed it:

  solar_mw: 14,297 rows (4.9%) have small negative values (e.g. -10 MW at
    midnight) -- sensor/calibration noise, since solar generation can't
    physically be negative. Clipped to 0.

Every other column (purchase_bid_mw, sell_bid_mw, mcv_mw,
final_scheduled_volume_mw, mcp_rs_mwh, demand_mw, wind_mw, net_load_mw) had
ZERO negative rows in the raw data -- checked programmatically, not
assumed. In particular mcp_rs_mwh and net_load_mw CAN legitimately go
negative in a real grid (negative prices during oversupply, negative net
load when renewables exceed demand) so they are deliberately NOT touched
even though a naive "clip all negatives" rule would have mangled them if
they ever occurred.

net_load_mw is RECOMPUTED after cleaning solar (net_load = demand - wind -
solar), since the original value was computed from the noisy raw solar
figure -- a negative solar reading was being subtracted-as-negative,
i.e. silently adding a few MW to net_load. Small effect, but the two
columns need to stay internally consistent with each other.

USAGE:
  python clean_dataset.py --in national_price_dataset.csv --out national_price_dataset_clean.csv
"""

import argparse
import csv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    print(f"Loaded {len(rows)} rows")

    n_solar_clipped = 0
    for r in rows:
        solar = float(r["solar_mw"])
        if solar < 0:
            solar = 0.0
            n_solar_clipped += 1
        wind = float(r["wind_mw"])
        demand = float(r["demand_mw"])

        r["solar_mw"] = f"{solar:.3f}"
        r["net_load_mw"] = f"{demand - wind - solar:.3f}"

    print(f"Clipped {n_solar_clipped} negative solar_mw rows to 0, recomputed net_load_mw for all rows")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        writer.writerows([r[c] for c in fieldnames] for r in rows)

    print(f"Wrote {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
