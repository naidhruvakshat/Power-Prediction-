# National 15-Minute Electricity Price Model — Data Pipeline

Joins India-wide 15-minute net load (demand − wind − solar, from SCADA/PMU
exports) with IEX price data (DAM/RTM/GDAM) into one modeling-ready table.

## Folder structure

```
national_price_model/
  data/processed/
    combined_15min_netload.csv       standardized net load, Sept 2021 - Jun 2025
    iex_long.csv                     IEX DAM/RTM/GDAM prices, long format, 2022-2026
    national_price_dataset.csv       joined table (price + net load)
    national_price_dataset_features.csv   FINAL, feature-engineered table -- start here for modeling
  src/processing/
    standardize_scada_to_15min.py   resolution-agnostic net-load standardizer
    iex_to_long.py                  parses IEX Market Snapshot xlsx files
    join_netload_price.py           joins the two on timestamp, per market_type
    build_features.py               adds time/holiday/price-cap/lag/weather features
```

Raw source files (Audit/ folder — SCADA exports + IEX Market Snapshots,
750MB+) are not tracked in git; see `.gitignore`. Re-running the pipeline
from scratch requires those local files.

## Pipeline order

1. `standardize_scada_to_15min.py --in-dir <scada raw folder> --out combined_15min_netload.csv`
2. `iex_to_long.py --in-dir <IEX Data folder> --out iex_long.csv`
3. `join_netload_price.py --netload combined_15min_netload.csv --price iex_long.csv --out national_price_dataset.csv`
4. `build_features.py --in national_price_dataset.csv --modeling-dataset <electricity_demand_forecast>/data/processed/modeling_dataset.csv --out national_price_dataset_features.csv`

## Standardization rule (net load, step 1)

Native resolution < 15 min → average down (mean of every real reading in
the 15-min window). Native resolution > 15 min → linear interpolation up
(assumes the coarser reading is an instantaneous snapshot, not an hourly
average — undecided which it is; original hourly values are kept in a
separate column so this can be revisited). Native resolution == 15 min →
pass through. Resolution is detected empirically per file, not assumed by
filename/year.

## Data quality issues found and handled (step 2, IEX)

- **All 12 files in `IEX_2023/DAM/`** declare `Date: 04-08-2026` instead of
  their labeled 2023 month — a mis-export that captured a live "today"
  snapshot instead of historical data. Verified by inspection, not
  guessed. These files are entirely rejected (0 usable rows) rather than
  contributing any data under a false 2023 label. **DAM has no 2023 data
  at all until these are re-downloaded.**
- **RTM_2023 and GDAM_2023** both have two files (their "(8)" and "(9)")
  declaring the identical range `01-08-2023 to 31-08-2023` — August was
  downloaded twice, and September 2023 was never downloaded for either
  market. Real gap, not fabricated.
- **RTM_2024 and GDAM_2024** have the same duplicate-month pattern for
  November (files "(11)"/"(12)"), meaning December 2024 is missing for
  both markets.
- General safeguard added: any row outside a file's own declared
  `Date: X to Y` header range is dropped (catches similar mis-exports
  automatically in future re-runs), and any exact-duplicate
  `(market_type, timestamp)` row across files is collapsed to one (only
  when values agree across every duplicate — disagreements are kept and
  flagged rather than silently resolved).

### Remaining documented gaps in `iex_long.csv` (real, not bugs)

| Market | Gap |
|---|---|
| DAM | All of 2023 (needs re-download) |
| DAM | December 2024 |
| RTM | September 2023 |
| RTM | December 2024 |
| RTM | ~30 min on 2024-07-17/18 (minor) |
| GDAM | September 2023 |
| GDAM | December 2024 |

## Current joined dataset

`national_price_dataset.csv`: 290,389 rows across DAM/RTM/GDAM, spanning
2022-04-01 to 2025-06-24 (the net-load data's ceiling — IEX price data
actually extends to 2026-08-04, but net load from Mendeley stops in June
2025, so the trainable span is the *intersection* of the two, per the
project's span rule). Extending past June 2025 needs a newer net-load
source.

Columns: `timestamp, market_type, purchase_bid_mw, sell_bid_mw, mcv_mw,
final_scheduled_volume_mw, mcp_rs_mwh, demand_mw, wind_mw, solar_mw,
net_load_mw, netload_is_interpolated, netload_source_resolution_min,
price_source_file`.

## Features added (step 4)

`national_price_dataset_features.csv` adds 22 columns on top of the joined
table:

- **Cyclical time encodings**: `block_sin`/`block_cos` (15-min block within
  the day, 96 blocks), `dow_sin`/`dow_cos` (day of week), `month_sin`/
  `month_cos` — sine/cosine pairs so e.g. 23:45 and 00:00 read as adjacent
  instead of maximally far apart, which a raw block number (0 vs 95) would
  imply. Plus `is_weekend`.
- **`is_national_holiday`**: computed offline via the `holidays` Python
  library for the full 2022-2026 span (national only — this is a national
  model, not state-wise, so no per-state holiday column here).
- **Price cap context** (`price_cap_rs_mwh`, `is_at_price_cap`): the
  regulatory ceiling on MCP is Rs 10,000/MWh *today*, but it wasn't always
  — checking our own data's monthly maximums against CERC's public order
  history shows it was Rs 20,000/MWh through 2022-04-06, Rs 12,000/MWh from
  2022-04-07 to 2023-04-03, and Rs 10,000/MWh from 2023-04-04 onward. A
  fixed "mcp == 10000" check would misclassify every capped row before
  April 2023. `is_at_price_cap` checks against the cap actually in force on
  that date instead.
- **Lags** (`mcp_lag_{1block,1day,1week}`, `net_load_lag_{1block,1day,1week}`):
  computed separately per `market_type` (DAM/RTM/GDAM never share lags with
  each other) so no market's series leaks into another's.
- **National weather** (`national_temp_mean`, `national_humidity_mean`,
  `national_wind_speed_max`, `national_cloud_cover_mean`): demand-weighted
  across states (each state's reading weighted by its share of that day's
  total energy_mu, so high-load states like Maharashtra/UP dominate over
  low-load states like Sikkim) rather than a flat average. **Coverage
  caveat**: only available 2024-04-01 to 2026-04-05 (the demand-forecast
  project's weather range) — that's ~61% of rows (177,792 of 290,389) with
  no weather value, specifically everything from 2022-04-01 to 2024-03-31.
  Extending weather back to 2022 needs a fresh Open-Meteo pull, which can't
  run from this sandbox (network-blocked) — same as the original weather
  fetch, would need to run on your machine.

## Next steps

- Re-download `IEX_2023/DAM` properly (all 12 files currently unusable).
- Decide whether to chase down September 2023 / December 2024 RTM+GDAM,
  or accept as permanent gaps.
- Decide on the July 2025 - Aug 2026 net-load gap (train on intersection
  vs. find a newer source).
- Decide on the weather coverage gap (2022-04 to 2024-03, ~61% of rows) --
  extend the Open-Meteo pull back to 2022, or accept partial coverage.
- Remaining `dataset_definition.xlsx` features not yet built: physics_price
  (merit-order estimate), reserve margins, monsoon/festival flags beyond
  national holidays.
