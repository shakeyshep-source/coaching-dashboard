"""
backfill_activities.py — RAW DATA LAYER (one-off)

Job: fetch activities from a start date and merge them into
garmin_activities.json. Nothing else.

Why it exists: the daily pull fetches a 56-day window, so the activity
history began on 2 July. Easy-run efficiency — pace per heartbeat, the
clearest everyday measure of aerobic fitness — is only meaningful across
months, and the runs that would show a January-to-August change are
sitting in Garmin's account rather than ours.

Fetched in chunks because a single request spanning eight months is
unreliable, and merged by activity_id so re-running costs nothing and
never duplicates.

    python backfill_activities.py             # from 2026-01-01
    python backfill_activities.py 2025-06-01  # from another start date

Same row schema as garmin_pull.pull_activities — it reuses that module's
parsing so the two can never drift apart.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta

from garminconnect import Garmin

import garmin_pull

ACTIVITIES_FILE = garmin_pull.ACTIVITIES_FILE
DEFAULT_START = "2026-01-01"
CHUNK_DAYS = 60


def parse_activity(a):
    """One raw Garmin activity to our row. Mirrors pull_activities."""
    distance_m = a.get("distance")
    duration_s = a.get("duration")
    avg_speed = a.get("averageSpeed")
    return {
        "date": (a.get("startTimeLocal") or "")[:10],
        "activity_id": a.get("activityId"),
        "name": a.get("activityName"),
        "type": (a.get("activityType") or {}).get("typeKey"),
        "distance_km": round(distance_m / 1000, 2) if distance_m else None,
        "duration_min": round(duration_s / 60, 1) if duration_s else None,
        "avg_pace_min_per_km": round((1000 / avg_speed) / 60, 2) if avg_speed else None,
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "elevation_gain_m": a.get("elevationGain"),
        "cadence": a.get("averageRunningCadenceInStepsPerMinute"),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
    }


def chunks(start, end, days=CHUNK_DAYS):
    current = start
    while current <= end:
        stop = min(current + timedelta(days=days - 1), end)
        yield current, stop
        current = stop + timedelta(days=1)


def main():
    start = datetime.strptime(
        sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START, "%Y-%m-%d"
    ).date()
    end = date.today()

    try:
        with open(ACTIVITIES_FILE) as f:
            known = {a.get("activity_id"): a for a in json.load(f)}
    except FileNotFoundError:
        known = {}
    before = len(known)

    client = Garmin()
    client.login(tokenstore=os.environ.get("GARMIN_TOKENSTORE", garmin_pull.TOKENSTORE))

    for chunk_start, chunk_end in chunks(start, end):
        raw = garmin_pull.safe_get(
            client.get_activities_by_date, chunk_start.isoformat(), chunk_end.isoformat()
        ) or []
        added = 0
        for a in raw:
            row = parse_activity(a)
            # Existing rows win: the daily pull has fresher data for
            # anything inside its window, and a backfill must not
            # overwrite it with an older snapshot.
            if row["activity_id"] and row["activity_id"] not in known:
                known[row["activity_id"]] = row
                added += 1
        print(f"  {chunk_start} to {chunk_end}: {len(raw)} found, {added} new")

    rows = sorted(known.values(), key=lambda a: (a.get("date") or "", a.get("activity_id") or 0))
    with open(ACTIVITIES_FILE, "w") as f:
        json.dump(rows, f, indent=2)

    print(f"\n{len(rows) - before} new activities, {len(rows)} held in {ACTIVITIES_FILE}")
    if rows:
        print(f"Spans {rows[0]['date']} to {rows[-1]['date']}")


if __name__ == "__main__":
    main()
