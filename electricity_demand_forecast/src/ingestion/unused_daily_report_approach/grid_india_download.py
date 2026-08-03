"""
grid_india_download.py

Downloads Grid India's Daily PSP (Power Supply Position) Reports, which
contain state-wise demand-met figures, for a range of dates.

WHY THIS APPROACH:
  Grid India (formerly POSOCO) does not expose a demand-data API. Reports are
  published one file per day at report.grid-india.in, organised into folders
  by Indian fiscal year and month, e.g.:

    .../ReportData/Daily Report/PSP Report/2024-2025/May 2024/30.05.24_NLDC_PSP.xls

  Both .xls and .pdf versions of each day's report exist under the same
  filename stem. We prefer .xls because it is structured data -- no table
  extraction from a PDF layout required. We fall back to .pdf only if no xls
  is found for a date (older reports, or an intermittent gap).

  There are two known URL forms for the same file (a direct static path, and
  an index.php?p=...&dl=... form). We try both per date, since site behaviour
  has changed over the report's history (older filings may only work via one
  form).

WHAT THIS SCRIPT DOES NOT DO:
  It does not parse the downloaded files into a clean demand table -- that is
  intentionally split into a second step (see inspect_grid_india_report.py and
  parse_grid_india_reports.py). Government report layouts are inconsistent
  enough (merged cells, shifting column order, header rows that move) that
  guessing the exact table shape before ever seeing a real file is a common
  beginner mistake. Download broadly first, inspect one real file, THEN write
  the parser against what you actually see.

USAGE:
  python grid_india_download.py --start 2025-03-01 --end 2025-03-31 \
      --out-dir data/raw/demand

  This writes one file per successfully-downloaded day into --out-dir, plus a
  manifest.csv logging what succeeded/failed and which URL form worked, so
  you know exactly what's missing and can re-run just the gaps.

NOTE ON TESTING:
  report.grid-india.in was not reachable from the sandbox this script was
  written in (outbound network there is allowlisted and did not include this
  domain), so the URL construction below is based on documented/observed
  report paths, not a live end-to-end test. Run it yourself; if a URL 404s,
  open https://report.grid-india.in/psp_report.php in a browser for that date
  and compare the real download link to PSPReportURLs.direct/.index_php below
  -- send me what you see and I'll correct the pattern.
"""

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

BASE = "https://report.grid-india.in"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT_S = 30
SLEEP_BETWEEN_REQUESTS_S = 1.0


def fiscal_year_folder(d: date) -> str:
    """Indian fiscal year runs Apr-Mar, e.g. 2024-04-01..2025-03-31 -> '2024-2025'."""
    start_year = d.year if d.month >= 4 else d.year - 1
    return f"{start_year}-{start_year + 1}"


def month_folder(d: date) -> str:
    return d.strftime("%B %Y")  # e.g. "May 2024"


def filename_stem(d: date) -> str:
    return d.strftime("%d.%m.%y") + "_NLDC_PSP"


@dataclass
class Candidate:
    url: str
    form: str  # "direct" or "index_php"


def candidate_urls(d: date, ext: str) -> list[Candidate]:
    fy = fiscal_year_folder(d)
    month = month_folder(d)
    stem = filename_stem(d)
    filename = f"{stem}.{ext}"

    direct_path = f"ReportData/Daily Report/PSP Report/{fy}/{month}/{filename}"
    direct_url = f"{BASE}/{quote(direct_path)}"

    index_p = quote(f"Daily Report/PSP Report/{fy}/{month}", safe="")
    index_url = f"{BASE}/index.php?p={index_p}&dl={quote(filename)}"

    return [Candidate(direct_url, "direct"), Candidate(index_url, "index_php")]


def looks_like_xls(content: bytes) -> bool:
    return content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or content[:4] == b"PK\x03\x04"


def looks_like_pdf(content: bytes) -> bool:
    return content[:4] == b"%PDF"


def try_download(d: date, ext: str, out_dir: Path) -> tuple[bool, str, str]:
    """Try both URL forms for one (date, extension). Returns (success, path_or_reason, form)."""
    validator = looks_like_xls if ext in ("xls", "xlsx") else looks_like_pdf
    for cand in candidate_urls(d, ext):
        try:
            resp = requests.get(cand.url, headers=HEADERS, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException:
            continue
        if resp.status_code == 200 and validator(resp.content):
            out_path = out_dir / f"{d.isoformat()}_NLDC_PSP.{ext}"
            out_path.write_bytes(resp.content)
            return True, str(out_path), cand.form
    return False, f"no valid {ext} found (tried direct + index_php)", ""


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description="Download Grid India daily PSP reports")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw/demand"))
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = args.out_dir / "manifest.csv"
    manifest_rows = []

    for d in daterange(start, end):
        # Prefer xls (structured); fall back to pdf if no xls exists for that day.
        ok, result, form = try_download(d, "xls", args.out_dir)
        ext_used = "xls"
        if not ok:
            ok, result, form = try_download(d, "pdf", args.out_dir)
            ext_used = "pdf"
        status = "ok" if ok else "failed"
        print(f"{d.isoformat()}: {status} ({ext_used}) - {result}")
        manifest_rows.append([d.isoformat(), status, ext_used if ok else "", form, result])
        time.sleep(SLEEP_BETWEEN_REQUESTS_S)

    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "status", "extension", "url_form", "path_or_reason"])
        writer.writerows(manifest_rows)

    n_ok = sum(1 for r in manifest_rows if r[1] == "ok")
    print(f"\nDone: {n_ok}/{len(manifest_rows)} days downloaded. Manifest: {manifest_path}")


if __name__ == "__main__":
    sys.exit(main())
