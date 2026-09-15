#!/usr/bin/env python3
"""
Scrape upcoming screenings from the configured city's primary source.

NYC (default): Screenslate JSON:API via tools/scrape_screenslate.py
LA:            good1s.org (Playwright + Haiku) + Revival Hub (JSON API)
               Revival Hub is authoritative for venues it covers;
               good1s fills in anything else.

Pass --force to bypass cache and re-fetch.
Pass --city la to switch to the LA pipeline.

Output: screenings.json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
TOOLS_DIR = ROOT / "tools"
PYTHON = sys.executable

# Venues where Revival Hub is authoritative; good1s entries for these are dropped
# to avoid duplicates with potentially worse title extraction.
REVIVALHUB_AUTHORITATIVE = {
    "Aero Theatre",
    "Egyptian Theatre",
    "Los Feliz Theatre",
    "New Beverly Cinema",
    "Vidiots",
    "Vista Theatre",
    "2220 Arts + Archives",
    "Nuart Theatre",
    "Alamo Drafthouse",
    "LACMA",
}


def run_tool(tool_path, extra_args):
    result = subprocess.run(
        [PYTHON, str(tool_path)] + extra_args,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        return json.loads(result.stdout.strip() or "[]")
    except json.JSONDecodeError as e:
        print(f"  WARNING: {tool_path.name} — bad JSON: {e}", file=sys.stderr)
        return []


def merge_la(revivalhub, good1s):
    """
    Revival Hub is authoritative for the venues it covers.
    good1s adds screenings for venues Revival Hub doesn't track
    (Billy Wilder Theater, Brain Dead Studios, Now Instant Image Hall, etc.)
    """
    merged = list(revivalhub)
    rh_keys = {(s["film"], s["venue"], s["date"], s["time"]) for s in revivalhub}

    for s in good1s:
        if s["venue"] in REVIVALHUB_AUTHORITATIVE:
            continue  # defer to Revival Hub for these venues
        key = (s["film"], s["venue"], s["date"], s["time"])
        if key not in rh_keys:
            merged.append(s)

    merged.sort(key=lambda s: (s["date"], s["time"], s["venue"]))
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--city", default="nyc", choices=["nyc", "la"])
    parser.add_argument("--days", type=int, default=14, help="Lookahead window in days (NYC only)")
    args = parser.parse_args()

    force_args = ["--force"] if args.force else []

    if args.city == "la":
        rh = run_tool(TOOLS_DIR / "scrape_revivalhub.py", force_args)
        print(f"  [Revival Hub] {len(rh)} screenings", file=sys.stderr)

        g1s = run_tool(TOOLS_DIR / "scrape_good1s.py", force_args)
        print(f"  [good1s.org]  {len(g1s)} screenings", file=sys.stderr)

        screenings = merge_la(rh, g1s)
        label = "LA (Revival Hub + good1s.org)"
    else:
        screenings = run_tool(TOOLS_DIR / "scrape_screenslate.py", force_args + ["--days", str(args.days)])
        label = "Screenslate (NYC)"

    output = ROOT / "screenings.json"
    output.write_text(json.dumps(screenings, indent=2, ensure_ascii=False))
    print(f"\n[{label}] Total: {len(screenings)} screenings → {output.name}")
    if not screenings:
        print(f"  WARNING: 0 screenings returned from {label}.")


if __name__ == "__main__":
    main()
