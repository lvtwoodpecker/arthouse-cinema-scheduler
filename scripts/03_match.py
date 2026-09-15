#!/usr/bin/env python3
"""
Cross-reference watchlist titles against scraped screenings.

Matching strategy:
- Exact match (case-insensitive) first
- Fuzzy match via rapidfuzz (falls back to difflib) at 85% threshold
- Deduplicates: if the same film shows at multiple venues, keeps the best one
  (Q&A > soonest date > alphabetical venue)

Outputs: matches.json — list of matches ready for calendar event creation.
"""

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

FUZZY_THRESHOLD = 85  # % similarity for a match
LIKED_RATING_THRESHOLD = 3.5  # films rated >= this qualify for the liked-Q&A rule


def normalize(title):
    """Lowercase, strip articles, punctuation, and extra whitespace."""
    title = title.lower().strip()
    title = re.sub(r"^(the|a|an)\s+", "", title)
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title


def fuzzy_score(a, b):
    try:
        from rapidfuzz import fuzz
        return fuzz.ratio(a, b)
    except ImportError:
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio() * 100


def best_screening(screenings):
    """Pick the best screening when a film appears multiple times.
    Priority: Q&A first, then earliest date, then venue alphabetically.
    """
    def sort_key(s):
        return (
            0 if s["qa"] else 1,
            s.get("date", "9999"),
            s["venue"],
        )
    return sorted(screenings, key=sort_key)[0]


def load_ratings():
    """Load films rated >= LIKED_RATING_THRESHOLD from ratings.csv.
    Returns a dict of normalized_title -> {"name": ..., "year": ..., "rating": ...}
    """
    ratings_path = ROOT / "letterboxd-export" / "ratings.csv"
    if not ratings_path.exists():
        return {}
    liked = {}
    with open(ratings_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rating = float(row.get("Rating", 0))
            except ValueError:
                continue
            if rating >= LIKED_RATING_THRESHOLD:
                name = row.get("Name", "").strip()
                if name:
                    liked[normalize(name)] = {
                        "name": name,
                        "year": row.get("Year", "").strip(),
                        "rating": rating,
                    }
    return liked


def main():
    watchlist_path = ROOT / "watchlist_titles.json"
    screenings_path = ROOT / "screenings.json"

    if not watchlist_path.exists():
        print("ERROR: watchlist_titles.json not found. Run 01_check_watchlist.py first.")
        raise SystemExit(1)
    if not screenings_path.exists():
        print("ERROR: screenings.json not found. Run 02_scrape_cinemas.py first.")
        raise SystemExit(1)

    watchlist = json.loads(watchlist_path.read_text())
    screenings = json.loads(screenings_path.read_text())
    ratings = load_ratings()

    # watched.csv — informational only. Watchlist is source of truth:
    # a film on watchlist is always included even if already watched.
    watched_path = ROOT / "letterboxd-export" / "watched.csv"
    watched_norms: set[str] = set()
    if watched_path.exists():
        with open(watched_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("Name", "").strip()
                if name:
                    watched_norms.add(normalize(name))

    watchlist_norm = {normalize(f["name"]): f for f in watchlist}
    screening_norm = [(normalize(s["film"]), s) for s in screenings]

    # film_title (normalized) → list of matching screenings
    film_matches: dict[str, list] = {}

    for wl_norm, wl_film in watchlist_norm.items():
        for sc_norm, screening in screening_norm:
            score = 100 if wl_norm == sc_norm else fuzzy_score(wl_norm, sc_norm)
            if score >= FUZZY_THRESHOLD:
                key = wl_film["name"]
                film_matches.setdefault(key, []).append(screening)

    # Collect all watchlist-matched normalized titles to exclude from liked-Q&A pass
    matched_norms = set(film_matches.keys())
    matched_norms_normalized = {normalize(k) for k in matched_norms}

    # Build final match list — all showtimes per film (user wants all options)
    # Watchlist is source of truth: include everything regardless of watched status.
    matches = []
    for film_name, hits in film_matches.items():
        for screening in hits:
            matches.append({
                "film": film_name,
                "venue": screening["venue"],
                "date": screening["date"],
                "time": screening["time"],
                "url": screening["url"],
                "qa": screening["qa"],
                "notes": screening["notes"],
                "liked_qa": False,
                "watched": normalize(film_name) in watched_norms,
            })

    # Liked-film Q&A rule: rated >= 3.5, NOT on watchlist, has a Q&A screening
    liked_qa_matches = []
    for sc_norm, screening in screening_norm:
        if not screening["qa"]:
            continue
        if sc_norm in matched_norms_normalized:
            continue  # already on watchlist, skip
        for rated_norm, rated_film in ratings.items():
            score = 100 if sc_norm == rated_norm else fuzzy_score(sc_norm, rated_norm)
            if score >= FUZZY_THRESHOLD:
                liked_qa_matches.append({
                    "film": rated_film["name"],
                    "venue": screening["venue"],
                    "date": screening["date"],
                    "time": screening["time"],
                    "url": screening["url"],
                    "qa": True,
                    "notes": screening["notes"],
                    "liked_qa": True,
                    "rating": rated_film["rating"],
                    "watched": sc_norm in watched_norms,
                })
                break  # don't double-add from multiple rating entries

    # Sort: Q&A first, then by date
    matches.sort(key=lambda m: (0 if m["qa"] else 1, m.get("date", "9999")))
    liked_qa_matches.sort(key=lambda m: m.get("date", "9999"))

    all_matches = matches + liked_qa_matches

    output = ROOT / "matches.json"
    output.write_text(json.dumps(all_matches, indent=2, ensure_ascii=False))

    qa_count = sum(1 for m in matches if m["qa"])
    print(f"Found {len(matches)} watchlist matches ({qa_count} with Q&A)")
    print(f"Found {len(liked_qa_matches)} liked-film Q&A additions (rated >= {LIKED_RATING_THRESHOLD})")
    print(f"Total → {output.name}")
    for m in all_matches:
        tag = " [LIKED Q&A]" if m["liked_qa"] else (" [Q&A]" if m["qa"] else "")
        print(f"  {m['film']} @ {m['venue']} — {m['date']}{tag}")


if __name__ == "__main__":
    main()
