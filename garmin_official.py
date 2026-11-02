"""
garmin_official.py — RAW DATA LAYER, official route (shadow mode, Nov 2026)

The same job as garmin_pull.py — wellness days, activities, laps and
within-run samples — but through Garmin's official Personal API
(free personal key, Garmin Connect → Settings → Personal API Key, stored
as the GARMIN_API_KEY secret; the key expires 30 Oct 2027).

For now it writes to shadow/ only, with the same file names and the same
locked schemas, so the two routes can be compared day by day before the
dashboard is switched over (compare_official.py). Nothing downstream
reads shadow/.

What the official API does not give, and is left to garmin_pull.py:
VO2 max, and Garmin's own acute/chronic training load and ACWR. Instead
each activity's own training load is kept, and an acute (7-day sum) and
chronic (28-day sum / 4) load are worked out from those, as extra fields
— Garmin smooths its version differently, so the ratio is what to
compare, not the raw numbers.

Auth (Garmin's Personal API FAQ, confirmed by garmin_api_probe.py):
POST the key to the token endpoint with "Authorization: ApiKey <key>";
the reply is a plain-text access token valid 24 h, sent as Bearer.
"""

import io
import json
import os
import sys
import zipfile
from datetime import date, datetime, timedelta

import requests

BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
TIMEOUT = 30
OUT_DIR = "shadow"
DAYS_TO_PULL = 14
ACTIVITY_DAYS_TO_PULL = 56
LAP_ACTIVITY_TYPES = ("running", "trail_running", "track_running", "treadmill_running")
STREAM_SAMPLES = 300
STREAM_MIN_DURATION_MIN = 40


# --- pure parsing, testable without a Garmin session ----------------------

def wellness_row(date_str, hrv, sleep, summary):
    """One day in garmin_data.json's locked schema, from the three
    official payloads (any of which may be None)."""
    hrv_summary = (hrv or {}).get("hrvSummary") or {}
    sleep = sleep or {}
    # dailySleep returns the sleep DTO at the top level; tolerate the
    # unofficial nesting too in case the shape moves.
    if "dailySleepDTO" in sleep:
        sleep = sleep.get("dailySleepDTO") or {}
    sleep_seconds = sleep.get("sleepTimeSeconds")
    summary = summary or {}
    return {
        "date": date_str,
        "rhr": summary.get("restingHeartRate"),
        "hrv_last_night": hrv_summary.get("lastNightAvg"),
        "hrv_status": hrv_summary.get("status"),
        "hrv_weekly_avg": hrv_summary.get("weeklyAvg"),
        "sleep_score": ((sleep.get("sleepScores") or {}).get("overall") or {}).get("value"),
        "sleep_duration_hrs": round(sleep_seconds / 3600, 2) if sleep_seconds else None,
        "body_battery_high": summary.get("bodyBatteryHighestValue"),
        "body_battery_low": summary.get("bodyBatteryLowestValue"),
        "training_load_acute": None,
        "training_load_chronic": None,
        "vo2max": None,
        "vo2max_date": None,
        "training_status_feedback": None,
    }


def activity_row(a):
    """One activity in garmin_activities.json's locked schema, plus the
    extra training_load field (an addition, not a rename)."""
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
        "training_load": a.get("activityTrainingLoad"),
    }


def laps_from_fit(laps):
    """FIT 'lap' messages (as dicts) → garmin_laps.json lap rows.

    FIT stores running cadence in strides per minute, with the half
    stride in a separate fractional field; the unofficial route reports
    steps per minute, so both are combined and doubled to match."""
    rows = []
    for i, lap in enumerate(laps, start=1):
        distance_m = lap.get("total_distance")
        duration_s = lap.get("total_timer_time") or lap.get("total_elapsed_time")
        pace = None
        if distance_m and duration_s and distance_m > 0:
            pace = round((duration_s / 60) / (distance_m / 1000), 2)
        cadence = lap.get("avg_running_cadence") or lap.get("avg_cadence")
        if cadence is not None:
            cadence = round((cadence + (lap.get("avg_fractional_cadence") or 0)) * 2, 1)
        intensity = lap.get("intensity")
        rows.append({
            "index": i,
            "intensity_type": str(intensity).upper() if intensity is not None else None,
            "distance_km": round(distance_m / 1000, 3) if distance_m else None,
            "duration_sec": round(duration_s, 1) if duration_s else None,
            "pace_min_per_km": pace,
            "avg_hr": lap.get("avg_heart_rate"),
            "max_hr": lap.get("max_heart_rate"),
            "cadence": cadence,
            "elevation_gain_m": lap.get("total_ascent"),
        })
    return rows


def samples_from_fit(records, target=STREAM_SAMPLES):
    """FIT 'record' messages (as dicts) → garmin_streams.json samples,
    thinned evenly to about `target` points."""
    timed = [r for r in records if r.get("timestamp") is not None]
    if not timed:
        return []
    start = timed[0]["timestamp"]
    n = len(timed)
    if n > target:
        # Evenly spaced picks, first and last included — an integer step
        # would overshoot the target by up to double.
        picks = sorted({round(i * (n - 1) / (target - 1)) for i in range(target)})
        timed = [timed[i] for i in picks]
    rows = []
    for r in timed:
        t = r["timestamp"]
        elapsed = (t - start).total_seconds() if isinstance(t, datetime) else float(t - start)
        distance = r.get("distance")
        speed = r.get("enhanced_speed", r.get("speed"))
        altitude = r.get("enhanced_altitude", r.get("altitude"))
        rows.append({
            "t_sec": round(elapsed, 1),
            "hr": r.get("heart_rate"),
            "speed_mps": round(speed, 3) if speed is not None else None,
            "distance_km": round(distance / 1000, 4) if distance is not None else None,
            "elevation_m": round(altitude, 1) if altitude is not None else None,
        })
    return rows


def add_load_estimates(days, activities):
    """Acute = sum of activity training load over the 7 days to a date;
    chronic = 28-day sum / 4. Written into the wellness rows' existing
    training_load fields, plus acwr_estimate (an added field)."""
    load_by_date = {}
    for a in activities:
        if a.get("training_load"):
            load_by_date[a["date"]] = load_by_date.get(a["date"], 0) + a["training_load"]
    for row in days:
        d = date.fromisoformat(row["date"])
        acute = sum(load_by_date.get((d - timedelta(days=k)).isoformat(), 0) for k in range(7))
        chronic = sum(load_by_date.get((d - timedelta(days=k)).isoformat(), 0) for k in range(28)) / 4
        row["training_load_acute"] = round(acute, 1)
        row["training_load_chronic"] = round(chronic, 1)
        row["acwr_estimate"] = round(acute / chronic, 2) if chronic else None
    return days


# --- network ---------------------------------------------------------------

class Official:
    def __init__(self, key):
        r = requests.post(BASE + TOKEN_PATH, headers={"Authorization": f"ApiKey {key}"}, timeout=TIMEOUT)
        r.raise_for_status()
        text = r.text.strip()
        try:
            body = json.loads(text)
        except ValueError:
            body = text
        if isinstance(body, dict):
            body = body.get("access_token") or body.get("accessToken") or body.get("token")
        if not body or not isinstance(body, str):
            raise RuntimeError("Token response was not recognisable")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {body}", "Accept": "application/json"})

    def get(self, path, **params):
        try:
            r = self.s.get(BASE + path, params=params or None, timeout=TIMEOUT)
            if r.status_code != 200 or not r.content:
                return None
            return r.json()
        except (requests.RequestException, ValueError):
            return None

    def fit(self, activity_id):
        """The activity's FIT file, unzipped, or None."""
        try:
            r = self.s.get(f"{BASE}/download-service/files/activity/{activity_id}",
                           headers={"Accept": "*/*"}, timeout=TIMEOUT)
        except requests.RequestException:
            return None
        if r.status_code != 200 or not r.content:
            return None
        data = r.content
        if data[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".fit")]
                if not names:
                    return None
                data = z.read(names[0])
        return data


def read_fit(data):
    """(laps, records) as lists of dicts from FIT bytes."""
    import fitdecode
    laps, records = [], []
    with fitdecode.FitReader(io.BytesIO(data)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name not in ("lap", "record"):
                continue
            values = {f.name: f.value for f in frame.fields}
            (laps if frame.name == "lap" else records).append(values)
    return laps, records


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def main():
    key = os.environ.get("GARMIN_API_KEY")
    if not key:
        print("GARMIN_API_KEY not set - shadow pull skipped.")
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    api = Official(key)
    today = date.today()

    days = []
    for i in range(DAYS_TO_PULL):
        d = (today - timedelta(days=i)).isoformat()
        days.append(wellness_row(
            d,
            api.get(f"/hrv-service/hrv/{d}"),
            api.get("/sleep-service/sleep/dailySleep", date=d),
            api.get("/usersummary-service/usersummary/daily", calendarDate=d),
        ))
    days.sort(key=lambda r: r["date"])

    start = (today - timedelta(days=ACTIVITY_DAYS_TO_PULL)).isoformat()
    listing = api.get("/activitylist-service/activities/search/activities", limit=100, start=0) or []
    fresh = [activity_row(a) for a in listing if (a.get("startTimeLocal") or "")[:10] >= start]

    known = {a["activity_id"]: a for a in load(f"{OUT_DIR}/garmin_activities.json", [])}
    for a in fresh:
        known[a["activity_id"]] = a
    activities = sorted(known.values(), key=lambda a: (a.get("date") or "", a.get("activity_id") or 0))
    add_load_estimates(days, activities)
    save(f"{OUT_DIR}/garmin_data.json", days)

    history = {r["date"]: r for r in load(f"{OUT_DIR}/garmin_history.json", [])}
    for r in days:
        history[r["date"]] = r
    save(f"{OUT_DIR}/garmin_history.json", sorted(history.values(), key=lambda r: r["date"]))
    save(f"{OUT_DIR}/garmin_activities.json", activities)

    laps = load(f"{OUT_DIR}/garmin_laps.json", [])
    streams = load(f"{OUT_DIR}/garmin_streams.json", [])
    have_laps = {r["activity_id"] for r in laps}
    have_streams = {r["activity_id"] for r in streams}
    fetched = 0
    for a in activities:
        if (a.get("type") or "") not in LAP_ACTIVITY_TYPES:
            continue
        want_stream = (a.get("duration_min") or 0) >= STREAM_MIN_DURATION_MIN and a["activity_id"] not in have_streams
        if a["activity_id"] in have_laps and not want_stream:
            continue
        data = api.fit(a["activity_id"])
        if not data:
            continue
        try:
            fit_laps, records = read_fit(data)
        except Exception as e:  # a bad file must not stop the rest
            print(f"FIT parse failed for {a['activity_id']}: {type(e).__name__}")
            continue
        fetched += 1
        meta = {"activity_id": a["activity_id"], "date": a.get("date"), "type": a.get("type")}
        if a["activity_id"] not in have_laps and fit_laps:
            laps.append({**meta, "name": a.get("name"), "laps": laps_from_fit(fit_laps)})
        if want_stream:
            samples = samples_from_fit(records)
            if len(samples) >= 20:
                streams.append({**meta, "samples": samples})
    laps.sort(key=lambda r: (r.get("date") or "", r.get("activity_id") or 0))
    streams.sort(key=lambda r: (r.get("date") or "", r.get("activity_id") or 0))
    save(f"{OUT_DIR}/garmin_laps.json", laps)
    save(f"{OUT_DIR}/garmin_streams.json", streams)

    print(f"Official (shadow): {len(days)} days, {len(fresh)} recent activities, "
          f"{fetched} FIT file(s) read, {len(laps)} lap sets, {len(streams)} streams")
    return 0


if __name__ == "__main__":
    sys.exit(main())
