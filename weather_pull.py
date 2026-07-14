"""
weather_pull.py — WEATHER LAYER

Job: fetch current + forecast weather for training location, write
weather.json. Uses Open-Meteo (free, no API key required).

Flags a simple heat_risk field since Shep has exercise-induced
respiratory symptoms in heat + high intensity — this is meant to feed
a "today" caution alongside recovery data, not replace judgement.

LOCKED SCHEMA:

    {
      "date": "YYYY-MM-DD",
      "temp_max_c": float,
      "temp_min_c": float,
      "humidity_pct": float,
      "wind_speed_kmh": float,
      "heat_risk": "low" | "moderate" | "high"
    }
"""

import json
import requests

# Gloucester, UK — update if training location changes
LATITUDE = 51.8642
LONGITUDE = -2.2382
OUTPUT_FILE = "weather.json"


def classify_heat_risk(temp_max_c, humidity_pct):
    if temp_max_c >= 24 or (temp_max_c >= 20 and humidity_pct >= 70):
        return "high"
    if temp_max_c >= 18:
        return "moderate"
    return "low"


def main():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        "&daily=temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,wind_speed_10m_max"
        "&timezone=Europe/London&forecast_days=7"
    )

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    daily = resp.json()["daily"]

    results = []
    for i, date_str in enumerate(daily["time"]):
        temp_max = daily["temperature_2m_max"][i]
        humidity = daily["relative_humidity_2m_mean"][i]
        row = {
            "date": date_str,
            "temp_max_c": temp_max,
            "temp_min_c": daily["temperature_2m_min"][i],
            "humidity_pct": humidity,
            "wind_speed_kmh": daily["wind_speed_10m_max"][i],
            "heat_risk": classify_heat_risk(temp_max, humidity),
        }
        results.append(row)
        print(f"{date_str}  Max: {row['temp_max_c']}C  Humidity: {row['humidity_pct']}%  "
              f"Risk: {row['heat_risk']}")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved {len(results)} days to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
