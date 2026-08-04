"""
join_netload_price.py

Joins the standardized 15-min net-load table (combined_15min_netload.csv,
from standardize_scada_to_15min.py) onto the IEX price table (iex_long.csv,
from iex_to_long.py) on timestamp, separately for each market_type (DAM,
RTM, GDAM never share rows with each other -- per the "market_type
grouping" rule, they must never be merged into one price series).

WHY INNER JOIN FROM THE PRICE SIDE:
  Price is what we're ultimately predicting; a price row with no matching
  net-load value can't be used for training a model that needs net_load as
  a feature. Net-load-only rows (outside the IEX date range) are simply not
  relevant yet. Unmatched counts are reported per market so a real gap is
  visible, not silently dropped.

USAGE:
  python join_netload_price.py \
      --netload combined_15min_netload.csv --price iex_long.csv \
      --out national_price_dataset.csv
"""

import argparse
import csv
from pathlib import Path


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--netload", type=Path, required=True)
    parser.add_argument("--price", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    netload_rows = load_csv(args.netload)
    price_rows = load_csv(args.price)
    print(f"Loaded {len(netload_rows)} net-load rows, {len(price_rows)} price rows")

    netload_by_ts = {r["timestamp"]: r for r in netload_rows}

    merged = []
    unmatched_by_market: dict[str, int] = {}
    for p in price_rows:
        nl = netload_by_ts.get(p["timestamp"])
        if nl is None:
            unmatched_by_market[p["market_type"]] = unmatched_by_market.get(p["market_type"], 0) + 1
            continue
        merged.append({
            "timestamp": p["timestamp"],
            "market_type": p["market_type"],
            "purchase_bid_mw": p["purchase_bid_mw"],
            "sell_bid_mw": p["sell_bid_mw"],
            "mcv_mw": p["mcv_mw"],
            "final_scheduled_volume_mw": p["final_scheduled_volume_mw"],
            "mcp_rs_mwh": p["mcp_rs_mwh"],
            "demand_mw": nl["demand_mw"],
            "wind_mw": nl["wind_mw"],
            "solar_mw": nl["solar_mw"],
            "net_load_mw": nl["net_load_mw"],
            "netload_is_interpolated": nl["is_interpolated"],
            "netload_source_resolution_min": nl["source_resolution_min"],
            "price_source_file": p["source_file"],
        })

    # market_type-first, timestamp-second -- keeps each market's series
    # contiguous, which is exactly the grouping the "never share lags across
    # market_type" modelling rule needs downstream.
    merged.sort(key=lambda r: (r["market_type"], r["timestamp"]))

    fieldnames = list(merged[0].keys()) if merged else []
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged)

    print(f"\nWrote {len(merged)} joined rows -> {args.out}")
    if unmatched_by_market:
        print(f"Price rows with NO net-load match (dropped, outside net-load coverage or a documented gap): {unmatched_by_market}")

    for market in sorted({r["market_type"] for r in merged}):
        sub = [r for r in merged if r["market_type"] == market]
        dates = sorted(r["timestamp"] for r in sub)
        print(f"  {market}: {len(sub)} rows, {dates[0]} to {dates[-1]}")


if __name__ == "__main__":
    main()
