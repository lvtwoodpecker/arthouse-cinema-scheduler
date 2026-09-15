#!/usr/bin/env python3
"""
Tool: scrape_good1s

Scrapes good1s.org for LA arthouse screenings using Playwright (headless browser).
Navigates day-by-day via the "next day" button, collecting 14 days of screenings.

Extraction strategy:
  1. Claude Haiku (via Anthropic SDK) -- clean titles, handles edge cases
  2. BeautifulSoup fallback -- zero API calls, regex-based title cleaning

Output: JSON array to stdout (same schema as scrape_screenslate.py)
Cache:  cache/YYYY-WNN/good1s.json

Requires: playwright (pip install playwright && playwright install chromium)
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString
from playwright.async_api import async_playwright

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / "cache"
LOOKAHEAD_DAYS = 14

# Lowercase partial-match strings for approved LA venues
APPROVED_VENUES = {
    "aero theatre", "aero theater",
    "los feliz theatre", "los feliz theater",
    "egyptian theatre", "egyptian theater",
    "new beverly cinema", "new bev",
    "vidiots",
    "lacma",
    "nuart theatre", "nuart theater",
    "alamo drafthouse",
    "brain dead studios",
    "laemmle",
    "now instant image",
    "acropolis cinema",
    "vista theatre", "vista theater",
    "2220 arts",
    "billy wilder",
    "hammer museum",
    "ucla",
    "cinespia",
    "hollywood forever",
}

VENUE_CANONICAL = {
    "aero theater": "Aero Theatre",
    "los feliz theater": "Los Feliz Theatre",
    "los feliz 3": "Los Feliz Theatre",
    "egyptian theater": "Egyptian Theatre",
    "the new beverly cinema": "New Beverly Cinema",
    "new bev": "New Beverly Cinema",
    "vista theater": "Vista Theatre",
    "2220 arts + archives": "2220 Arts + Archives",
    "2220 arts and archives": "2220 Arts + Archives",
    "billy wilder theater": "Billy Wilder Theater",
    "billy wilder theatre": "Billy Wilder Theater",
    "hammer museum": "Billy Wilder Theater",
    "cinespia": "Cinespia (Hollywood Forever)",
    "hollywood forever": "Cinespia (Hollywood Forever)",
}

QA_KEYWORDS = {
    "q&a", "filmmaker", "director", "in person", "panel", "discussion",
    "special guest", "conversation", "introduced by", "introduction by",
    "with the director", "post-screening",
}

_TIME_RE = re.compile(r'\b(\d{1,2}:\d{2}\s*[AP]M)\b', re.IGNORECASE)
_SKIP_TEXT = {
    "prev day", "next day", "good 1s", "los angeles", "newsletter",
    "subscribe", "put together movies", "share a link", "star",
}
_DAY_NAMES = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


def is_approved_venue(name):
    lower = name.lower()
    return any(v in lower for v in APPROVED_VENUES)


def canonical_venue(name):
    return VENUE_CANONICAL.get(name.lower().strip(), name.strip())


def is_qa(text):
    lower = text.lower()
    return any(kw in lower for kw in QA_KEYWORDS)


def parse_time(raw):
    m = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)', raw.strip(), re.IGNORECASE)
    if not m:
        return None
    h, mins, ampm = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if ampm == "PM" and h != 12:
        h += 12
    elif ampm == "AM" and h == 12:
        h = 0
    return "%02d:%02d" % (h, mins)


def clean_film_title(raw):
    # good1s.org format: "FILM TITLE Director Name, YEAR, Runtime Mins, Format"
    t = raw.strip()
    # Strip from first ", YEAR" onward
    t = re.sub(r',?\s+(?:19|20)\d{2}\b.*', '', t, flags=re.DOTALL)
    # Strip standalone "N Mins" at end
    t = re.sub(r'\s+\d+\s+Mins.*$', '', t, flags=re.IGNORECASE)
    # Strip comma-introduced director: ", Firstname ..."
    t = re.sub(r',\s+[A-Z][a-z].*$', '', t)
    # Strip & / and / slash co-director suffix
    t = re.sub(r'\s+(?:&|and|/)\s+[A-Z][a-z]\S*(?:\s+[A-Z][a-z]\S*)+$', '', t)
    # Strip exactly last 2 Title-Case words (director First Last)
    t = re.sub(r'(?:\s+[A-Z]\.)?\s+[A-Z][a-z]\S*\s+[A-Z][a-z]\S*$', '', t)
    # Strip trailing format labels
    t = re.sub(
        r'\s+(?:in\s+)?(?:DCP|35mm|16mm|70mm|Digital|HD|Nitrate|B&W|Color|Technicolor)\s*$',
        '', t, flags=re.IGNORECASE,
    )
    t = re.sub(r'["\']', '', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip(' ,.-/')


def is_nav_or_header(text):
    lower = text.lower()
    if any(skip in lower for skip in _SKIP_TEXT):
        return True
    if any(day in lower for day in _DAY_NAMES) and len(text) < 40:
        return True
    if re.match(r'^\d{1,2}/\d{1,2}', text.strip()):
        return True
    return False


# ---------------------------------------------------------------------------
# Extraction strategy 1: Claude Haiku via Anthropic SDK
# ---------------------------------------------------------------------------

def extract_with_haiku(body_text, date_str):
    import anthropic
    client = anthropic.Anthropic(timeout=20.0, max_retries=1)
    prompt = (
        "Extract ALL film screenings from this good1s.org Los Angeles listing page.\n\n"
        "Date: %s\n\n"
        "Return a JSON array. Each element must have these exact keys:\n"
        '  "film"  -- film title only: strip director name, year, runtime, format\n'
        '  "venue" -- exact venue/theater name as shown on the page\n'
        '  "date"  -- always "%s"\n'
        '  "time"  -- HH:MM 24-hour format (e.g. "19:30")\n'
        '  "url"   -- ticket/info URL, "" if none\n'
        '  "qa"    -- true if Q&A / filmmaker / special guest mentioned\n'
        '  "notes" -- format like "35mm", "70mm", or "" if none\n\n'
        "Rules:\n"
        "- Include every venue -- do NOT filter\n"
        "- Each showtime is a separate object\n"
        "- Film title must be clean: just the title, no director/year/format\n"
        "- Return ONLY a raw JSON array, no markdown, no explanation\n\n"
        "Page text:\n%s"
    ) % (date_str, date_str, body_text[:8000])

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Extraction strategy 2: BeautifulSoup HTML parser (no API calls)
# ---------------------------------------------------------------------------

def extract_with_bs4(html, date_str):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()

    screenings = []
    current_venue = None

    for elem in (soup.body.descendants if soup.body else []):
        if isinstance(elem, NavigableString):
            continue
        tag = elem.name.lower() if elem.name else ""
        if not tag:
            continue

        text = elem.get_text(" ", strip=True)
        if not text:
            continue

        if tag in ("h2", "h3"):
            if not _TIME_RE.search(text) and not is_nav_or_header(text) and len(text) < 100:
                current_venue = text.strip()
            continue

        if tag not in ("li", "p", "span", "a", "td", "div") or not current_venue:
            continue

        child_tags = {c.name for c in elem.children if getattr(c, "name", None)}
        if child_tags & {"ul", "ol", "div", "section", "article", "h2", "h3"}:
            continue

        time_match = _TIME_RE.search(text)
        if not time_match:
            continue

        time_24 = parse_time(time_match.group(1))
        if not time_24:
            continue

        if tag == "a":
            url = elem.get("href", "")
            film_raw = text[: time_match.start()].strip()
        else:
            link = elem.find("a")
            url = link.get("href", "") if link else ""
            if link:
                link_text = link.get_text(" ", strip=True)
                film_raw = (
                    link_text
                    if link_text and not _TIME_RE.search(link_text) and len(link_text) < 150
                    else text[: time_match.start()].strip()
                )
            else:
                film_raw = text[: time_match.start()].strip()

        film = clean_film_title(film_raw)
        if not film or len(film) < 3:
            continue

        screenings.append({
            "film": film,
            "venue": current_venue,
            "date": date_str,
            "time": time_24,
            "url": url,
            "qa": is_qa(text),
            "notes": "",
        })

    seen = set()
    unique = []
    for s in screenings:
        key = (s["film"], s["venue"], s["time"])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


# ---------------------------------------------------------------------------
# Filter + canonicalize venue names
# ---------------------------------------------------------------------------

def filter_and_canonicalize(raw_list):
    out = []
    for s in raw_list:
        venue = s.get("venue", "")
        if is_approved_venue(venue):
            out.append(dict(s, venue=canonical_venue(venue)))
    return out


# ---------------------------------------------------------------------------
# Playwright navigation + per-day extraction
# ---------------------------------------------------------------------------

def merge_extractions(haiku_list, bs4_list):
    """
    Merge Haiku and BS4 results. Haiku titles win on (venue, time) collisions
    since they're cleaner. BS4 adds anything Haiku missed.
    """
    merged = {(s["venue"], s["time"]): s for s in bs4_list}
    for s in haiku_list:
        merged[(s["venue"], s["time"])] = s
    return list(merged.values())


async def scrape(days=LOOKAHEAD_DAYS, use_haiku=True):
    all_screenings = []
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("  Navigating to good1s.org...", file=sys.stderr)
        await page.goto("https://www.good1s.org/", wait_until="networkidle", timeout=30000)

        for day_offset in range(days):
            date = today + timedelta(days=day_offset)
            date_str = date.strftime("%Y-%m-%d")

            try:
                await page.wait_for_load_state("load", timeout=15000)

                html = await page.content()

                if use_haiku:
                    try:
                        body_text = await page.inner_text("body")
                        haiku_raw = extract_with_haiku(body_text, date_str)
                        bs4_raw = extract_with_bs4(html, date_str)
                        raw = merge_extractions(haiku_raw, bs4_raw)
                        method = "haiku+bs4"
                    except Exception as haiku_err:
                        print(
                            "  %s: haiku failed (%s), using bs4 only"
                            % (date_str, haiku_err),
                            file=sys.stderr,
                        )
                        raw = extract_with_bs4(html, date_str)
                        method = "bs4"
                else:
                    raw = extract_with_bs4(html, date_str)
                    method = "bs4"

                approved = filter_and_canonicalize(raw)
                all_screenings.extend(approved)
                print(
                    "  %s [%s]: %d total, %d approved"
                    % (date_str, method, len(raw), len(approved)),
                    file=sys.stderr,
                )
            except Exception as e:
                print("  %s: failed -- %s" % (date_str, e), file=sys.stderr)

            if day_offset < days - 1:
                try:
                    next_link = page.get_by_text("next day", exact=False).first
                    await next_link.click(timeout=10000)
                    await page.wait_for_load_state("load", timeout=15000)
                except Exception as e:
                    print(
                        "  Warning: could not navigate to day %d: %s" % (day_offset + 2, e),
                        file=sys.stderr,
                    )
                    break

        await browser.close()

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
    path = get_week_dir() / "good1s.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_cache(screenings):
    path = get_week_dir() / "good1s.json"
    path.write_text(json.dumps(screenings, indent=2, ensure_ascii=False))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Bypass cache and re-scrape")
    parser.add_argument("--days", type=int, default=LOOKAHEAD_DAYS)
    parser.add_argument("--no-haiku", action="store_true", help="Skip Haiku, use BS4 only")
    args = parser.parse_args()

    if not args.force:
        cached = load_cache()
        if cached is not None:
            print("  good1s.org: %d screenings (cached)" % len(cached), file=sys.stderr)
            print(json.dumps(cached))
            return

    screenings = asyncio.run(scrape(days=args.days, use_haiku=not args.no_haiku))
    cache_path = save_cache(screenings)

    venues_found = {}
    for s in screenings:
        venues_found[s["venue"]] = venues_found.get(s["venue"], 0) + 1

    print(
        "  good1s.org: %d screenings across %d venues -> %s"
        % (len(screenings), len(venues_found), cache_path.relative_to(ROOT)),
        file=sys.stderr,
    )
    for venue, count in sorted(venues_found.items()):
        print("    %s: %d" % (venue, count), file=sys.stderr)

    print(json.dumps(screenings))


if __name__ == "__main__":
    main()
