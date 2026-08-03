"""
diagnose_weekly_pdf.py

One-off diagnostic to figure out why extraction is failing on the newer-style
weekly report PDFs (filenames with a "_XXX" id suffix). Prints, for a given
page:
  - raw extracted text (first 1500 chars)
  - how many tables pdfplumber's extract_tables() found
  - how many embedded images are on the page (a page that's ALL image and
    ZERO text strongly suggests the table was rendered as a picture, e.g.
    a scanned page or an image-based report export -- which would mean we
    need OCR instead of text/table extraction)

USAGE:
  python diagnose_weekly_pdf.py "weekly_reports/Weekly 050525 to 110525_427.pdf"
  python diagnose_weekly_pdf.py "weekly_reports/Weekly 050525 to 110525_427.pdf" --page 2
  python diagnose_weekly_pdf.py "weekly_reports/Weekly 050525 to 110525_427.pdf" --all-pages
"""

import argparse
import sys
from pathlib import Path

import pdfplumber


def diagnose_page(page, index: int):
    text = page.extract_text() or ""
    tables = page.extract_tables()
    images = page.images
    print(f"=== page {index} ===")
    print(f"  extract_text() length: {len(text)} chars")
    print(f"  extract_tables() found: {len(tables)} table(s)")
    print(f"  embedded images on page: {len(images)}")
    if text.strip():
        print("  --- text preview (first 1500 chars) ---")
        print(text[:1500])
    else:
        print("  --- NO TEXT FOUND on this page ---")
    if tables:
        for ti, t in enumerate(tables):
            print(f"  --- table {ti} ({len(t)} rows) preview ---")
            for row in t[:10]:
                print("   ", row)
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--page", type=int, default=4, help="1-indexed page number to inspect (default: 4)")
    parser.add_argument("--all-pages", action="store_true", help="inspect every page instead of just one")
    args = parser.parse_args()

    with pdfplumber.open(args.pdf_path) as pdf:
        print(f"{args.pdf_path.name}: {len(pdf.pages)} page(s) total\n")
        if args.all_pages:
            for i, page in enumerate(pdf.pages):
                diagnose_page(page, i + 1)
        else:
            idx = args.page - 1
            if idx < 0 or idx >= len(pdf.pages):
                print(f"Page {args.page} out of range (doc has {len(pdf.pages)} pages)")
                sys.exit(1)
            diagnose_page(pdf.pages[idx], args.page)


if __name__ == "__main__":
    main()
