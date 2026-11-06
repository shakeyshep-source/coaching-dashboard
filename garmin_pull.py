"""
garmin_pull.py — RAW DATA LAYER

Job: fetch data from Garmin and write garmin_data.json, garmin_history.json,
garmin_activities.json, garmin_laps.json and garmin_streams.json. Nothing
else. No calculations beyond unit conversion, no renaming games.

Two routes into Garmin, since 6 Nov 2026:

  * PRIMARY — Garmin's official Personal API (garmin_official.py, key in
    the GARMIN_API_KEY secret, expires 30 Oct 2027): wellness days, the
    activity list, and laps + within-run samples from each run's FIT file.
    Switched to after a shadow run (2-6 Nov) matched the old route on
    every wellness field, every activity, all 510 laps, and stream HR
    within 1.1 bpm.
  * SECONDARY — the unofficial garminconnect login (GARMIN_TOKENSTORE).
    Still the only source of Garmin's own training status, acute/chronic
    load, ACWR and VO2 max, which the official API does not offer; and
    the fallback for anything the official route fails to return — the
    whole route if its token cannot be had, or a single day, activity
    list or FIT file if one call fails.

Either route alone keeps the dashboard running. If the unofficial login
breaks, ACWR falls back to an estimate from per-activity training load
(acwr_estimate, see garmin_official.add_load_estimates) and VO2 max
simply stops updating until it is fixed.

LOCKED SCHEMA — do not change these field names without updating
build_computed.py and every chart that reads computed_data.json.

    {
      "date": "YYYY-MM-DD",
      "rhr": float | null,
      "hrv_last_night": float | null,
      "hrv_status": str | null,
      "hrv_weekly_avg": float | null,   # Garmin's own 7-day average
      "sleep_score": float | null,
      "sleep_duration_hrs": float | null,
      "body_battery_high": float | null,
      "body_battery_low": float | null,
      "training_load_acute": float | null,
      "training_load_chronic": float | null
    }

If Garmin's API shape ever changes again, this file and
garmin_official.py are the ONLY files that should need editing.
Everything downstream reads these exact keys.
"""

import json
import os
from datetime import date, timedelta

from garmin_official import (Official, activity_row, add_load_estimates, laps_from_fit,
                             read_fit, samples_from_fit, wellness_row)

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
# Within-run HR and speed samples, for aerobic decoupling. Garmin will
# downsample server-side to whatever we ask for, so we request a fixed
# ~300 points rather than storing a 1Hz series that would add megabytes
# to a repo committed three times a day. That is enough resolution for
# half-vs-half comparison and nothing here needs more.
STREAMS_FILE = "garmin_streams.json"
STREAM_SAMPLES = 300
# Decoupling is meaningless on a short run - HR lag and the warm-up
# dominate. Only runs long enough to have a steady middle are fetched.
STREAM_MIN_DURATION_MIN = 40


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
        "hrv_weekly_avg": None,
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
        # Garmin's own 7-day average — the figure on the watch's HRV
        # screen. Stored so the dashboard can show the number he is
        # actually looking at, rather than only our rolling reference,
        # which is deliberately computed a different way (see
        # rolling_baseline in build_computed.py) and so never matches.
        row["hrv_weekly_avg"] = summary.get("weeklyAvg")

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

    for k, v in pull_training_status(client, date_str).items():
        if v is not None:
            row[k] = v

    return row


# Fields only the unofficial route has: Garmin's own training status,
# load and ACWR, and VO2 max.
STATUS_FIELDS = ("training_load_acute", "training_load_chronic", "acwr_garmin",
                 "acwr_status", "training_status_feedback", "vo2max", "vo2max_date")
WELLNESS_CORE = ("rhr", "hrv_last_night", "sleep_score", "body_battery_high")


def pull_training_status(client, date_str):
    """Garmin's own training status for a day (unofficial route only).
    Every STATUS_FIELDS key is returned, None where Garmin gave nothing."""
    out = dict.fromkeys(STATUS_FIELDS)
    status = safe_get(client.get_training_status, date_str)
    if not status:
        return out
    try:
        latest = (status.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
        if latest:
            device_id = list(latest.keys())[0]
            acute_dto = latest[device_id].get("acuteTrainingLoadDTO", {})
            out["training_load_acute"] = acute_dto.get("dailyTrainingLoadAcute")
            out["training_load_chronic"] = acute_dto.get("dailyTrainingLoadChronic")
            out["acwr_garmin"] = acute_dto.get("dailyAcuteChronicWorkloadRatio")
            out["acwr_status"] = acute_dto.get("acwrStatus")
            out["training_status_feedback"] = latest[device_id].get("trainingStatusFeedbackPhrase")
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    try:
        vo2 = (status.get("mostRecentVO2Max") or {}).get("generic") or {}
        out["vo2max"] = vo2.get("vo2MaxValue")
        out["vo2max_date"] = vo2.get("calendarDate")
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return out


def has_wellness(row):
    return any(row.get(k) is not None for k in WELLNESS_CORE)


def official_wellness(api, date_str):
    return wellness_row(
        date_str,
        api.get(f"/hrv-service/hrv/{date_str}"),
        api.get("/sleep-service/sleep/dailySleep", date=date_str),
        api.get("/usersummary-service/usersummary/daily", calendarDate=date_str),
    )


def build_days(api, client, dates):
    """Wellness rows for `dates`: official first, unofficial for any day the
    official route returned nothing for. Returns (rows, status_by_date,
    sources) — status is overlaid later, after the load estimates, so
    Garmin's own acute/chronic load wins wherever it exists."""
    rows, status_by_date = [], {}
    sources = {"official": 0, "unofficial": 0, "none": 0}
    for d in dates:
        row = official_wellness(api, d) if api else None
        if row is not None and has_wellness(row):
            sources["official"] += 1
            status_by_date[d] = pull_training_status(client, d) if client else {}
        elif client:
            fallback = pull_day(client, d)
            if row is None or has_wellness(fallback):
                row = fallback
                sources["unofficial" if has_wellness(fallback) else "none"] += 1
            else:
                sources["none"] += 1
            status_by_date[d] = {k: fallback.get(k) for k in STATUS_FIELDS}
        else:
            sources["none"] += 1
            status_by_date[d] = {}
        rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows, status_by_date, sources


def overlay_status(rows, status_by_date):
    for row in rows:
        for k, v in (status_by_date.get(row["date"]) or {}).items():
            if v is not None:
                row[k] = v
    return rows


def official_activities(api, since):
    """Activity rows since `since` from the official list, paged; None if
    the list could not be had at all (so the caller falls back)."""
    rows = []
    for page in range(5):
        listing = api.get("/activitylist-service/activities/search/activities",
                          limit=100, start=page * 100)
        if listing is None:
            return None if page == 0 else rows
        rows += [activity_row(a) for a in listing if (a.get("startTimeLocal") or "")[:10] >= since]
        oldest = min(((a.get("startTimeLocal") or "")[:10] for a in listing), default="")
        if len(listing) < 100 or oldest < since:
            break
    rows.sort(key=lambda r: (r["date"], r["activity_id"] or 0))
    return rows


def merge_activities(existing, fresh):
    """A freshly pulled activity wins — Garmin revises HR and pace after a
    sync — but anything outside the window is kept, and a training_load
    already held is not lost if this pull came by the route without it."""
    known = {a.get("activity_id"): a for a in existing}
    for a in fresh:
        old = known.get(a.get("activity_id"))
        if old and a.get("training_load") is None and old.get("training_load") is not None:
            a = {**a, "training_load": old["training_load"]}
        known[a.get("activity_id")] = a
    return sorted(known.values(), key=lambda a: (a.get("date") or "", a.get("activity_id") or 0))


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


def parse_stream(payload):
    """Turn Garmin's activity-details payload into time/HR/speed samples.

    The payload gives a list of metric descriptors and a parallel list of
    value arrays, so every field has to be looked up by key rather than
    position — the indices differ between activities.

    LOCKED SCHEMA (one row per sample, inside garmin_streams.json):

        {"t_sec": float, "hr": float|null,
         "speed_mps": float|null, "distance_km": float|null,
         "elevation_m": float|null}
    """
    descriptors = (payload or {}).get("metricDescriptors") or []
    index = {}
    for d in descriptors:
        key = d.get("key")
        if key is not None and d.get("metricsIndex") is not None:
            index[key] = d["metricsIndex"]

    def value(metrics, key):
        i = index.get(key)
        if i is None or i >= len(metrics):
            return None
        return metrics[i]

    rows = []
    start_ms = None
    for point in (payload or {}).get("activityDetailMetrics") or []:
        metrics = point.get("metrics") or []
        elapsed = value(metrics, "sumElapsedDuration")
        if elapsed is None:
            elapsed = value(metrics, "sumDuration")
        if elapsed is None:
            stamp = value(metrics, "directTimestamp")
            if stamp is None:
                continue
            start_ms = start_ms if start_ms is not None else stamp
            elapsed = (stamp - start_ms) / 1000.0

        distance = value(metrics, "sumDistance")
        rows.append({
            "t_sec": round(float(elapsed), 1),
            "hr": value(metrics, "directHeartRate"),
            "speed_mps": value(metrics, "directSpeed"),
            # Garmin reports sumDistance in metres on some activities and
            # kilometres on others; normalise on the way in.
            "distance_km": (round(distance / 1000, 4) if distance and distance > 100
                            else round(distance, 4) if distance else None),
            "elevation_m": value(metrics, "directElevation"),
        })
    rows.sort(key=lambda r: r["t_sec"])
    return rows


def pull_laps_and_streams(api, client, activities, laps, streams):
    """Laps for every run not yet held, and within-run samples for runs of
    STREAM_MIN_DURATION_MIN or more. Official route: one FIT file gives
    both. Unofficial route: splits and details requests, used only for
    what the FIT file could not supply.

    Only activities missing from the files are fetched, so a normal day
    costs one or two requests."""
    have_laps = {r.get("activity_id") for r in laps}
    have_streams = {r.get("activity_id") for r in streams}
    counts = {"fit": 0, "unofficial_laps": 0, "unofficial_streams": 0}
    for a in activities:
        activity_id = a.get("activity_id")
        if not activity_id or (a.get("type") or "") not in LAP_ACTIVITY_TYPES:
            continue
        need_laps = activity_id not in have_laps
        need_stream = (activity_id not in have_streams
                       and (a.get("duration_min") or 0) >= STREAM_MIN_DURATION_MIN)
        if not need_laps and not need_stream:
            continue
        meta = {"activity_id": activity_id, "date": a.get("date"), "type": a.get("type")}

        if api:
            data = api.fit(activity_id)
            if data:
                try:
                    fit_laps, records = read_fit(data)
                except Exception as e:  # a bad file must not stop the rest
                    print(f"FIT parse failed for {activity_id}: {type(e).__name__}")
                    fit_laps, records = [], []
                if fit_laps or records:
                    counts["fit"] += 1
                if need_laps and fit_laps:
                    laps.append({**meta, "name": a.get("name"), "laps": laps_from_fit(fit_laps)})
                    need_laps = False
                if need_stream:
                    samples = samples_from_fit(records)
                    if len(samples) >= 20:
                        streams.append({**meta, "samples": samples})
                        need_stream = False

        if client and need_laps:
            rows = parse_laps(safe_get(client.get_activity_splits, activity_id))
            if rows:
                laps.append({**meta, "name": a.get("name"), "laps": rows})
                counts["unofficial_laps"] += 1
        if client and need_stream:
            samples = parse_stream(safe_get(client.get_activity_details, activity_id, STREAM_SAMPLES, 0))
            if len(samples) >= 20:
                streams.append({**meta, "samples": samples})
                counts["unofficial_streams"] += 1

    laps.sort(key=lambda r: (r.get("date") or "", r.get("activity_id") or 0))
    streams.sort(key=lambda r: (r.get("date") or "", r.get("activity_id") or 0))
    return laps, streams, counts


def official_session():
    key = os.environ.get("GARMIN_API_KEY")
    if not key:
        print("GARMIN_API_KEY not set - official route skipped.")
        return None
    try:
        return Official(key)
    except Exception as e:
        print(f"Official API token failed ({type(e).__name__}) - using the unofficial route.")
        return None


def unofficial_session():
    try:
        from garminconnect import Garmin
        client = Garmin()
        client.login(tokenstore=TOKENSTORE)
        return client
    except Exception as e:
        print(f"Unofficial login failed ({type(e).__name__}) - no training status, "
              f"ACWR from the estimate, VO2 max not updated.")
        return None


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def main():
    api = official_session()
    client = unofficial_session()
    if not api and not client:
        raise SystemExit("Neither Garmin route is available - nothing pulled.")

    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(DAYS_TO_PULL)]
    results, status_by_date, day_sources = build_days(api, client, dates)

    since = (today - timedelta(days=ACTIVITY_DAYS_TO_PULL)).isoformat()
    fresh = official_activities(api, since) if api else None
    activity_source = "official"
    if fresh is None:
        fresh = pull_activities(client) if client else []
        activity_source = "unofficial" if client else "none"
    activities = merge_activities(load_json(ACTIVITIES_FILE), fresh)

    # Our own acute/chronic estimate first, then Garmin's own load and
    # ACWR laid over it wherever the unofficial route supplied them.
    add_load_estimates(results, activities)
    overlay_status(results, status_by_date)

    for row in results:
        print(f"{row['date']}  RHR: {row['rhr']}  HRV: {row['hrv_last_night']}  "
              f"Sleep: {row['sleep_score']}  BB: {row['body_battery_high']}")
    print(f"Wellness by route: {day_sources}")

    save_json(OUTPUT_FILE, results)
    print(f"\nSaved {len(results)} days to {OUTPUT_FILE}")

    by_date = {r["date"]: r for r in load_json(HISTORY_FILE)}
    for r in results:
        # A freshly pulled day wins: Garmin backfills sleep and HRV for a
        # day some hours after it ends, so today's value for yesterday is
        # more complete than yesterday's was. But an empty pull (both
        # routes failed for that day) never blanks a day already held.
        if has_wellness(r) or not has_wellness(by_date.get(r["date"]) or {}):
            by_date[r["date"]] = r
    history = sorted(by_date.values(), key=lambda r: r["date"])
    save_json(HISTORY_FILE, history)
    print(f"Wellness history now spans {len(history)} days "
          f"({history[0]['date']} to {history[-1]['date']})")

    save_json(ACTIVITIES_FILE, activities)
    print(f"Saved {len(fresh)} recent activities ({activity_source} route); "
          f"{len(activities)} held in {ACTIVITIES_FILE}")

    laps, streams, counts = pull_laps_and_streams(
        api, client, activities, load_json(LAPS_FILE), load_json(STREAMS_FILE))
    save_json(LAPS_FILE, laps)
    save_json(STREAMS_FILE, streams)
    print(f"Laps/streams fetched: {counts}; {len(laps)} lap sets, {len(streams)} streams held")


if __name__ == "__main__":
    main()
