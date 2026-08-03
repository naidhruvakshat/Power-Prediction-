"""
weather_openmeteo.py

Pulls historical daily weather data for every Indian state from the Open-Meteo
Historical Weather API (https://open-meteo.com/en/docs/historical-weather-api).

WHY OPEN-METEO:
  - Free, no API key/registration required.
  - Backed by ERA5 / ERA5-Land reanalysis (same underlying science as raw ERA5),
    but served as simple JSON/CSV over REST instead of gridded NetCDF files from
    Copernicus CDS -- much less integration work for the same data quality.
  - Covers every variable in your feature wishlist (temperature, humidity,
    rainfall, wind, cloud cover, pressure, solar radiation) in one call.

WHY ONE COORDINATE PER STATE:
  States are not points. We approximate each state with the lat/lon of its
  largest load center (usually the biggest city, not always the administrative
  capital -- e.g. Mumbai for Maharashtra, Ahmedabad for Gujarat) because
  electricity demand tracks population/industry, not geographic area. This is
  a simplification: for large or climatically diverse states (e.g. Uttar
  Pradesh, Rajasthan, Jammu & Kashmir) you may eventually want multiple
  sample points averaged or population-weighted. See config file for notes.

USAGE:
  python weather_openmeteo.py \
      --states-file state_coordinates.csv \
      --start 2024-03-01 --end 2025-03-31 \
      --out-dir data/raw/weather

OUTPUT:
  One CSV per state in --out-dir, plus a combined long-format file
  weather_all_states.csv with columns: date, state, <weather variables...>

NOTE ON TESTING:
  This script could not be executed against the live API from within the
  research sandbox used to write it (outbound network to open-meteo.com was
  blocked there). The request shape below follows the official documented
  API exactly. Run it yourself; if any daily variable name is rejected, the
  API returns a JSON error naming the bad variable -- just delete it from
  DAILY_VARIABLES and rerun.
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Daily aggregate variables covering your feature wishlist. Names must match
# the Open-Meteo "Daily Weather Variables" / "Additional Daily Variables"
# parameter names exactly -- see the API docs table if you add/remove any.
DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "apparent_temperature_mean",
    "relative_humidity_2m_mean",
    "precipitation_sum",
    "rain_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "cloud_cover_mean",
    "surface_pressure_mean",
    "shortwave_radiation_sum",  # solar radiation, optional per your spec
    "et0_fao_evapotranspiration",
]

REQUEST_TIMEOUT_S = 30
RETRIES = 3
RETRY_BACKOFF_S = 5
SLEEP_BETWEEN_STATES_S = 1.0  # be polite to the free API


def fetch_state_weather(state: str, lat: float, lon: float, start: str, end: str) -> dict:
    """Call the Open-Meteo archive API for one state and return the parsed JSON."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "Asia/Kolkata",
    }
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(ARCHIVE_URL, params=params, timeout=REQUEST_TIMEOUT_S)
            if resp.status_code != 200:
                # Open-Meteo returns a JSON body naming the bad parameter on 400s --
                # surface it directly instead of a generic HTTP error.
                raise RuntimeError(f"HTTP {resp.status_code} for {state}: {resp.text[:300]}")
            data = resp.json()
            if "daily" not in data:
                raise RuntimeError(f"Unexpected response for {state}: {data}")
            return data
        except Exception as e:  # noqa: BLE001 - we want to retry on anything and report clearly
            last_err = e
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF_S * attempt)
    raise RuntimeError(f"Failed to fetch weather for {state} after {RETRIES} attempts: {last_err}")


def load_states(states_file: Path) -> list[dict]:
    with open(states_file, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_state_csv(out_dir: Path, state: str, payload: dict) -> Path:
    daily = payload["daily"]
    dates = daily["time"]
    out_path = out_dir / f"{state.replace(' ', '_')}.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["date"] + DAILY_VARIABLES
        writer.writerow(header)
        for i, d in enumerate(dates):
            row = [d] + [daily.get(var, [None] * len(dates))[i] for var in DAILY_VARIABLES]
            writer.writerow(row)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Fetch historical weather per Indian state from Open-Meteo")
    parser.add_argument("--states-file", type=Path, default=Path("state_coordinates.csv"))
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw/weather"))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    states = load_states(args.states_file)
    print(f"Loaded {len(states)} states from {args.states_file}")

    combined_rows = []
    combined_header = ["date", "state"] + DAILY_VARIABLES

    for row in states:
        state = row["state"]
        lat, lon = float(row["latitude"]), float(row["longitude"])
        print(f"Fetching {state} ({lat}, {lon}) ...", end=" ", flush=True)
        try:
            payload = fetch_state_weather(state, lat, lon, args.start, args.end)
        except RuntimeError as e:
            print(f"FAILED: {e}")
            continue
        out_path = write_state_csv(args.out_dir, state, payload)
        daily = payload["daily"]
        for i, d in enumerate(daily["time"]):
            combined_rows.append(
                [d, state] + [daily.get(var, [None] * len(daily["time"]))[i] for var in DAILY_VARIABLES]
            )
        print(f"OK -> {out_path} ({len(daily['time'])} days)")
        time.sleep(SLEEP_BETWEEN_STATES_S)

    combined_path = args.out_dir / "weather_all_states.csv"
    with open(combined_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(combined_header)
        writer.writerows(combined_rows)
    print(f"\nCombined long-format file written: {combined_path} ({len(combined_rows)} rows)")


if __name__ == "__main__":
    sys.exit(main())
