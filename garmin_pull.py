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

TOKENSTORE = "/home/shakeyshep/.garmin_tokens"
OUTPUT_FILE = "garmin_data.json"
DAYS_TO_PULL = 14  # rolling window; adjust as needed


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
            latest = status["mostRecentTrainingStatus"]["latestTrainingStatusData"]
            device_id = list(latest.keys())[0]
            acute_dto = latest[device_id].get("acuteTrainingLoadDTO", {})
            row["training_load_acute"] = acute_dto.get("dailyTrainingLoadAcute")
            row["training_load_chronic"] = acute_dto.get("dailyTrainingLoadChronic")
            row["acwr_garmin"] = acute_dto.get("dailyAcuteChronicWorkloadRatio")
            row["acwr_status"] = acute_dto.get("acwrStatus")
            row["training_status_feedback"] = latest[device_id].get("trainingStatusFeedbackPhrase")
        except (KeyError, IndexError):
            pass

        try:
            vo2 = status.get("mostRecentVO2Max", {}).get("generic", {})
            row["vo2max"] = vo2.get("vo2MaxValue")
            row["vo2max_date"] = vo2.get("calendarDate")
        except (KeyError, IndexError):
            pass

    return row


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


if __name__ == "__main__":
    main()
