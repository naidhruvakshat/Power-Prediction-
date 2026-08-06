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
    national_price_dataset_clean.csv      negative-value-cleaned version of the above
    national_price_dataset_features.csv   feature-engineered, cleaned table
  data/splits/
    train.csv          2022-04-01 to 2024-07-04 (~68%)
    validation.csv      2024-07-05 to 2024-12-28 (~15%)
    test.csv            2024-12-29 to 2025-06-24 (~17%)
  models/
    xgboost_dam.json, xgboost_rtm.json, xgboost_gdam.json   trained models
    results_summary.json    metrics + feature importance per market
  src/processing/
    standardize_scada_to_15min.py   resolution-agnostic net-load standardizer
    iex_to_long.py                  parses IEX Market Snapshot xlsx files
    join_netload_price.py           joins the two on timestamp, per market_type
    clean_dataset.py                column-specific negative-value cleaning
    build_features.py               adds time/holiday/price-cap/lag/weather features
  src/modeling/
    split_dataset.py                chronological train/validation/test split
    train_xgboost.py                trains + evaluates one XGBoost model per market
```

Raw source files (Audit/ folder — SCADA exports + IEX Market Snapshots,
750MB+) are not tracked in git; see `.gitignore`. Re-running the pipeline
from scratch requires those local files.

## Pipeline order

1. `standardize_scada_to_15min.py --in-dir <scada raw folder> --out combined_15min_netload.csv`
2. `iex_to_long.py --in-dir <IEX Data folder> --out iex_long.csv`
3. `join_netload_price.py --netload combined_15min_netload.csv --price iex_long.csv --out national_price_dataset.csv`
4. `clean_dataset.py --in national_price_dataset.csv --out national_price_dataset_clean.csv`
5. `build_features.py --in national_price_dataset_clean.csv --modeling-dataset <electricity_demand_forecast>/data/processed/modeling_dataset.csv --out national_price_dataset_features.csv`
6. `split_dataset.py --in national_price_dataset_features.csv --out-dir data/splits/`
7. `train_xgboost.py --splits-dir data/splits/ --out-dir models/`

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
  each other) so no market's series leaks into another's. Looked up by
  EXACT timestamp match, not by counting a fixed number of rows back --
  an earlier version counted rows, which silently pulled values from over
  a year before the missing-2023 gap and mislabeled them "last week."
  Fixed: if the exact prior timestamp doesn't exist (e.g. right after a
  gap), the lag is left blank instead of substituting a wrong value.
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

## Cleaning (step 4)

Checked every numeric column for negatives before touching anything --
NOT a blanket "negatives -> 0" pass:

| Column | Negative rows found | Action |
|---|---|---|
| solar_mw | 14,297 (4.9%) | Clipped to 0 -- solar can't physically be negative; small nighttime sensor noise (e.g. -10 MW at midnight) |
| wind_mw | 0 | untouched |
| demand_mw | 0 | untouched |
| purchase_bid_mw / sell_bid_mw / mcv_mw / final_scheduled_volume_mw | 0 | untouched |
| mcp_rs_mwh | 0 | untouched -- deliberately would NOT have been zeroed even if found, since negative prices are a real market phenomenon (oversupply) |
| net_load_mw | 0 (originally) | recomputed after cleaning solar, since it's derived from demand/wind/solar |

`net_load_mw` is recomputed as `demand_mw - wind_mw - solar_mw` using the
cleaned solar figure, since the original was computed from noisy raw solar
(subtracting a negative solar reading was silently inflating net_load by a
few MW).

## Train / validation / test split (step 6)

Chronological, not random -- a random row split would leak same-day
information between train and test on a time series. Fixed calendar
cutoffs, ~70/15/15 by day count across the full 2022-04-01 to 2025-06-24
span:

| Split | Date range | Rows |
|---|---|---|
| train | 2022-04-01 to 2024-07-04 | 197,088 |
| validation | 2024-07-05 to 2024-12-28 | 42,910 |
| test | 2024-12-29 to 2025-06-24 | 50,391 |

## Model (step 7)

One XGBoost regressor per `market_type` (DAM/RTM/GDAM never share a model
or training signal, matching the project's market-type-grouping rule).

**Features excluded deliberately** (leakage): `purchase_bid_mw`,
`sell_bid_mw`, `mcv_mw`, `final_scheduled_volume_mw` are outputs of the
same market-clearing auction as the target price, cleared simultaneously
-- using them to predict that same block's price assumes information you
wouldn't have yet in a real forecast. `is_at_price_cap` is computed
directly from the target (`mcp_rs_mwh`) during feature engineering, so
it's excluded outright.

**Nowcasting caveat**: `demand_mw`/`wind_mw`/`solar_mw`/`net_load_mw` are
used as-is for the block being priced, i.e. the model assumes the actual
realized grid state is already known. Reasonable for RTM (decided close to
real-time delivery); optimistic for DAM (bid a day ahead, when only a
forecast of net load would really be available). Flagging this as a known
simplification for this first pass, not a deployment-ready assumption.

**Missing values** (weather before 2024-04, lags right after data gaps)
are left as NaN -- XGBoost splits on NaN natively, nothing is imputed.

### Results (test set)

| Market | MAE (Rs/MWh) | RMSE (Rs/MWh) | MAPE |
|---|---|---|---|
| DAM | 286.96 | 587.91 | 13.44% |
| RTM | 386.35 | 705.26 | 14.26%* |
| GDAM | 323.61 | 636.32 | 13.18% |

\* RTM's raw MAPE computed as 325.66% -- inflated by 58 rows (0.35% of
the test set) where the real price crashed to near-zero (RTM prices can
legitimately hit ~0 during oversupply), which blows up percentage error
even for a small absolute miss. 14.26% is MAPE recomputed excluding those
rows; MAE/RMSE were unaffected and are the more trustworthy metrics for
RTM regardless.

Across all three markets, `mcp_lag_1block` (the price 15 minutes ago)
dominates feature importance (80-85%) -- expected for a short-horizon
series like this; price doesn't move much in 15 minutes most of the time.

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
