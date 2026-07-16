"""
sheets_pull.py — GOOGLE FORMS INPUT LAYER

Job: read new entries from the three Google Form response sheets
and merge them into the local JSON files the rest of the pipeline
already reads. Runs automatically via cron alongside garmin_pull.py
and weather_pull.py — no manual export or file transfer needed.

Uses Google Sheets' public CSV export endpoint (no API key required,
same no-auth approach as Open-Meteo weather pull). Sheets must be
shared as "Anyone with the link can view" for this to work.

SHEET IDs — update these if sheets are ever recreated:
"""

import csv
import io
import json
import requests
from datetime import datetime

# Sheet IDs extracted from the sharing URLs
DAILY_LOG_SHEET_ID    = "1eqsg4m9zmA6kKIfvpNjlZ1kuaFABcgm-JWsC7ErA2g4"
TRAINING_PLAN_SHEET_ID = "1GJ4PkSXBRT6wBY6mLX3pBIu9PCQWzVW1G6i4JXxsgIc"
RACE_LOG_SHEET_ID     = "14vCnLI1wYWep2FAyTotvdp2-wgsWawE_B19aE-oPNAI"

# Output files — must match what build_computed.py and dashboard expect
MANUAL_LOG_FILE    = "manual_log.json"
TRAINING_PLAN_FILE = "training_plan.json"
RACES_FILE         = "races.json"


def fetch_sheet_csv(sheet_id):
    """Fetch a Google Sheet as CSV using the public export endpoint."""
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def parse_date(raw):
    """Parse dates from Google Forms — typically 'DD/MM/YYYY' or 'YYYY-MM-DD'."""
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw.strip()


def parse_bool(raw, default=True):
    """Parse Yes/No responses from Google Forms."""
    if not raw:
        return default
    return raw.strip().lower() in ("yes", "true", "1")


def parse_float(raw):
    """Parse optional numeric fields, returning None if blank or invalid."""
    try:
        return float(raw.strip()) if raw.strip() else None
    except (ValueError, AttributeError):
        return None


def pull_daily_log():
    rows = fetch_sheet_csv(DAILY_LOG_SHEET_ID)
    entries = []
    for row in rows:
        date = parse_date(row.get("Date", ""))
        if not date:
            continue
        entries.append({
            "date": date,
            "achilles_score": int(parse_float(row.get("Achillies score", "")) or 0),
            "session_type": row.get("Session Type", "").strip().lower() or None,
            "rpe": parse_float(row.get("RPE", "")),
            "on_target": parse_bool(row.get("On target", ""), default=True),
            "session_notes": row.get("Notes", "").strip() or None,
        })
    # Deduplicate by date, keeping most recent submission for each day
    by_date = {}
    for e in entries:
        by_date[e["date"]] = e
    return sorted(by_date.values(), key=lambda r: r["date"])


def pull_training_plan():
    rows = fetch_sheet_csv(TRAINING_PLAN_SHEET_ID)
    entries = []
    for row in rows:
        date = parse_date(row.get("Session date", ""))
        if not date:
            continue
        entries.append({
            "date": date,
            "session_type": row.get("Session type", "").strip().lower() or None,
            "target_distance_km": parse_float(row.get("Target distance", "")),
            "target_pace": row.get("Targetpace", "").strip() or None,
            "notes": row.get("Notes", "").strip() or None,
        })
    by_date = {}
    for e in entries:
        by_date[e["date"]] = e
    return sorted(by_date.values(), key=lambda r: r["date"])


def pull_races():
    rows = fetch_sheet_csv(RACE_LOG_SHEET_ID)
    entries = []
    for row in rows:
        date = parse_date(row.get("Date", ""))
        if not date:
            continue
        minutes = parse_float(row.get("Time minutes", ""))
        seconds = parse_float(row.get("Time seconds", ""))
        total_seconds = None
        time_fmt = None
        if minutes is not None and seconds is not None:
            total_seconds = int(minutes * 60 + seconds)
            m = int(total_seconds // 60)
            s = total_seconds - m * 60
            time_fmt = f"{m}:{s:05.2f}"
        entries.append({
            "date": date,
            "name": row.get("Race name", "").strip(),
            "distance_km": parse_float(row.get("Distance", "")),
            "time_seconds": total_seconds,
            "time_fmt": time_fmt,
            "tapered": parse_bool(row.get("Tapered for this race", ""), default=False),
            "conditions_normal": parse_bool(row.get("Conditions normal", ""), default=True),
            "include_in_prediction": parse_bool(row.get("Standalone max effort race", ""), default=True),
            "notes": row.get("Notes", "").strip() or None,
        })
    by_date = {}
    for e in entries:
        by_date[e["date"]] = e
    return sorted(by_date.values(), key=lambda r: r["date"])


def load_existing(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def merge(existing, new_entries, key="date"):
    """Merge new sheet entries into existing JSON, new entries win on conflict."""
    merged = {e[key]: e for e in existing}
    for e in new_entries:
        merged[e[key]] = e
    return sorted(merged.values(), key=lambda r: r[key])


def main():
    print("--- Sheets pull ---")

    print("Pulling daily log...")
    daily = pull_daily_log()
    existing_daily = load_existing(MANUAL_LOG_FILE)
    merged_daily = merge(existing_daily, daily)
    with open(MANUAL_LOG_FILE, "w") as f:
        json.dump(merged_daily, f, indent=2)
    print(f"  {len(daily)} sheet entries -> {len(merged_daily)} total in {MANUAL_LOG_FILE}")

    print("Pulling training plan...")
    plan = pull_training_plan()
    existing_plan = load_existing(TRAINING_PLAN_FILE)
    merged_plan = merge(existing_plan, plan)
    with open(TRAINING_PLAN_FILE, "w") as f:
        json.dump(merged_plan, f, indent=2)
    print(f"  {len(plan)} sheet entries -> {len(merged_plan)} total in {TRAINING_PLAN_FILE}")

    print("Pulling race log...")
    races = pull_races()
    existing_races = load_existing(RACES_FILE)
    merged_races = merge(existing_races, races)
    with open(RACES_FILE, "w") as f:
        json.dump(merged_races, f, indent=2)
    print(f"  {len(races)} sheet entries -> {len(merged_races)} total in {RACES_FILE}")

    print("Sheets pull complete.")


if __name__ == "__main__":
    main()
