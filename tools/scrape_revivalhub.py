#!/usr/bin/env python3
"""
Tool: scrape_revivalhub

Fetches LA arthouse screenings from Revival Hub's Firebase JSON API.
No browser automation needed — pure requests + JSON parsing.

Output: JSON array to stdout (same schema as scrape_screenslate.py / scrape_good1s.py)
Cache:  cache/YYYY-WNN/revivalhub.json
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "cache"
LOOKAHEAD_DAYS = 14

DATA_URL = "https://storage.googleapis.com/revival-hub-ab2a8.firebasestorage.app/airtable-uploads/data-import.json"

# Pacific time: PDT = UTC-7 (Mar-Nov), PST = UTC-8 (Nov-Mar)
# Use a fixed offset based on current date (good enough for scheduling)
def _pacific_offset():
    now = datetime.now()
    # DST: second Sunday in March through first Sunday in November
    year = now.year
    dst_start = datetime(year, 3, 8) + timedelta(days=(6 - datetime(year, 3, 8).weekday()) % 7)
    dst_end = datetime(year, 11, 1) + timedelta(days=(6 - datetime(year, 11, 1).weekday()) % 7)
    if dst_start <= now < dst_end:
        return -7  # PDT
    return -8  # PST


APPROVED_VENUE_IDS = {
    "receaYS3GjcnaPXRk": "Aero Theatre",
    "rec3pxveRm2MxNEf9": "Egyptian Theatre",
    "reca5PPodRRuNBu5S": "Los Feliz Theatre",
    "recNZKXdvrRtEhn3q": "New Beverly Cinema",
    "rectsa7NnuY0acNLe": "Vidiots",
    "recpykVc1XZDmGvlx": "LACMA",
    "recSVRBPoGGjmdtEr": "Nuart Theatre",
    "recnDUb015DPEwdHn": "Alamo Drafthouse",
    "recLl1QkRkckozthW": "Vista Theatre",
    "rec48AVVKakrz4Clb": "2220 Arts + Archives",
    # Billy Wilder / Hammer Museum not in Revival Hub — covered by good1s
}

QA_CATEGORIES = {"q&a", "filmmaker", "in person", "panel", "discussion", "special guest"}
QA_KEYWORDS = {
    "q&a", "filmmaker", "director", "in person", "panel", "discussion",
    "special guest", "conversation", "introduced by", "introduction by",
    "with the director", "post-screening",
}


def is_qa(screening):
    cats = [c.lower() for c in screening.get("categories", [])]
    if any(q in cats for q in QA_CATEGORIES):
        return True
    info = screening.get("additionalInfo", "").lower()
    return any(kw in info for kw in QA_KEYWORDS)


def utc_ms_to_pacific(utc_ms):
    offset_hours = _pacific_offset()
    dt_utc = datetime.fromtimestamp(utc_ms / 1000, tz=timezone.utc)
    dt_pacific = dt_utc + timedelta(hours=offset_hours)
    return dt_pacific


def parse_screening(s, venue_name, lookahead_dates):
    films = s.get("films", [])
    if not films:
        return []

    # Multi-film = double feature; use joined titles
    if len(films) == 1:
        title = films[0]["name"]
    else:
        title = " + ".join(f["name"] for f in films)

    formats = s.get("formats", [])
    notes = formats[0] if formats else ""

    ticket_urls = s.get("ticket_urls", [])
    url = ticket_urls[0] if ticket_urls else ""

    qa = is_qa(s)

    results = []

    # Each screening_times entry is a separate showtime
    for iso_time in s.get("screening_times", []):
        try:
            dt_utc = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
            offset_hours = _pacific_offset()
            dt_pacific = dt_utc + timedelta(hours=offset_hours)
            date_str = dt_pacific.strftime("%Y-%m-%d")
            time_str = dt_pacific.strftime("%H:%M")
        except Exception:
            continue

        if date_str not in lookahead_dates:
            continue

        results.append({
            "film": title,
            "venue": venue_name,
            "date": date_str,
            "time": time_str,
            "url": url,
            "qa": qa,
            "notes": notes,
        })

    # Fallback: use utc_start if no screening_times
    if not results and s.get("utc_start"):
        dt_pacific = utc_ms_to_pacific(s["utc_start"])
        date_str = dt_pacific.strftime("%Y-%m-%d")
        time_str = dt_pacific.strftime("%H:%M")
        if date_str in lookahead_dates:
            results.append({
                "film": title,
                "venue": venue_name,
                "date": date_str,
                "time": time_str,
                "url": url,
                "qa": qa,
                "notes": notes,
            })

    return results


def fetch_screenings(days=LOOKAHEAD_DAYS):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    lookahead_dates = {
        (today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)
    }

    print("  Fetching Revival Hub JSON...", file=sys.stderr)
    resp = requests.get(DATA_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    all_screenings = []
    for s in data.get("screenings", []):
        venue_id = s.get("venue", "")
        venue_name = APPROVED_VENUE_IDS.get(venue_id)
        if not venue_name:
            continue
        all_screenings.extend(parse_screening(s, venue_name, lookahead_dates))

    # Deduplicate
    seen = set()
    unique = []
    for s in all_screenings:
        key = (s["film"], s["venue"], s["date"], s["time"])
        if key not in seen:
            seen.add(key)
            unique.append(s)

    unique.sort(key=lambda s: (s["date"], s["time"], s["venue"]))
    return unique


def get_week_dir():
    iso = datetime.now().isocalendar()
    week_dir = CACHE_DIR / ("%d-W%02d" % (iso[0], iso[1]))
    week_dir.mkdir(parents=True, exist_ok=True)
    return week_dir


def load_cache():
    path = get_week_dir() / "revivalhub.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_cache(screenings):
    path = get_week_dir() / "revivalhub.json"
    path.write_text(json.dumps(screenings, indent=2, ensure_ascii=False))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Bypass cache and re-fetch")
    parser.add_argument("--days", type=int, default=LOOKAHEAD_DAYS)
    args = parser.parse_args()

    if not args.force:
        cached = load_cache()
        if cached is not None:
            print("  Revival Hub: %d screenings (cached)" % len(cached), file=sys.stderr)
            print(json.dumps(cached))
            return

    screenings = fetch_screenings(days=args.days)
    cache_path = save_cache(screenings)

    venues_found = {}
    for s in screenings:
        venues_found[s["venue"]] = venues_found.get(s["venue"], 0) + 1

    print(
        "  Revival Hub: %d screenings across %d venues -> %s"
        % (len(screenings), len(venues_found), cache_path.relative_to(ROOT)),
        file=sys.stderr,
    )
    for venue, count in sorted(venues_found.items()):
        print("    %s: %d" % (venue, count), file=sys.stderr)

    print(json.dumps(screenings))


if __name__ == "__main__":
    main()
