#!/usr/bin/env python3
"""
Format matches.json into calendar event payloads.

This script doesn't call any API — it just prepares structured event data
that the agent (Claude) reads and pushes to Google Calendar via MCP tools.

Pass --city la to use the LA Arthouse calendar and LA venue addresses.

Output: events_to_create.json
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent

CALENDARS = {
    "nyc": "Arthouse Screenings",
    "la": "LA Arthouse Association",
}

VENUE_ADDRESSES_NYC = {
    "Metrograph": "7 Ludlow St, New York, NY 10002",
    "Film Forum": "209 W Houston St, New York, NY 10014",
    "Anthology Film Archives": "32 2nd Ave, New York, NY 10003",
    "BAM Cinematek": "30 Lafayette Ave, Brooklyn, NY 11217",
    "IFC Center": "323 6th Ave, New York, NY 10014",
    "Nitehawk Cinema (Williamsburg)": "136 Metropolitan Ave, Brooklyn, NY 11249",
    "Nitehawk Williamsburg": "136 Metropolitan Ave, Brooklyn, NY 11249",
    "Nitehawk Cinema (Prospect Park)": "188 Prospect Park W, Brooklyn, NY 11215",
    "Nitehawk Prospect Park": "188 Prospect Park W, Brooklyn, NY 11215",
    "MoMA Film": "11 W 53rd St, New York, NY 10019",
    "Museum of the Moving Image": "36-01 35th Ave, Astoria, NY 11106",
    "Low Cinema": "70-11 60th St, Ridgewood, NY 11385",
    "The Quad": "34 W 13th St, New York, NY 10011",
    "Spectacle Theater": "124 S 3rd St, Brooklyn, NY 11249",
    "Film at Lincoln Center": "165 W 65th St, New York, NY 10023",
}

VENUE_ADDRESSES_LA = {
    "Aero Theatre": "1328 Montana Ave, Santa Monica, CA 90403",
    "Egyptian Theatre": "6712 Hollywood Blvd, Hollywood, CA 90028",
    "Los Feliz Theatre": "1822 N Vermont Ave, Los Angeles, CA 90027",
    "New Beverly Cinema": "7165 Beverly Blvd, Los Angeles, CA 90036",
    "Vidiots": "4745 Cahuenga Blvd, North Hollywood, CA 91602",
    "LACMA": "5905 Wilshire Blvd, Los Angeles, CA 90036",
    "Nuart Theatre": "11272 Santa Monica Blvd, Los Angeles, CA 90025",
    "Alamo Drafthouse": "700 W 7th St, Los Angeles, CA 90017",
    "Brain Dead Studios": "5623 Melrose Ave, Los Angeles, CA 90038",
    "Laemmle": "Los Angeles, CA",
    "Now Instant Image Hall": "2958 Sunset Blvd, Los Angeles, CA 90026",
    "Acropolis Cinema": "Los Angeles, CA",
    "American Cinematheque": "6712 Hollywood Blvd, Hollywood, CA 90028",
    "Vista Theatre": "4473 Sunset Dr, Los Angeles, CA 90027",
    "2220 Arts + Archives": "2220 Beverly Blvd, Los Angeles, CA 90057",
    "Billy Wilder Theater": "10899 Wilshire Blvd, Los Angeles, CA 90024",
    "Cinespia (Hollywood Forever)": "6000 Santa Monica Blvd, Los Angeles, CA 90038",
}


def format_event(match, calendar_name: str, addresses: dict) -> dict:
    title = match["film"]
    venue = match["venue"]
    date = match.get("date", "")
    time = match.get("time", "")
    url = match.get("url", "")
    qa = match.get("qa", False)
    liked_qa = match.get("liked_qa", False)
    rating = match.get("rating")

    if liked_qa:
        summary = f"LIKED FILM Q&A: {title} @ {venue}"
    elif qa:
        summary = f"Q&A: {title} @ {venue}"
    else:
        summary = f"{title} @ {venue}"

    description_parts = []
    if liked_qa:
        description_parts.append(
            f"Not on watchlist — added because you rated this {rating}/5 and there's a Q&A."
        )
    elif qa:
        description_parts.append("Q&A / Special event")
    if url:
        description_parts.append(f"Tickets / info: {url}")
    description = "\n".join(description_parts)

    location = addresses.get(venue, venue)

    return {
        "summary": summary,
        "description": description,
        "location": location,
        "date": date,
        "time": time,
        "all_day": not bool(time),
        "qa": qa,
        "calendar": calendar_name,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="nyc", choices=["nyc", "la"])
    args = parser.parse_args()

    calendar_name = CALENDARS[args.city]
    addresses = VENUE_ADDRESSES_LA if args.city == "la" else VENUE_ADDRESSES_NYC

    matches_path = ROOT / "matches.json"
    if not matches_path.exists():
        print("ERROR: matches.json not found. Run 03_match.py first.")
        raise SystemExit(1)

    matches = json.loads(matches_path.read_text())
    events = [format_event(m, calendar_name, addresses) for m in matches]

    output = ROOT / "events_to_create.json"
    output.write_text(json.dumps(events, indent=2, ensure_ascii=False))
    print(f"Formatted {len(events)} events → {output.name}")
    print(f"Calendar: {calendar_name}")
    print("\nNext step: agent reads events_to_create.json and pushes to Google Calendar.")


if __name__ == "__main__":
    main()
