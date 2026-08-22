"""
garmin_pull.py — RAW DATA LAYER

Job: fetch data from Garmin Connect and write it to garmin_data.json.
Nothing else. No calculations, no derived fields, no renaming games.

LOCKED SCHEMA — do not change these field names without updating
build_computed.py and every chart that reads computed_data.json.

    {
      "date": "YYYY-MM-DD",
      "rhr": float | null,
      "hrv_last_night": float | null,
      "hrv_status": str | null,
      "sleep_score": float | null,
      "sleep_duration_hrs": float | null,
      "body_battery_high": float | null,
      "body_battery_low": float | null,
      "training_load_acute": float | null,
      "training_load_chronic": float | null
    }

If Garmin's API shape ever changes again, this is the ONLY file that
should need editing. Everything downstream reads these exact keys.
"""

import json
import os
from datetime import date, timedelta
from garminconnect import Garmin

# Tokenstore path: overridable via env var so the same script runs on the
# laptop (default path) and in GitHub Actions (tokens restored from the
# GARMIN_TOKENS_B64 secret into the runner's home directory).
TOKENSTORE = os.environ.get("GARMIN_TOKENSTORE", "/home/shakeyshep/.garmin_tokens")
OUTPUT_FILE = "garmin_data.json"
# garmin_data.json is a rolling 14-day window — days fall out of it as
# it moves. Recovery trends need months, not a fortnight, so every day
# seen is also accumulated here and never dropped.
HISTORY_FILE = "garmin_history.json"
ACTIVITIES_FILE = "garmin_activities.json"
# Per-lap splits. garmin_activities.json carries whole-run averages only,
# which say almost nothing about a rep session: "14.01 km at 4:39/km"
# hides five 1200s inside 1.6 seconds of each other. One request per
# activity, so results are kept and only new activities are fetched.
LAPS_FILE = "garmin_laps.json"
DAYS_TO_PULL = 14  # rolling window; adjust as needed
ACTIVITY_DAYS_TO_PULL = 56  # 8 weeks — enough for weekly trend stats
LAP_ACTIVITY_TYPES = ("running", "trail_running", "track_running", "treadmill_running")


def safe_get(fn, *args):
    """Call a Garmin API method, return None on any failure instead of crashing."""
    try:
        return fn(*args)
    except Exception:
        return None


def pull_day(client, date_str):
    row = {
        "date": date_str,
        "rhr": None,
        "hrv_last_night": None,
        "hrv_status": None,
        "sleep_score": None,
        "sleep_duration_hrs": None,
        "body_battery_high": None,
        "body_battery_low": None,
        "training_load_acute": None,
        "training_load_chronic": None,
        "vo2max": None,
        "vo2max_date": None,
        "training_status_feedback": None,
    }

    # --- RHR ---
    rhr_data = safe_get(client.get_rhr_day, date_str)
    if rhr_data:
        vals = (
            rhr_data.get("allMetrics", {})
            .get("metricsMap", {})
            .get("WELLNESS_RESTING_HEART_RATE", [])
        )
        if vals:
            row["rhr"] = vals[0].get("value")

    # --- HRV ---
    hrv_data = safe_get(client.get_hrv_data, date_str)
    if hrv_data:
        summary = hrv_data.get("hrvSummary", {})
        row["hrv_last_night"] = summary.get("lastNightAvg")
        row["hrv_status"] = summary.get("status")

    # --- Sleep (separate endpoint, nested under dailySleepDTO) ---
    sleep_data = safe_get(client.get_sleep_data, date_str)
    if sleep_data:
        dto = sleep_data.get("dailySleepDTO", {})
        row["sleep_score"] = dto.get("sleepScores", {}).get("overall", {}).get("value")
        sleep_seconds = dto.get("sleepTimeSeconds")
        if sleep_seconds:
            row["sleep_duration_hrs"] = round(sleep_seconds / 3600, 2)

    # --- Body battery comes from get_stats ---
    stats = safe_get(client.get_stats, date_str)
    if stats:
        row["body_battery_high"] = stats.get("bodyBatteryHighestValue")
        row["body_battery_low"] = stats.get("bodyBatteryLowestValue")

    # --- Training load / ACWR from get_training_status (Garmin calculates ACWR itself) ---
    status = safe_get(client.get_training_status, date_str)
    if status:
        try:
            latest = (status.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
            if not latest:
                raise KeyError("no training status data for this date")
            device_id = list(latest.keys())[0]
            acute_dto = latest[device_id].get("acuteTrainingLoadDTO", {})
            row["training_load_acute"] = acute_dto.get("dailyTrainingLoadAcute")
            row["training_load_chronic"] = acute_dto.get("dailyTrainingLoadChronic")
            row["acwr_garmin"] = acute_dto.get("dailyAcuteChronicWorkloadRatio")
            row["acwr_status"] = acute_dto.get("acwrStatus")
            row["training_status_feedback"] = latest[device_id].get("trainingStatusFeedbackPhrase")
        except (KeyError, IndexError, TypeError):
            pass

        try:
            vo2 = (status.get("mostRecentVO2Max") or {}).get("generic") or {}
            row["vo2max"] = vo2.get("vo2MaxValue")
            row["vo2max_date"] = vo2.get("calendarDate")
        except (KeyError, IndexError, TypeError, AttributeError):
            pass

    return row


def pull_activities(client):
    """Fetch recent activities (runs etc.) so distance/pace/HR stats are
    automated — the manual log only needs to carry thoughts, RPE and
    achilles score, never numbers the watch already knows.

    LOCKED SCHEMA (garmin_activities.json, one row per activity):

        {
          "date": "YYYY-MM-DD",
          "activity_id": int,
          "name": str,
          "type": str,              # e.g. "running", "trail_running"
          "distance_km": float | null,
          "duration_min": float | null,
          "avg_pace_min_per_km": float | null,
          "avg_hr": float | null,
          "max_hr": float | null,
          "elevation_gain_m": float | null,
          "cadence": float | null,
          "aerobic_te": float | null,
          "anaerobic_te": float | null
        }
    """
    today = date.today()
    start = (today - timedelta(days=ACTIVITY_DAYS_TO_PULL)).isoformat()
    raw = safe_get(client.get_activities_by_date, start, today.isoformat()) or []

    rows = []
    for a in raw:
        distance_m = a.get("distance")
        duration_s = a.get("duration")
        avg_speed = a.get("averageSpeed")  # m/s
        pace = None
        if avg_speed:
            pace = round((1000 / avg_speed) / 60, 2)  # min per km
        rows.append({
            "date": (a.get("startTimeLocal") or "")[:10],
            "activity_id": a.get("activityId"),
            "name": a.get("activityName"),
            "type": (a.get("activityType") or {}).get("typeKey"),
            "distance_km": round(distance_m / 1000, 2) if distance_m else None,
            "duration_min": round(duration_s / 60, 1) if duration_s else None,
            "avg_pace_min_per_km": pace,
            "avg_hr": a.get("averageHR"),
            "max_hr": a.get("maxHR"),
            "elevation_gain_m": a.get("elevationGain"),
            "cadence": a.get("averageRunningCadenceInStepsPerMinute"),
            "aerobic_te": a.get("aerobicTrainingEffect"),
            "anaerobic_te": a.get("anaerobicTrainingEffect"),
        })

    rows.sort(key=lambda r: (r["date"], r["activity_id"] or 0))
    return rows


def parse_laps(payload):
    """Turn Garmin's split payload into our lap rows.

    LOCKED SCHEMA (one row per lap, inside garmin_laps.json):

        {
          "index": int,             # 1-based, in the order run
          "intensity_type": str|null,  # ACTIVE / REST / WARMUP / COOLDOWN
          "distance_km": float|null,
          "duration_sec": float|null,
          "pace_min_per_km": float|null,
          "avg_hr": float|null,
          "max_hr": float|null,
          "cadence": float|null,
          "elevation_gain_m": float|null
        }

    Kept as a pure function of the payload so it can be tested without a
    Garmin session — the pull itself cannot run outside the Action.
    """
    laps = (payload or {}).get("lapDTOs") or []
    rows = []
    for i, lap in enumerate(laps, start=1):
        distance_m = lap.get("distance")
        duration_s = lap.get("duration")
        pace = None
        if distance_m and duration_s and distance_m > 0:
            pace = round((duration_s / 60) / (distance_m / 1000), 2)
        rows.append({
            "index": i,
            "intensity_type": lap.get("intensityType"),
            "distance_km": round(distance_m / 1000, 3) if distance_m else None,
            "duration_sec": round(duration_s, 1) if duration_s else None,
            "pace_min_per_km": pace,
            "avg_hr": lap.get("averageHR"),
            "max_hr": lap.get("maxHR"),
            "cadence": lap.get("averageRunCadence"),
            "elevation_gain_m": lap.get("elevationGain"),
        })
    return rows


def pull_laps(client, activities, existing):
    """Fetch splits for runs we do not already hold.

    One HTTP request per activity, so this only ever asks about activities
    missing from garmin_laps.json — a normal daily run adds one request,
    and a first run over 8 weeks of history adds ~50 once.
    """
    have = {row.get("activity_id") for row in existing}
    rows = list(existing)
    fetched = 0
    for a in activities:
        activity_id = a.get("activity_id")
        if not activity_id or activity_id in have:
            continue
        if (a.get("type") or "") not in LAP_ACTIVITY_TYPES:
            continue
        payload = safe_get(client.get_activity_splits, activity_id)
        laps = parse_laps(payload)
        if not laps:
            continue
        rows.append({
            "activity_id": activity_id,
            "date": a.get("date"),
            "name": a.get("name"),
            "type": a.get("type"),
            "laps": laps,
        })
        fetched += 1
    rows.sort(key=lambda r: (r.get("date") or "", r.get("activity_id") or 0))
    return rows, fetched


def main():
    client = Garmin()
    client.login(tokenstore=TOKENSTORE)

    today = date.today()
    results = []
    for i in range(DAYS_TO_PULL):
        d = today - timedelta(days=i)
        date_str = d.isoformat()
        row = pull_day(client, date_str)
        results.append(row)
        print(f"{date_str}  RHR: {row['rhr']}  HRV: {row['hrv_last_night']}  "
              f"Sleep: {row['sleep_score']}  BB: {row['body_battery_high']}")

    results.sort(key=lambda r: r["date"])

    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved {len(results)} days to {OUTPUT_FILE}")

    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    except FileNotFoundError:
        history = []
    by_date = {r["date"]: r for r in history}
    for r in results:
        # A freshly pulled day wins: Garmin backfills sleep and HRV for a
        # day some hours after it ends, so today's value for yesterday is
        # more complete than yesterday's was.
        by_date[r["date"]] = r
    history = sorted(by_date.values(), key=lambda r: r["date"])
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)
    print(f"Wellness history now spans {len(history)} days "
          f"({history[0]['date']} to {history[-1]['date']})")

    activities = pull_activities(client)
    with open(ACTIVITIES_FILE, "w") as f:
        json.dump(activities, f, indent=2)
    print(f"Saved {len(activities)} activities to {ACTIVITIES_FILE}")

    try:
        with open(LAPS_FILE) as f:
            existing_laps = json.load(f)
    except FileNotFoundError:
        existing_laps = []
    laps, fetched = pull_laps(client, activities, existing_laps)
    with open(LAPS_FILE, "w") as f:
        json.dump(laps, f, indent=2)
    print(f"Laps: {fetched} new activity/activities fetched, "
          f"{len(laps)} held in {LAPS_FILE}")


if __name__ == "__main__":
    main()
