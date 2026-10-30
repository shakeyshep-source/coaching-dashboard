"""One-off probe of Garmin's official Personal API (Oct 2026).

Answers one question before anything is switched over: does the
Personal API return every field garmin_pull.py currently gets through
the unofficial login? It reports structure only — status codes, key
names and value TYPES — never a value, because Actions logs on this
repo are readable. Nothing is written to the repo.

Run from the Actions tab ("Garmin API probe"). Needs the
GARMIN_API_KEY repo secret: the personal key from Garmin Connect →
Settings → Personal API Key (shown once at creation; lasts a year).

Auth flow (Garmin's Personal API FAQ): POST the key to the token
endpoint with "Authorization: ApiKey <key>" → an OAuth2 access token
valid 24 h → data requests carry "Authorization: Bearer <token>".

Query-parameter names are not in the endpoint list we have, so each
probe tries a few likely spellings and reports which one answered.
"""

import io
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
TIMEOUT = 30

# What the dashboard needs, and the key names the unofficial payloads
# use for it. A probe "covers" a field if any of these names appears
# in a successful response (dotted names must end a key path).
DASHBOARD_FIELDS = {
    "rhr": ["restingHeartRate", "WELLNESS_RESTING_HEART_RATE", "restingHeartRateValue"],
    "hrv_last_night": ["lastNightAvg"],
    "hrv_weekly_avg": ["weeklyAvg"],
    "hrv_status": ["hrvSummary.status"],
    "sleep_score": ["sleepScores", "overallSleepScore", "sleepScore"],
    "sleep_duration": ["sleepTimeSeconds"],
    "body_battery_high_low": ["bodyBatteryHighestValue", "bodyBatteryLowestValue"],
    "vo2max": ["vO2MaxValue", "vo2MaxValue", "vo2MaxRunning", "vo2Max"],
    "training_load_acwr": ["dailyTrainingLoadAcute", "acuteTrainingLoad", "dailyAcuteChronicWorkloadRatio", "acwr"],
    "activity_list": ["activityId"],
    "activity_laps": ["splitSummaries", "lapDTOs", "laps"],
}


def london_dates(days_back):
    today = datetime.now(ZoneInfo("Europe/London")).date()
    return [(today - timedelta(days=n)).isoformat() for n in range(days_back, 0, -1)]


def key_paths(obj, prefix="", depth=0, out=None):
    """Every key path in a JSON payload with its value type, values omitted."""
    if out is None:
        out = {}
    if depth > 5:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            out[path] = type(v).__name__
            key_paths(v, path, depth + 1, out)
    elif isinstance(obj, list) and obj:
        key_paths(obj[0], f"{prefix}[]", depth + 1, out)
    return out


def get_token(key):
    r = requests.post(BASE + TOKEN_PATH, headers={"Authorization": f"ApiKey {key}"}, timeout=TIMEOUT)
    if r.status_code != 200:
        print(f"Token request failed: HTTP {r.status_code}. {r.text[:200]}")
        sys.exit(1)
    # The first run got HTTP 200 with a body that was not labelled JSON,
    # so read the text whatever the content-type claims: a JSON object,
    # a bare JSON string, or the token as plain text.
    ctype = r.headers.get("content-type", "?")
    text = r.text.strip()
    try:
        body = json.loads(text)
    except ValueError:
        body = text
    if isinstance(body, dict):
        for name in ("access_token", "accessToken", "token", "personalApiToken"):
            if body.get(name):
                return body[name], f"JSON object, keys {sorted(body.keys())}"
        print(f"Token response ({ctype}) had no recognised token field. Keys: {sorted(body.keys())}")
        sys.exit(1)
    if isinstance(body, str) and body and " " not in body:
        return body, f"plain token ({ctype}, {len(body)} chars)"
    print(f"Token response ({ctype}, {len(text)} chars) was not a recognisable token. "
          f"Response header names: {sorted(r.headers.keys())}")
    sys.exit(1)


def probe(session, name, path, variants):
    """Try each parameter variant until one answers 200 with content."""
    attempts = []
    for params in variants:
        try:
            r = session.get(BASE + path, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            attempts.append(f"{params} → error {type(e).__name__}")
            continue
        if r.status_code == 200 and r.content:
            try:
                payload = r.json()
            except ValueError:
                attempts.append(f"{params} → 200, not JSON")
                continue
            return {"name": name, "path": path, "ok": True, "params": params,
                    "paths": key_paths(payload), "payload": payload, "attempts": attempts}
        # Error bodies say which parameter is wrong; they carry no data of his.
        attempts.append(f"{params} → HTTP {r.status_code} {r.text[:160]!r}")
    return {"name": name, "path": path, "ok": False, "attempts": attempts}


def probe_fit(session, activity_id):
    r = session.get(f"{BASE}/download-service/files/activity/{activity_id}", timeout=TIMEOUT)
    note = f"HTTP {r.status_code}, {r.headers.get('content-type', '?')}, {len(r.content)} bytes"
    if r.status_code == 200 and r.content[:2] == b"PK":
        try:
            names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
            note += f", zip of {[n.rsplit('.', 1)[-1] for n in names]}"
        except zipfile.BadZipFile:
            note += ", zip unreadable"
    elif r.status_code == 200 and r.content[8:12] == b".FIT":
        note += ", raw FIT file"
    return note


def main():
    key = os.environ.get("GARMIN_API_KEY")
    if not key:
        print("GARMIN_API_KEY secret is not set. Add it under Settings → Secrets and variables → Actions.")
        sys.exit(1)

    token, token_shape = get_token(key)
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    week = london_dates(7)
    day, start, end = week[-1], week[0], week[-1]

    probes = [
        ("HRV, one night", f"/hrv-service/hrv/{day}", [{}]),
        ("HRV, range", f"/hrv-service/hrv/daily/{start}/{end}", [{}]),
        ("Sleep, one night", "/sleep-service/sleep/dailySleep",
         [{"date": day}, {"calendarDate": day}, {"sleepDate": day}]),
        ("Sleep, summaries", "/sleep-service/sleep/dailySleepSummariesByDate",
         [{"startDate": start, "endDate": end}, {"fromDate": start, "untilDate": end}, {"date": day}]),
        ("Daily summary", "/usersummary-service/usersummary/daily",
         [{"calendarDate": day}, {"date": day}]),
        ("Daily summaries v2", "/usersummary-service/v2/dailysummaries",
         [{"startDate": start, "endDate": end}, {"fromDate": start, "untilDate": end}, {"calendarDate": day}]),
        ("Body battery", "/wellness-service/wellness/bodyBattery/reports/daily",
         [{"startDate": start, "endDate": end}, {"fromDate": start, "untilDate": end}]),
        ("Daily heart rate", "/wellness-service/wellness/dailyHeartRate",
         [{"date": day}, {"calendarDate": day}]),
        ("Stress", f"/usersummary-service/stats/stress/daily/{start}/{end}", [{}]),
        ("Fitness age", f"/fitnessage-service/fitnessage/v2/{day}", [{}]),
        ("Wellness activity summary", f"/wellnessactivity-service/activity/summary/range/{start}/{end}", [{}]),
        ("User profile settings", "/userprofile-service/userprofile/settings", [{}]),
        ("Activity list", "/activitylist-service/activities/search/activities",
         [{"limit": 10, "start": 0}, {}]),
    ]

    results = [probe(session, *p) for p in probes]

    # Activity detail and file: use the most recent run from the list.
    activity_id = None
    listing = next((r for r in results if r["name"] == "Activity list" and r["ok"]), None)
    if listing and isinstance(listing["payload"], list):
        for a in listing["payload"]:
            t = ((a.get("activityType") or {}).get("typeKey") or "")
            if "running" in t and a.get("activityId"):
                activity_id = a["activityId"]
                break
    fit_note = "no running activity found in the list"
    if activity_id:
        results.append(probe(session, "Activity detail", f"/activity-service/activity/{activity_id}", [{}]))
        results.append(probe(session, "Activity workouts", f"/activity-service/activity/{activity_id}/workouts", [{}]))
        fit_note = probe_fit(session, activity_id)

    lines = ["# Garmin Personal API probe", "",
             f"Token: OK ({token_shape}). Dates probed: {start} to {end}.", "",
             "## Dashboard fields", "", "| field | covered by |", "|---|---|"]
    for field, names in DASHBOARD_FIELDS.items():
        hits = []
        for r in results:
            if not r["ok"]:
                continue
            clean = [p.replace("[]", "") for p in r["paths"]]
            leaf = {p.rsplit(".", 1)[-1] for p in clean}
            # A dotted name ("hrvSummary.status") must match the end of a
            # path, so a generic leaf like "status" elsewhere does not count.
            if any((n in leaf) if "." not in n else any(c.endswith(n) for c in clean)
                   for n in names):
                hits.append(r["name"])
        lines.append(f"| {field} | {', '.join(hits) if hits else '**not found**'} |")
    lines += ["", f"FIT download: {fit_note}", "", "## Endpoints", ""]
    for r in results:
        lines.append(f"### {r['name']} — `{r['path']}`")
        if r["ok"]:
            lines.append(f"OK with params {json.dumps(r['params'])}, {len(r['paths'])} key paths:")
            lines.append("```")
            lines += [f"{p}: {t}" for p, t in list(r["paths"].items())[:120]]
            lines.append("```")
        else:
            lines.append("No working parameter combination:")
        for a in r["attempts"]:
            lines.append(f"- {a}")
        lines.append("")

    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
