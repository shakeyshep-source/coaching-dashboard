"""
backfill_vo2max.py — RAW DATA LAYER (one-off, then kept topped up)

Job: fetch VO2max as far back as Garmin holds it and write it to
vo2max_history.json. Nothing else.

Why it exists: garmin_history.json only accumulates days the automation
has seen, so it starts on 2026-07-29. VO2max is the one metric where the
interesting story is a year long — 54 in January against 59 now — and
that history sits in Garmin's account, not ours. get_max_metrics(date)
returns the VO2max standing on any past date, so it can be recovered.

Sampled weekly rather than daily. Garmin revises VO2max every few days
at most, so weekly is enough resolution for a trend across months, and
it keeps a backfill from January to ~35 requests instead of ~240.

    python backfill_vo2max.py             # from 2026-01-01 to today
    python backfill_vo2max.py 2025-01-01  # from another start date

Safe to re-run: existing dates are kept, new ones merged in.

LOCKED SCHEMA (one row per sampled date):

    {"date": "YYYY-MM-DD", "vo2max": float}
"""

import json
import os
import sys
from datetime import date, datetime, timedelta

from garminconnect import Garmin

TOKENSTORE = os.environ.get("GARMIN_TOKENSTORE", "/home/shakeyshep/.garmin_tokens")
OUTPUT_FILE = "vo2max_history.json"
DEFAULT_START = "2026-01-01"
SAMPLE_EVERY_DAYS = 7


def parse_vo2max(payload):
    """Pull the running VO2max out of Garmin's max-metrics payload.

    The endpoint returns a single-item list, with running under "generic"
    and cycling under "cycling" — we want generic. Kept as a pure
    function so it is testable without a Garmin session.
    """
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return None
    generic = payload.get("generic") or {}
    value = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")
    return round(float(value), 1) if value else None


def sample_dates(start, end, step_days=SAMPLE_EVERY_DAYS):
    """Weekly samples, always including the end date."""
    dates, current = [], start
    while current < end:
        dates.append(current)
        current += timedelta(days=step_days)
    dates.append(end)
    return dates


def main():
    start = datetime.strptime(
        sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START, "%Y-%m-%d"
    ).date()
    end = date.today()

    try:
        with open(OUTPUT_FILE) as f:
            existing = {row["date"]: row for row in json.load(f)}
    except FileNotFoundError:
        existing = {}

    client = Garmin()
    client.login(tokenstore=TOKENSTORE)

    fetched = failed = 0
    for d in sample_dates(start, end):
        key = d.isoformat()
        if key in existing:
            continue
        try:
            value = parse_vo2max(client.get_max_metrics(key))
        except Exception as exc:
            failed += 1
            print(f"  {key}: failed ({type(exc).__name__})")
            continue
        if value is None:
            # Before he owned the watch, or a week with no qualifying run.
            continue
        existing[key] = {"date": key, "vo2max": value}
        fetched += 1
        print(f"  {key}: {value}")

    rows = sorted(existing.values(), key=lambda r: r["date"])
    with open(OUTPUT_FILE, "w") as f:
        json.dump(rows, f, indent=2)

    print(f"\n{fetched} new reading(s), {failed} failed, {len(rows)} held in {OUTPUT_FILE}")
    if rows:
        print(f"Spans {rows[0]['date']} ({rows[0]['vo2max']}) "
              f"to {rows[-1]['date']} ({rows[-1]['vo2max']})")


if __name__ == "__main__":
    main()
