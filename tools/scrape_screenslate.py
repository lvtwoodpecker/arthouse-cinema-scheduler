#!/usr/bin/env python3
"""
Tool: scrape_screenslate

Fetches upcoming NYC arthouse screenings directly from Screenslate's Drupal
JSON:API. No WebFetch, no HTML scraping, no Claude needed.

Strategy:
  - Query /jsonapi/node/screening with an OR filter across all 13 venue UUIDs
  - Include field_venue and field_showtimes paragraph entities
  - Paginate through all results
  - Filter client-side for showtimes in the next 14 days
    (server-side date filter on paragraph sub-fields causes 504 timeout)

Venue UUIDs were looked up via /jsonapi/node/venue?filter[title]=...
and are stable — only need to update if Screenslate restructures their DB.

Output: JSON array to stdout (same schema as extract_cinema_screenings.py)
Cache:  cache/YYYY-WNN/screenslate.json
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "cache"

SCREENSLATE_API = "https://www.screenslate.com/jsonapi/node/screening"

# Screenslate venue UUIDs → internal display names used throughout the pipeline
VENUES = {
    "417871ab-4a75-4974-b05e-f891a69652b7": "Metrograph",
    "54de142f-a0c3-4394-aab2-8fff618a0f8f": "Film Forum",
    "8bef435c-ce75-40c2-879e-ef4390618ed6": "Anthology Film Archives",
    "8d55963f-9462-4585-925b-c5e4cd87c49f": "IFC Center",
    "b42ce9a7-546c-4c54-98d4-e30ae3f999d6": "Nitehawk Williamsburg",
    "aa3622c4-0c02-4e25-a7d8-49c6bbd848b3": "Nitehawk Prospect Park",
    "56b2cbaa-eeb2-458c-84df-c1427be0c7ea": "MoMA Film",
    "5898f84f-ac78-47fb-94c7-211726df233b": "Museum of the Moving Image",
    "d2ae1816-6b69-4e82-955d-0a0f80dd2c03": "Low Cinema",
    "84734dfd-358f-43c4-90b7-d4a6d710c848": "BAM Cinematek",
    "36cd0845-c31c-4e7a-8a4c-3748031d7a55": "Spectacle Theater",
    "59cd222d-c543-4c3c-90a8-a1c5d5d68e8e": "The Quad",
    "4978b384-dadf-4f5e-a4be-fbb4a2f0253f": "Film at Lincoln Center",
}

QA_KEYWORDS = {
    "q&a", "filmmaker", "director", "in person", "panel",
    "discussion", "special guest", "conversation", "introduced by",
    "introduction by", "with the director", "post-screening",
}


def get_week_dir() -> Path:
    iso = datetime.now().isocalendar()
    week_dir = CACHE_DIR / f"{iso[0]}-W{iso[1]:02d}"
    week_dir.mkdir(parents=True, exist_ok=True)
    return week_dir


def load_cache():
    path = get_week_dir() / "screenslate.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_cache(screenings: list) -> Path:
    path = get_week_dir() / "screenslate.json"
    path.write_text(json.dumps(screenings, indent=2, ensure_ascii=False))
    return path


def fetch_venue(
    session: requests.Session,
    venue_uuid: str,
    venue_name: str,
    today: datetime,
    end_date: datetime,
) -> tuple[list, dict]:
    """Fetch all screening nodes for a single venue, stopping early when
    we've seen enough consecutive pages with no upcoming showtimes.
    Returns (nodes, included_entities_dict).
    """
    MAX_EMPTY_PAGES = 2

    all_nodes: list = []
    all_included: dict[str, dict] = {}

    offset = 0
    page = 0
    empty_streak = 0

    while True:
        params = {
            "include": "field_showtimes",
            "page[limit]": "50",
            "page[offset]": str(offset),
            "sort": "-changed",
            "filter[venue][condition][path]": "field_venue.id",
            "filter[venue][condition][value]": venue_uuid,
        }
        resp = session.get(SCREENSLATE_API, params=params, timeout=45)
        resp.raise_for_status()
        data = resp.json()

        nodes = data.get("data", [])
        included = data.get("included", [])

        for entity in included:
            all_included[entity["id"]] = entity

        upcoming = sum(1 for n in nodes if _has_upcoming(n, all_included, today, end_date))
        all_nodes.extend(nodes)

        page += 1

        if upcoming == 0:
            empty_streak += 1
        else:
            empty_streak = 0

        if not nodes or "next" not in data.get("links", {}):
            break
        if empty_streak >= MAX_EMPTY_PAGES:
            break
        if page >= 20:
            print(f"    WARNING: {venue_name} hit 20-page cap", file=sys.stderr)
            break

        offset += 50

    return all_nodes, all_included


def _has_upcoming(node: dict, included: dict, today: datetime, end_date: datetime) -> bool:
    st_data = node.get("relationships", {}).get("field_showtimes", {}).get("data", [])
    if not isinstance(st_data, list):
        st_data = [st_data] if st_data else []
    for ref in st_data:
        st_entity = included.get(ref.get("id", ""))
        if not st_entity:
            continue
        field_time = st_entity.get("attributes", {}).get("field_time", "")
        if not field_time:
            continue
        try:
            dt = datetime.fromisoformat(field_time)
            if dt.tzinfo:
                dt = dt.astimezone(tz=None).replace(tzinfo=None)
            if today.date() <= dt.date() <= end_date.date():
                return True
        except ValueError:
            continue
    return False


# Short venue name aliases used in Screenslate node titles (e.g. "at MoMI", "at FLC")
_AT_ALIASES = [
    "MoMI", "MoMA", "BAM", "AFA", "FLC",
    "Metrograph", "IFC", "Nitehawk", "Spectacle", "Quad",
    "Anthology", "Lincoln Center",
]

# Regex for Metrograph-style suffix: "Film Mar 26 Metrograph" or "Film Sept'24 Metrograph"
# Handles both "Month DD Venue" and "Month'YY Venue" with optional trailing text
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
_VENUE_WORD = r"(?:Metrograph|Anthology|BAM|IFC|Nitehawk|Spectacle|Quad|Forum|Lincoln|Cinema|Theater|Theatre|MoMA|MoMI|AFA|FLC)"
_DATE_VENUE_RE = re.compile(
    rf"\s+{_MONTH}[a-z'0-9]*[\s'-]*\d*\s+{_VENUE_WORD}.*$", re.IGNORECASE
)


def parse_film_title(raw_title: str, venue_name: str) -> str:
    """
    Strip venue/date suffixes from Screenslate node titles. Two patterns:
      1. '[Film] at [VenueName/alias]' — e.g. "The Others at MoMI", "Dry Leaf at FLC"
         Uses rfind so extra trailing text (e.g. "at BAM - March 2026") is also stripped.
      2. '[Film] [Month] [DD] [Venue]' — e.g. "Klute Feb 26 Metrograph", "Film Sept'24 Metrograph"
    """
    # Normalize unicode whitespace (Screenslate uses \u202f narrow no-break spaces)
    title = re.sub(r"\s+", " ", raw_title).strip()
    lower = title.lower()

    # Pattern 1: find last " at [alias]" and strip from there
    best_idx = -1
    for name in list(VENUES.values()) + _AT_ALIASES:
        needle = f" at {name.lower()}"
        idx = lower.rfind(needle)
        if idx > best_idx:
            best_idx = idx
    if best_idx > 0:
        return title[:best_idx].strip()

    # Pattern 2: "Month DD/YY VenueWord" suffix
    stripped = _DATE_VENUE_RE.sub("", title).strip()
    if stripped and stripped != title:
        return stripped

    return title


def is_qa(note: str) -> bool:
    if not note:
        return False
    note_lower = note.lower()
    return any(kw in note_lower for kw in QA_KEYWORDS)


def extract_screenings(
    nodes: list, included: dict, today: datetime, end_date: datetime
) -> list:
    results = []

    for node in nodes:
        attrs = node.get("attributes", {})
        rels = node.get("relationships", {})

        raw_title = re.sub(r"\s+", " ", attrs.get("title", "")).strip()
        if not raw_title:
            continue

        # Resolve venue from the relationship (field_venue not included, use VENUES map)
        venue_rel_data = rels.get("field_venue", {}).get("data")
        if not venue_rel_data:
            continue
        venue_uuid = venue_rel_data.get("id", "")
        venue_name = VENUES.get(venue_uuid)
        if not venue_name:
            continue  # skip venues not in our list

        film_title = parse_film_title(raw_title, venue_name)

        # Collect showtime refs
        st_data = rels.get("field_showtimes", {}).get("data", [])
        if not isinstance(st_data, list):
            st_data = [st_data] if st_data else []

        for ref in st_data:
            st_uuid = ref.get("id", "")
            st_entity = included.get(st_uuid)
            if not st_entity:
                continue

            st_attrs = st_entity.get("attributes", {})
            field_time = st_attrs.get("field_time", "")
            field_note = (st_attrs.get("field_note") or "").strip()
            field_url = (st_attrs.get("field_url") or "").strip()

            if not field_time:
                continue

            try:
                dt = datetime.fromisoformat(field_time)
                if dt.tzinfo:
                    # Convert to naive local time (system timezone = Eastern)
                    dt = dt.astimezone(tz=None).replace(tzinfo=None)
            except ValueError:
                continue

            if not (today.date() <= dt.date() <= end_date.date()):
                continue

            results.append({
                "film": film_title,
                "venue": venue_name,
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M"),
                "url": field_url,
                "qa": is_qa(field_note),
                "notes": field_note,
            })

    # Sort and deduplicate
    results.sort(key=lambda s: (s["date"], s["time"], s["venue"], s["film"]))
    seen: set = set()
    unique = []
    for s in results:
        key = (s["film"], s["venue"], s["date"], s["time"])
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return unique


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Bypass cache")
    parser.add_argument("--days", type=int, default=14, help="Lookahead window in days")
    args = parser.parse_args()

    if not args.force:
        cached = load_cache()
        if cached is not None:
            print(f"  Screenslate: {len(cached)} screenings (cached)", file=sys.stderr)
            print(json.dumps(cached))
            return

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = today + timedelta(days=args.days)
    print(
        f"  Fetching Screenslate API: {today.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}",
        file=sys.stderr,
    )

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    all_nodes: list = []
    all_included: dict = {}
    for venue_uuid, venue_name in VENUES.items():
        try:
            nodes, included = fetch_venue(session, venue_uuid, venue_name, today, end_date)
            all_nodes.extend(nodes)
            all_included.update(included)
            upcoming = sum(1 for n in nodes if _has_upcoming(n, all_included, today, end_date))
            print(f"  {venue_name}: {len(nodes)} nodes fetched, {upcoming} with upcoming showtimes", file=sys.stderr)
        except Exception as e:
            print(f"  ERROR fetching {venue_name}: {e}", file=sys.stderr)

    screenings = extract_screenings(all_nodes, all_included, today, end_date)

    cache_path = save_cache(screenings)

    venues_found = {}
    for s in screenings:
        venues_found[s["venue"]] = venues_found.get(s["venue"], 0) + 1

    print(
        f"  Screenslate: {len(screenings)} screenings across {len(venues_found)} venues → {cache_path.relative_to(ROOT)}",
        file=sys.stderr,
    )
    for venue, count in sorted(venues_found.items()):
        print(f"    {venue}: {count}", file=sys.stderr)

    print(json.dumps(screenings))


if __name__ == "__main__":
    main()
