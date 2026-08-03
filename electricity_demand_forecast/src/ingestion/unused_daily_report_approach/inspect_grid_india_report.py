"""
inspect_grid_india_report.py

Run this on ONE downloaded report (xls or pdf) before writing any parsing
logic. Government report layouts are rarely as clean as they look in a
browser -- merged header cells, region sub-headers mixed into the state
list, inconsistent column order across years. Rather than guess, dump the
raw structure and look at it once, then encode what you actually saw into
parse_grid_india_reports.py.

USAGE:
  python inspect_grid_india_report.py data/raw/demand/2025-03-15_NLDC_PSP.xls
  python inspect_grid_india_report.py data/raw/demand/2025-03-15_NLDC_PSP.pdf

For xls: prints every sheet's shape and first ~40 rows, and dumps each sheet
to a companion CSV (<file>__sheet_<name>.csv) for easier scrolling.

For pdf: extracts every table pdfplumber can find on every page, prints them,
and dumps each to a companion CSV (<file>__page_<n>_table_<m>.csv).
"""

import sys
from pathlib import Path


def inspect_xls(path: Path):
    import pandas as pd

    sheets = pd.read_excel(path, sheet_name=None, header=None)
    print(f"Found {len(sheets)} sheet(s): {list(sheets.keys())}\n")
    for name, df in sheets.items():
        print(f"--- sheet '{name}' shape={df.shape} ---")
        with pd.option_context("display.max_rows", 40, "display.max_columns", 20, "display.width", 200):
            print(df.head(40))
        out_csv = path.with_name(f"{path.stem}__sheet_{name}.csv")
        df.to_csv(out_csv, index=False)
        print(f"-> dumped to {out_csv}\n")


def inspect_pdf(path: Path):
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        print(f"{len(pdf.pages)} page(s)\n")
        for pi, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            print(f"--- page {pi} : {len(tables)} table(s) found ---")
            for ti, table in enumerate(tables):
                print(f"  table {ti}: {len(table)} rows x {len(table[0]) if table else 0} cols")
                for row in table[:15]:
                    print("   ", row)
                out_csv = path.with_name(f"{path.stem}__page_{pi}_table_{ti}.csv")
                import csv as csv_mod

                with open(out_csv, "w", newline="", encoding="utf-8") as f:
                    w = csv_mod.writer(f)
                    w.writerows(table)
                print(f"  -> dumped to {out_csv}")
            if not tables:
                text = page.extract_text() or ""
                print("  (no tables detected; raw text sample below)")
                print("  " + text[:500].replace("\n", "\n   "))
            print()


def main():
    if len(sys.argv) != 2:
        print("Usage: python inspect_grid_india_report.py <path to .xls or .pdf>")
        sys.exit(1)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    ext = path.suffix.lower()
    if ext in (".xls", ".xlsx"):
        inspect_xls(path)
    elif ext == ".pdf":
        inspect_pdf(path)
    else:
        print(f"Unsupported extension: {ext}")
        sys.exit(1)


if __name__ == "__main__":
    main()
