import json
import getpass
from datetime import date, timedelta
from garminconnect import Garmin, GarminConnectAuthenticationError

DAYS_BACK = 30
OUTPUT_FILE = "garmin_data.json"

print("\n🏃 Garmin Connect Data Puller")
print("─" * 35)
TOKENSTORE = "/home/shakeyshep/.garmin_tokens"

print("\nConnecting to Garmin Connect...")
try:
    client = Garmin(email="", password="")
    client.login(tokenstore=TOKENSTORE)
    print("✓ Logged in\n")
except GarminConnectAuthenticationError:
    print("✗ Login failed — check email/password")
    exit(1)
except Exception as e:
    print(f"✗ Connection error: {e}")
    exit(1)

today = date.today()
start_date = today - timedelta(days=DAYS_BACK)
date_range = [start_date + timedelta(days=i) for i in range(DAYS_BACK + 1)]
results = []

for d in date_range:
    date_str = d.isoformat()
    row = {"date": date_str}
    try:
        rhr_data = client.get_rhr_day(date_str)
        row["rhr"] = (rhr_data.get("allMetrics", {}).get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE", []) or [{}])[0].get("value")
    except:
        row["rhr"] = None
    try:
        hrv_data = client.get_hrv_data(date_str)
        summary = hrv_data.get("hrvSummary", {})
        row["hrv_last_night"] = summary.get("lastNightAvg")
        row["hrv_status"] = summary.get("status")
    except:
        row["hrv_last_night"] = None
        row["hrv_status"] = None
    try:
        sleep_data = client.get_sleep_data(date_str)
        daily = sleep_data.get("dailySleepDTO", {})
        row["sleep_score"] = daily.get("sleepScores", {}).get("overall", {}).get("value")
        row["sleep_duration_hrs"] = round(daily.get("sleepTimeSeconds", 0) / 3600, 1)
    except:
        row["sleep_score"] = None
        row["sleep_duration_hrs"] = None
    try:
        bb_data = client.get_body_battery(date_str)
        charged = [x.get("charged") for x in bb_data if x.get("charged")]
        row["body_battery_peak"] = max(charged) if charged else None
    except:
        row["body_battery_peak"] = None
    results.append(row)
    print(f"  {date_str}  HRV: {row['hrv_last_night']}  RHR: {row['rhr']}  Sleep: {row['sleep_score']}  BB: {row['body_battery_peak']}")

with open(OUTPUT_FILE, "w") as f:
    json.dump(results, f, indent=2)
print(f"\n✓ Saved to {OUTPUT_FILE}")
