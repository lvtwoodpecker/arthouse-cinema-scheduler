# arthouse

Match your Letterboxd watchlist against upcoming arthouse/repertory cinema screenings (NYC and LA), and push matches to Google Calendar as events — with a Claude Code agent driving the workflow and confirming before anything gets scheduled.

## How it works

```
run.py
 ├─ 01_check_watchlist.py    parse watchlist.csv, warn if stale
 ├─ 02_scrape_cinemas.py     scrape listings (per --city)
 │    NYC: tools/scrape_screenslate.py (Screenslate JSON:API)
 │    LA:  tools/scrape_revivalhub.py (Revival Hub API)
 │         + tools/scrape_good1s.py (good1s.org via Playwright + Claude Haiku)
 ├─ 03_match.py              fuzzy-match watchlist titles against scraped screenings
 ├─ 03b_verify_times.py      (NYC only) re-verify showtimes against source cinema sites
 └─ 04_format_events.py      format matches into calendar event payloads
```

`run.py` writes `events_to_create.json`. A Claude Code agent (see [`docs/agent.md`](docs/agent.md)) reads that file, presents matches grouped by priority (Q&A screenings, liked-film Q&A additions, rare formats, standard matches), waits for your confirmation, and creates the confirmed events on Google Calendar via the [Google Calendar MCP server](https://github.com/cocal/google-calendar-mcp).

The pipeline scripts never call an LLM except `scrape_good1s.py` (Haiku-assisted extraction for a JS-heavy site with a BeautifulSoup fallback) and `extract_cinema_screenings.py` (a fallback for scrapers that get blocked, e.g. by Cloudflare). Matching and scheduling logic itself is plain Python — no hallucinated screenings.

## Setup

1. **Watchlist export**: from Letterboxd, export your data (Settings → Data → Export) and place `watchlist.csv` and `watched.csv` at the project root / a `letterboxd-export/` folder — see `scripts/01_check_watchlist.py` and the agent doc for expected paths.
2. **Google Calendar OAuth**: create OAuth credentials in the Google Cloud Console (Desktop app type), download the JSON, and save it as `google-oauth.keys.json` at the project root (see `google-oauth.keys.json.example` for the expected shape). This repo uses [`@cocal/google-calendar-mcp`](https://github.com/cocal/google-calendar-mcp) as the MCP server.
3. **Python deps**: `pip install requests beautifulsoup4 playwright && playwright install chromium` (Playwright is only needed for the LA `good1s.org` scraper).
4. **Anthropic API key**: required for `tools/scrape_good1s.py` and `tools/extract_cinema_screenings.py` (set `ANTHROPIC_API_KEY` in your environment).
5. Create two Google Calendars (or one, if you only run one city) and note their calendar IDs for the agent config.

## Usage

```bash
python run.py                       # NYC, next 14 days (default)
python run.py --city nyc --days 21  # NYC, next 3 weeks
python run.py --city la             # LA
python run.py --force               # bypass cache, re-scrape everything
```

Then hand off to the Claude Code agent in [`docs/agent.md`](docs/agent.md) (drop it in `~/.claude/agents/`) to review matches and push confirmed events to your calendar.

## Notes / known limitations

- Metrograph's listing page has occasionally returned screenings with no parseable date (the extractor correctly returns `""` rather than guessing) — cross-referencing against an independently-scraped source for the same film/time can sometimes recover it, but not always.
- Individual cinema scrapers can break when a site's structure changes; `docs/agent.md` documents a WebFetch-based fallback for when a scraper returns 0 results.

## License

MIT — see [LICENSE](LICENSE).
