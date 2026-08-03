"""
bulk_download_from_list.py

Downloads every URL listed in a text file (one URL per line) -- meant for the
Grid India weekly-report PDF links you grab via the browser console snippet
(see chat). This is a generic bulk downloader, not report-specific, so it
also works for the daily PSP links if you ever want to hand-collect those
instead of using grid_india_download.py's URL-guessing approach.

USAGE:
  python bulk_download_from_list.py --links weekly_report_links.txt \
      --out-dir data/raw/weekly_reports

Skips a URL if the destination file already exists, so you can re-run safely
after adding more links to the same file.
"""

import argparse
import re
import sys
import time
import warnings
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT_S = 30
SLEEP_BETWEEN_REQUESTS_S = 1.0


def filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = Path(path).name
    if not name or not name.lower().endswith(".pdf"):
        # fall back to a sanitised version of the whole URL if there's no clean filename
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", url)[-150:]
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
    return name


def main():
    parser = argparse.ArgumentParser(description="Bulk-download PDFs from a list of URLs")
    parser.add_argument("--links", type=Path, required=True, help="text file, one URL per line")
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw/weekly_reports"))
    parser.add_argument(
        "--insecure",
        action="store_true",
        help=(
            "Skip SSL certificate verification. Only use this if you hit "
            "CERTIFICATE_VERIFY_FAILED errors and installing python-certifi-win32 "
            "didn't fix it. Safe enough here since these are public Grid India PDFs, "
            "not sensitive data -- but don't make this your default habit for other sites."
        ),
    )
    args = parser.parse_args()

    if args.insecure:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        print("WARNING: SSL certificate verification disabled (--insecure)\n")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    urls = [line.strip() for line in args.links.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"{len(urls)} URLs to fetch")

    ok, skipped, failed = 0, 0, 0
    for url in urls:
        out_path = args.out_dir / filename_from_url(url)
        if out_path.exists():
            skipped += 1
            continue
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_S, verify=not args.insecure)
            if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                out_path.write_bytes(resp.content)
                print(f"OK: {out_path.name}")
                ok += 1
            else:
                print(f"FAILED ({resp.status_code}, not a PDF): {url}")
                failed += 1
        except requests.RequestException as e:
            print(f"FAILED ({e}): {url}")
            failed += 1
        time.sleep(SLEEP_BETWEEN_REQUESTS_S)

    print(f"\nDone: {ok} downloaded, {skipped} already existed, {failed} failed.")


if __name__ == "__main__":
    sys.exit(main())
