#!/usr/bin/env python3
"""
Tool: extract_cinema_screenings

Fetches a cinema website, strips HTML to plain text, and uses Claude Haiku
to extract structured screening data.

This is a single-cinema tool. 02_scrape_cinemas.py calls it once per cinema.

Usage:
    python tools/extract_cinema_screenings.py --cinema "MoMA Film"
    python tools/extract_cinema_screenings.py --cinema "Metrograph" --force
    python tools/extract_cinema_screenings.py --cinema "Custom" --url "https://..."

    # Pre-fetched text (used by agent for Cloudflare-blocked sites like MoMA):
    python tools/extract_cinema_screenings.py --cinema "MoMA Film" --text-file /tmp/moma.txt
    python tools/extract_cinema_screenings.py --cinema "MoMA Film" --text-file /tmp/moma_w1.txt --text-file /tmp/moma_w2.txt

Output:
    JSON array to stdout. Progress/errors to stderr.

Cache:
    Results saved to cache/YYYY-WNN/<cinema_slug>.json
    Pass --force to bypass cache and re-fetch.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import subprocess

import requests

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "cache"

CINEMA_URLS = {
    "Metrograph":                  "https://metrograph.com/calendar/",
    "Film Forum":                  "https://filmforum.org/films",
    "Anthology Film Archives":     "https://anthologyfilmarchives.org/film_screenings",
    "BAM Cinematek":               "https://www.bam.org/film",
    "IFC Center":                  "https://www.ifccenter.com/films/",
    "Nitehawk Williamsburg":       "https://nitehawkcinema.com/williamsburg/",
    "Nitehawk Prospect Park":      "https://nitehawkcinema.com/prospectpark/",
    "MoMA Film":                   "https://www.moma.org/calendar/?filters[0]=film",
    "Museum of the Moving Image":  "https://movingimage.us/program/",
    "Low Cinema":                  "https://lowcinema.com/tickets/",
    "The Quad":                    "https://quadcinema.com/all-films/",
    "Spectacle Theater":           "https://spectacletheater.com",
    "Film at Lincoln Center":      "https://www.filmlinc.org/films/",
}


def get_week_dir() -> Path:
    iso = datetime.now().isocalendar()
    week_dir = CACHE_DIR / f"{iso[0]}-W{iso[1]:02d}"
    week_dir.mkdir(parents=True, exist_ok=True)
    return week_dir


def slugify(name: str) -> str:
    return re.sub(r"[^\w]+", "_", name.lower()).strip("_")


def load_cache(slug: str):
    path = get_week_dir() / f"{slug}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_cache(slug: str, screenings: list) -> Path:
    path = get_week_dir() / f"{slug}.json"
    path.write_text(json.dumps(screenings, indent=2, ensure_ascii=False))
    return path


def fetch_and_strip(url: str) -> str:
    """Fetch URL via requests and reduce to plain text."""
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    html = session.get(url, timeout=15).text

    try:
        import trafilatura
        text = trafilatura.extract(html, include_tables=True, include_links=True)
        if text and len(text) > 200:
            return text
    except ImportError:
        pass

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def extract_with_haiku(cinema: str, url: str, text: str) -> list:
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""Extract all upcoming film screenings from this cinema website text.

Cinema: {cinema}
Source URL: {url}
Today: {today}

For each screening return a JSON object with these exact keys:
  "film"  — film title (string)
  "venue" — always "{cinema}"
  "date"  — YYYY-MM-DD (string, "" if unknown)
  "time"  — HH:MM 24-hour (string, "" if unknown)
  "url"   — ticket/info link (string, "" if none)
  "qa"    — true if Q&A / filmmaker appearance / special guest is mentioned (boolean)
  "notes" — format or special info like "35mm", "4K restoration", "world premiere" (string, "" if none)

Rules:
- Only include screenings on or after today ({today})
- If a film has multiple showtimes, include each as a separate object
- Return ONLY a raw JSON array, no markdown, no explanation
- If no screenings found, return []

Website text:
{text[:10000]}"""

    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-haiku-4-5-20251001", "--output-format", "text"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI error: {result.stderr.strip()}")

    raw = result.stdout.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cinema", required=True)
    parser.add_argument("--url", help="Override URL (optional if cinema is in known list)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--text-file", action="append", dest="text_files", metavar="PATH",
        help="Use pre-fetched text from file instead of fetching. Can be passed multiple times to combine."
    )
    args = parser.parse_args()

    url = args.url or CINEMA_URLS.get(args.cinema, "")
    if not url and not args.text_files:
        print(f"ERROR: unknown cinema '{args.cinema}'. Pass --url or use one of: {', '.join(CINEMA_URLS)}", file=sys.stderr)
        sys.exit(1)

    slug = slugify(args.cinema)

    if not args.force:
        cached = load_cache(slug)
        if cached is not None:
            print(f"  {args.cinema}: {len(cached)} screenings (cached)", file=sys.stderr)
            print(json.dumps(cached))
            return

    # --- Get text ---
    if args.text_files:
        # Pre-fetched text supplied by agent (e.g. for MoMA via WebFetch)
        parts = []
        for path in args.text_files:
            parts.append(Path(path).read_text(encoding="utf-8"))
        text = "\n\n".join(parts)
        print(f"  {args.cinema}: using {len(args.text_files)} pre-fetched file(s), {len(text):,} chars total", file=sys.stderr)
    else:
        print(f"  Fetching {args.cinema}...", file=sys.stderr)
        try:
            text = fetch_and_strip(url)
        except Exception as e:
            print(f"  ERROR fetching {args.cinema}: {e}", file=sys.stderr)
            print("[]")
            sys.exit(0)

    print(f"  Extracting from {len(text):,} chars of text...", file=sys.stderr)
    try:
        screenings = extract_with_haiku(args.cinema, url, text)
    except Exception as e:
        print(f"  ERROR extracting {args.cinema}: {e}", file=sys.stderr)
        print("[]")
        sys.exit(0)

    cache_path = save_cache(slug, screenings)
    print(f"  {args.cinema}: {len(screenings)} screenings → {cache_path.relative_to(ROOT)}", file=sys.stderr)
    print(json.dumps(screenings))


if __name__ == "__main__":
    main()
