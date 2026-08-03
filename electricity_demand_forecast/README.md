# Electricity Demand Forecast — Stage 1: Data Pipeline

State-wise daily electricity demand data (Grid India) merged with daily weather
data (Open-Meteo), ready for EDA and feature engineering.

## Folder structure

```
electricity_demand_forecast/
  data/
    raw/
      weekly_reports/       100 downloaded Grid India weekly report PDFs (source of truth)
      weather_by_state/     34 per-state weather CSVs from Open-Meteo (incl. Ladakh)
    processed/
      weekly_energy_long.csv    demand extracted from the PDFs (date, state, energy_mu)
      weather_all_states.csv    combined weather (date, state, <13 weather variables>)
      modeling_dataset.csv      FINAL merged table -- start here for EDA/modeling
  src/
    ingestion/
      bulk_download_from_list.py   downloads PDFs from a list of URLs
      weather_openmeteo.py         fetches historical weather per state
      unused_daily_report_approach/  an earlier plan (daily PSP reports) that
        was superseded once the weekly reports turned out to give the same
        daily granularity from far fewer files -- kept for reference only
    processing/
      extract_weekly_page4.py      parses the PDFs into weekly_energy_long.csv
      merge_demand_weather.py      joins demand + weather into modeling_dataset.csv
    validation/
      validate_weekly_energy.py    checks the extracted demand data for duplicates/gaps
      validate_weather.py          checks the weather data for missing/implausible values
      diagnose_weekly_pdf.py       one-off tool for inspecting a single PDF's raw structure
  config/
    state_coordinates.csv        one representative lat/lon per state (for weather fetch)
    weekly_report_links.txt      the 105 PDF URLs collected from grid-india.in
```

## Pipeline order (how to reproduce from scratch)

1. `bulk_download_from_list.py --links ../config/weekly_report_links.txt --out-dir ../data/raw/weekly_reports`
2. `extract_weekly_page4.py --in-dir ../data/raw/weekly_reports --out ../data/processed/weekly_energy_long.csv`
3. `validate_weekly_energy.py --in ../data/processed/weekly_energy_long.csv`
4. `weather_openmeteo.py --states-file ../../config/state_coordinates.csv --start 2024-04-01 --end 2026-04-05 --out-dir ../data/raw/weather_by_state`
5. `validate_weather.py --in ../data/processed/weather_all_states.csv`
6. `merge_demand_weather.py --demand ../data/processed/weekly_energy_long.csv --weather ../data/processed/weather_all_states.csv --out ../data/processed/modeling_dataset.csv`

(Paths above are relative to each script's own folder -- adjust `--in-dir`/`--out`
if running from a different working directory.)

## Current dataset stats

- **modeling_dataset.csv**: 22,400 rows = 32 states x 700 daily dates (2024-04-01 to 2026-04-05)
- Columns: `date, state, energy_mu, temperature_2m_max, temperature_2m_min, temperature_2m_mean, apparent_temperature_mean, relative_humidity_2m_mean, precipitation_sum, rain_sum, wind_speed_10m_max, wind_gusts_10m_max, cloud_cover_mean, surface_pressure_mean, shortwave_radiation_sum, et0_fao_evapotranspiration`
- Validated: no duplicate (date, state) rows, no missing values, no out-of-range values, 100% demand-weather join rate.
- Ladakh has weather but no demand data (Grid India folds it into Jammu & Kashmir's regional figures) -- expected, not a bug.

## Next stage: EDA

Not started yet. Plan: demand vs. temperature scatter per state, weekday/seasonal
patterns, correlation heatmap across weather variables, before any feature
engineering or modeling.
