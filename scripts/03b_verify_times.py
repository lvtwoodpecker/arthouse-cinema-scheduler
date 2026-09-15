#!/usr/bin/env python3
"""
Verify exact showtimes from source cinema websites for Screenslate-matched films.

Screenslate showtimes can be inaccurate (wrong times, missing showtimes).
This script takes the candidate matches from 03_match.py and cross-checks
each film against the actual source cinema website to get precise times.

For each unique venue in matches.json, it runs extract_cinema_screenings.py
to get the full schedule from the source, then replaces the Screenslate
showtimes with the source-verified times.

Results:
- If the film is found on the source site: times are replaced with source times.
  Multiple showtimes from the source expand the match list accordingly.
- If NOT found on source: the Screenslate entry is kept but flagged with
  "time_verified": false so you know to double-check manually.

Output: matches.json (updated in-place with verified times)

Notes:
- MoMA Film and Museum of the Moving Image are Cloudflare-blocked. The agent
  should pre-fetch those pages via WebFetch and populate the cache before
  running this step (same approach as before). The script will use the cache
  automatically if it exists.
- Venue names from Screenslate are normalized to match internal cinema names.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
TOOL = ROOT / "tools" / "extract_cinema_screenings.py"
PYTHON = sys.executable

# Map Screenslate venue names (lowercased) to internal cinema names used in CINEMA_URLS
VENUE_ALIASES = {
    "metrograph": "Metrograph",
    "film forum": "Film Forum",
    "anthology film archives": "Anthology Film Archives",
    "anthology": "Anthology Film Archives",
    "bam cinematek": "BAM Cinematek",
    "bam": "BAM Cinematek",
    "ifc center": "IFC Center",
    "ifc": "IFC Center",
    "nitehawk williamsburg": "Nitehawk Williamsburg",
    "nitehawk cinema williamsburg": "Nitehawk Williamsburg",
    "nitehawk prospect park": "Nitehawk Prospect Park",
    "nitehawk cinema prospect park": "Nitehawk Prospect Park",
    "nitehawk": "Nitehawk Williamsburg",  # default to williamsburg if ambiguous
    "moma film": "MoMA Film",
    "moma": "MoMA Film",
    "museum of modern art": "MoMA Film",
    "museum of the moving image": "Museum of the Moving Image",
    "momi": "Museum of the Moving Image",
    "low cinema": "Low Cinema",
    "the quad": "The Quad",
    "quad cinema": "The Quad",
    "spectacle theater": "Spectacle Theater",
    "spectacle": "Spectacle Theater",
    "film at lincoln center": "Film at Lincoln Center",
    "lincoln center": "Film at Lincoln Center",
    "flc": "Film at Lincoln Center",
}


def normalize_venue(name: str) -> str | None:
    """Map a Screenslate venue name to our internal cinema name."""
    return VENUE_ALIASES.get(name.lower().strip())


def normalize_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r"^(the|a|an)\s+", "", title)
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title


def fuzzy_score(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz
        return fuzz.ratio(a, b)
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio() * 100


def scrape_venue(cinema_name: str) -> list:
    """Run extract_cinema_screenings.py for a venue and return screenings."""
    cmd = [PYTHON, str(TOOL), "--cinema", cinema_name]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    try:
        return json.loads(result.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return []


def find_film_in_venue_screenings(film_name: str, venue_screenings: list) -> list:
    """Find screenings for a specific film in a venue's full schedule."""
    film_norm = normalize_title(film_name)
    matches = []
    for s in venue_screenings:
        sc_norm = normalize_title(s.get("film", ""))
        score = 100 if film_norm == sc_norm else fuzzy_score(film_norm, sc_norm)
        if score >= 85:
            matches.append(s)
    return matches


def main():
    matches_path = ROOT / "matches.json"
    if not matches_path.exists():
        print("ERROR: matches.json not found. Run 03_match.py first.")
        raise SystemExit(1)

    candidates = json.loads(matches_path.read_text())

    # Group candidates by venue, resolving Screenslate names to internal names
    venue_to_candidates: dict[str, list] = {}
    unresolvable = []

    for i, c in enumerate(candidates):
        raw_venue = c["venue"]
        internal = normalize_venue(raw_venue) or raw_venue  # keep original if unrecognized
        c["_internal_venue"] = internal
        c["_idx"] = i
        venue_to_candidates.setdefault(internal, []).append(c)

    # Fetch source schedule for each unique venue that has candidates
    venue_cache: dict[str, list] = {}
    for venue_name in venue_to_candidates:
        print(f"  Verifying from source: {venue_name}...")
        screenings = scrape_venue(venue_name)
        venue_cache[venue_name] = screenings
        if not screenings:
            print(f"    WARNING: 0 screenings returned for {venue_name} — times unverified")

    # Build verified matches list
    verified: list[dict] = []

    for venue_name, group in venue_to_candidates.items():
        source_screenings = venue_cache.get(venue_name, [])
        # Collect unique films in this venue's candidates
        films_done: set[str] = set()

        for candidate in group:
            film_name = candidate["film"]
            if film_name in films_done:
                continue
            films_done.add(film_name)

            source_hits = find_film_in_venue_screenings(film_name, source_screenings)

            if source_hits:
                # Replace Screenslate entries with source-verified entries
                for hit in source_hits:
                    entry = dict(candidate)  # copy base fields (liked_qa, watched, etc.)
                    entry["venue"] = venue_name
                    entry["date"] = hit["date"]
                    entry["time"] = hit["time"]
                    entry["url"] = hit["url"] or candidate.get("url", "")
                    entry["qa"] = hit["qa"] or candidate.get("qa", False)
                    entry["notes"] = hit["notes"] or candidate.get("notes", "")
                    entry["time_verified"] = True
                    entry.pop("_internal_venue", None)
                    entry.pop("_idx", None)
                    verified.append(entry)
                print(f"    {film_name} @ {venue_name}: {len(source_hits)} source showtime(s) ✓")
            else:
                # Film not found on source — keep Screenslate entry, flag as unverified
                entry = dict(candidate)
                entry["venue"] = venue_name
                entry["time_verified"] = False
                entry.pop("_internal_venue", None)
                entry.pop("_idx", None)
                verified.append(entry)
                print(f"    {film_name} @ {venue_name}: not found on source site — keeping Screenslate time (UNVERIFIED)")

    # Sort: Q&A first, then by date
    verified.sort(key=lambda m: (0 if m["qa"] else 1, m.get("date", "9999")))

    matches_path.write_text(json.dumps(verified, indent=2, ensure_ascii=False))

    total = len(verified)
    unverified = sum(1 for m in verified if not m.get("time_verified", True))
    print(f"\nVerified {total - unverified}/{total} showtimes from source sites")
    if unverified:
        print(f"  {unverified} unverified (kept from Screenslate) — double-check these manually")
    print(f"→ matches.json updated")


if __name__ == "__main__":
    main()
