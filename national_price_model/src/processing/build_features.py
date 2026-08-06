"""
build_features.py

Adds the first round of model-ready features to national_price_dataset.csv:
  - cyclical time encodings (15-min block, day-of-week, month)
  - is_weekend, is_national_holiday
  - price-cap context: the REGULATORY CAP CHANGED OVER TIME, so this is a
    lookup by date, not a fixed 10000 threshold (see below)
  - lag features for price and net load, computed separately per
    market_type so DAM/RTM/GDAM never leak into each other's lags
  - a demand-weighted national daily weather feature (partial coverage --
    see caveat below)

WHY THE PRICE CAP IS A DATE-BASED LOOKUP, NOT A FIXED 10000:
  Checking our own data empirically (max MCP per month, per day) shows the
  regulatory ceiling itself moved over the dataset's span:
    2022-04-01 to 2022-04-06: Rs 20,000/MWh (old cap, still in effect the
      first few days of the dataset while exchanges rolled out the new
      bidding software)
    2022-04-07 to 2023-04-03: Rs 12,000/MWh (CERC order, April 2022)
    2023-04-04 onwards:       Rs 10,000/MWh (CERC order 04/SM/2023)
  This matches CERC's public order history and our own data's observed
  monthly maximums (2022 tops out at 12000, 2024-2025 tops out at 10000).
  A fixed "mcp == 10000" flag would silently misclassify every capped row
  from 2022 through early 2023 as "not at cap" -- treating a real price
  ceiling as if it were just a very high but uncapped price. is_at_price_cap
  is computed against the cap actually in force on that date instead.

WHY LAGS ARE COMPUTED PER MARKET_TYPE:
  DAM, RTM, and GDAM are different markets that clear at different times
  with different dynamics. A "previous block" lag that crossed from an RTM
  row into a DAM row would be comparing unrelated series. Each market's
  rows are sorted by timestamp and shifted independently.

WHY LAGS ARE LOOKED UP BY EXACT TIMESTAMP, NOT BY COUNTING ROWS BACK:
  The first version of this script computed "1 week ago" by walking back
  96*7 POSITIONS in the sorted list -- which is only correct if the series
  has no gaps. It doesn't: DAM is missing all of 2023, and RTM/GDAM are
  each missing a month. For the rows right after a gap, counting back a
  fixed number of positions lands on a real row, but one from over a year
  before the gap -- silently mislabeled as "last week." Fixed by computing
  the actual target timestamp (current time minus 15 min / 1 day / 1 week)
  and looking it up directly; if that exact timestamp isn't in the data,
  the lag is left blank rather than substituting a wrong value.

WEATHER COVERAGE CAVEAT (documented, not silently patched):
  The price dataset spans 2022-04-01 to 2025-06-24. The weather data we
  have (from the demand-forecast project) only covers 2024-04-01 to
  2026-04-05. So the national weather feature is NULL for everything
  before 2024-04-01 -- roughly the first two years of the price dataset.
  Extending weather back to 2022 needs a fresh Open-Meteo pull (blocked
  from this sandbox, same as before -- would need to run on your machine).
  The national weather value itself is demand-weighted (each state's
  temperature weighted by its share of national energy_mu) rather than a
  plain average, so populous/high-demand states like Maharashtra or UP
  don't get diluted by low-demand states like Sikkim.

USAGE:
  python build_features.py --in national_price_dataset.csv --out national_price_dataset_features.csv
"""

import argparse
import csv
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import holidays

# Empirically verified against our own data (max MCP per month/day) and
# CERC's public order history. Each tuple is (effective_from_date, cap_rs_mwh).
PRICE_CAP_REGIME = [
    (date(2022, 4, 1), 20000.0),
    (date(2022, 4, 7), 12000.0),
    (date(2023, 4, 4), 10000.0),
]


def price_cap_for_date(d: date) -> float:
    cap = PRICE_CAP_REGIME[0][1]
    for effective_from, value in PRICE_CAP_REGIME:
        if d >= effective_from:
            cap = value
        else:
            break
    return cap


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_national_weather(modeling_dataset_path: Path) -> dict[str, dict]:
    """Demand-weighted national daily weather: each state's reading is
    weighted by its share of that day's total energy_mu, so the 'national'
    number reflects where the load actually is, not a flat state average."""
    rows = load_csv(modeling_dataset_path)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)

    weather_cols = ["temperature_2m_mean", "relative_humidity_2m_mean", "wind_speed_10m_max", "cloud_cover_mean"]
    out = {}
    for d, day_rows in by_date.items():
        total_demand = sum(float(r["energy_mu"]) for r in day_rows)
        if total_demand <= 0:
            continue
        agg = {}
        for col in weather_cols:
            agg[col] = sum(float(r[col]) * float(r["energy_mu"]) for r in day_rows) / total_demand
        out[d] = agg
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--modeling-dataset", type=Path, default=None, help="path to Stage 1's modeling_dataset.csv, for the national weather feature")
    args = parser.parse_args()

    rows = load_csv(args.in_path)
    print(f"Loaded {len(rows)} rows")

    # --- national holidays (offline, computed for the full span up front) ---
    years = list(range(2022, 2027))
    india_holidays = holidays.India(years=years)  # national only -- no subdiv, this is a national model

    # --- national weather (best-effort, partial coverage -- see caveat) ---
    national_weather = {}
    n_weather_missing = 0
    if args.modeling_dataset and args.modeling_dataset.exists():
        national_weather = build_national_weather(args.modeling_dataset)
        print(f"Loaded national weather for {len(national_weather)} distinct dates ({min(national_weather)} to {max(national_weather)})")

    # --- per-row time/holiday/price-cap features ---
    for r in rows:
        ts = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        d = ts.date()

        block = ts.hour * 4 + ts.minute // 15  # 0-95
        r["block_of_day"] = block
        r["block_sin"] = math.sin(2 * math.pi * block / 96)
        r["block_cos"] = math.cos(2 * math.pi * block / 96)

        dow = ts.weekday()  # 0=Monday
        r["day_of_week"] = dow
        r["dow_sin"] = math.sin(2 * math.pi * dow / 7)
        r["dow_cos"] = math.cos(2 * math.pi * dow / 7)
        r["is_weekend"] = 1 if dow >= 5 else 0

        month = ts.month
        r["month_sin"] = math.sin(2 * math.pi * (month - 1) / 12)
        r["month_cos"] = math.cos(2 * math.pi * (month - 1) / 12)

        r["is_national_holiday"] = 1 if d in india_holidays else 0

        cap = price_cap_for_date(d)
        r["price_cap_rs_mwh"] = cap
        mcp = float(r["mcp_rs_mwh"])
        r["is_at_price_cap"] = 1 if mcp >= cap - 1.0 else 0  # small epsilon for float rounding

        date_str = d.isoformat()
        w = national_weather.get(date_str)
        if w:
            r["national_temp_mean"] = round(w["temperature_2m_mean"], 2)
            r["national_humidity_mean"] = round(w["relative_humidity_2m_mean"], 2)
            r["national_wind_speed_max"] = round(w["wind_speed_10m_max"], 2)
            r["national_cloud_cover_mean"] = round(w["cloud_cover_mean"], 2)
        else:
            r["national_temp_mean"] = ""
            r["national_humidity_mean"] = ""
            r["national_wind_speed_max"] = ""
            r["national_cloud_cover_mean"] = ""
            n_weather_missing += 1

    print(f"Rows without national weather (outside 2024-04-01 to 2026-04-05): {n_weather_missing} ({n_weather_missing/len(rows)*100:.1f}%)", flush=True)

    # --- lags, computed per market_type so markets never leak into each other ---
    by_market: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_market[r["market_type"]].append(r)

    LAG_SPECS = [("1block", timedelta(minutes=15)), ("1day", timedelta(days=1)), ("1week", timedelta(weeks=1))]
    for market, group in by_market.items():
        group.sort(key=lambda r: r["timestamp"])
        mcp_by_ts = {r["timestamp"]: float(r["mcp_rs_mwh"]) for r in group}
        netload_by_ts = {r["timestamp"]: float(r["net_load_mw"]) for r in group}
        n_gap_hits = {label: 0 for label, _ in LAG_SPECS}
        for r in group:
            ts = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            for label, delta in LAG_SPECS:
                target = (ts - delta).strftime("%Y-%m-%d %H:%M:%S")
                if target in mcp_by_ts:
                    r[f"mcp_lag_{label}"] = mcp_by_ts[target]
                    r[f"net_load_lag_{label}"] = netload_by_ts[target]
                else:
                    r[f"mcp_lag_{label}"] = ""
                    r[f"net_load_lag_{label}"] = ""
                    n_gap_hits[label] += 1
        print(f"{market}: {len(group)} rows, lags computed (blank due to no exact match: {n_gap_hits})", flush=True)

    # Each market's group is already timestamp-sorted from the lag step
    # above -- write market-by-market (alphabetical) instead of flattening
    # + re-sorting 290k dicts by a tuple key. Also use csv.writer on plain
    # tuples instead of DictWriter -- DictWriter re-validates every row's
    # keys against fieldnames (extrasaction checking) on every call, which
    # is the actual bottleneck at this row count; a flat list comprehension
    # + csv.writer.writerows is the same output, much faster.
    t0 = time.time()
    n_written = 0
    fieldnames = list(next(iter(by_market.values()))[0].keys())
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        for market in sorted(by_market):
            group = by_market[market]
            writer.writerows([r[k] for k in fieldnames] for r in group)
            n_written += len(group)
    print(f"CSV write took {time.time()-t0:.1f}s", flush=True)

    print(f"\nWrote {n_written} rows, {len(fieldnames)} columns -> {args.out}")


if __name__ == "__main__":
    main()
