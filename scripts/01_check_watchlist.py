#!/usr/bin/env python3
"""
Parse watchlist.csv and check if it's stale.

Outputs: watchlist_titles.json — list of {name, year} dicts.

Flags:
    --confirm   When watchlist.csv is stale, prompt [y/N] before continuing.
                Without this flag, runs silently (no prompt, no exit).
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
CSV_PATH = ROOT / "watchlist.csv"
OUTPUT = ROOT / "watchlist_titles.json"

STALE_DAYS = 60


def check_staleness(confirm: bool):
    if not CSV_PATH.exists():
        print("ERROR: watchlist.csv not found at", CSV_PATH)
        sys.exit(1)

    age = datetime.now() - datetime.fromtimestamp(CSV_PATH.stat().st_mtime)
    if age > timedelta(days=STALE_DAYS):
        days_old = age.days
        if confirm:
            answer = input(
                f"\nwatchlist.csv is {days_old} days old (>{STALE_DAYS}). "
                "Update it before running? [y/N] "
            ).strip().lower()
            if answer == "y":
                print(
                    "\nTo update:\n"
                    "  1. letterboxd.com → Profile → Settings → Import & Export → Export Your Data\n"
                    "  2. Unzip and drop watchlist.csv into /Users/cedric/projects/arthouse/\n"
                    "  3. Re-run this script."
                )
                sys.exit(2)
        # Silent or user chose to proceed — continue with stale data


def parse_watchlist():
    films = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "").strip()
            year = row.get("Year", "").strip()
            if name:
                films.append({"name": name, "year": year})
    return films


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true",
                        help="Prompt interactively if watchlist.csv is stale")
    args = parser.parse_args()

    check_staleness(confirm=args.confirm)
    films = parse_watchlist()
    OUTPUT.write_text(json.dumps(films, indent=2, ensure_ascii=False))
    print(f"Parsed {len(films)} films from watchlist → {OUTPUT.name}")


if __name__ == "__main__":
    main()
