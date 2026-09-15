#!/usr/bin/env python3
"""
Arthouse Cinema Watchlist Scheduler — orchestrator.

Runs the pipeline in order:
    01_check_watchlist.py   — stale check + parse watchlist.csv
    02_scrape_cinemas.py    — scrape listings for all-venue discovery
    03_match.py             — fuzzy-match watchlist vs screenings
    03b_verify_times.py     — verify exact times from source cinema websites (NYC only)
    04_format_events.py     — format matches into calendar event payloads

After this script completes, the agent reads events_to_create.json
and pushes events to Google Calendar via MCP.

Flags:
    --city nyc   (default) NYC pipeline via Screenslate API
    --city la    LA pipeline via good1s.org (Playwright + Haiku)
    --force      Re-scrape even if cache exists for this week
    --confirm    Prompt interactively if watchlist.csv is stale
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent / "scripts"
PYTHON = sys.executable


def run(script, extra=None):
    cmd = [PYTHON, str(SCRIPTS_DIR / script)] + (extra or [])
    print(f"\n── {script} {'─' * (50 - len(script))}")
    result = subprocess.run(cmd)
    if result.returncode not in (0,):
        if result.returncode == 2:
            print("\nExiting — update your watchlist.csv and re-run.")
        else:
            print(f"\nERROR: {script} exited with code {result.returncode}. Stopping.")
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="nyc", choices=["nyc", "la"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--days", type=int, default=14, help="Lookahead window in days (NYC only)")
    args = parser.parse_args()

    scraper_args = ["--force"] if args.force else []
    watchlist_args = ["--confirm"] if args.confirm else []
    city_args = ["--city", args.city]
    if args.city == "nyc":
        city_args += ["--days", str(args.days)]

    print(f"=== Arthouse Cinema Watchlist Scheduler [{args.city.upper()}] ===")
    run("01_check_watchlist.py", watchlist_args)
    run("02_scrape_cinemas.py", scraper_args + city_args)
    run("03_match.py")
    if args.city == "nyc":
        run("03b_verify_times.py")
    run("04_format_events.py", city_args)
    print("\n=== Pipeline complete ===")
    print("events_to_create.json is ready — agent will now create calendar events.")


if __name__ == "__main__":
    main()
