"""
race_predictor.py — PREDICTION LAYER

Job: read garmin_data.json (for VO2max trend), races.json (clean race
results), and training_plan.json (to find whatever race is actually
next), produce time predictions across the four standard distances
(5K, 10K, Half Marathon, Marathon), each with a confidence band.
Writes race_prediction.json.

Method: Riegel's formula (T2 = T1 * (D2/D1)^1.06) projects a time at
another distance from each clean race result, then averages across
races and widens into a band based on how much those projections
disagree with each other, plus VO2max trend.

IMPORTANT CAVEAT ON MARATHON PREDICTIONS: Riegel's formula gets
progressively less reliable the further you extrapolate from your
actual race distances. Projecting from 5K/10K results out to a full
marathon is a big stretch — it doesn't account for fuelling, glycogen
depletion, or the different physiological demands of holding pace for
2+ hours versus 15-40 minutes. Treat the marathon number as a rough
ballpark, not a genuine target time, until there's an actual longer
race (half marathon or further) to base it on instead.

This is still a first pass overall — the band widths and VO2max
weighting are estimates, not sports-science gospel. Treat every
number here as a sanity-check, not a guarantee.
"""

import json
from datetime import date

GARMIN_FILE = "garmin_data.json"
RACES_FILE = "races.json"
PLAN_FILE = "training_plan.json"
OUTPUT_FILE = "race_prediction.json"

RIEGEL_EXPONENT = 1.06

TARGET_DISTANCES = {
    "5K": 5.0,
    "10K": 10.0,
    "Half Marathon": 21.0975,
    "Marathon": 42.195,
}

KEY_SESSIONS = [
    {
        "date": "2026-07-08",
        "description": "4x1km @ 3:37 avg, RPE 7-8",
    },
]


def riegel_predict(known_time_s, known_distance_km, target_distance_km):
    return known_time_s * (target_distance_km / known_distance_km) ** RIEGEL_EXPONENT


def fmt_time(seconds):
    if seconds is None:
        return None
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    if h > 0:
        return f"{h}:{m:02d}:{s:05.2f}"
    return f"{m}:{s:05.2f}"


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def vo2max_trend(garmin_rows):
    readings = [r for r in garmin_rows if r.get("vo2max")]
    if len(readings) < 2:
        return None, None
    readings.sort(key=lambda r: r["date"])
    latest = readings[-1]["vo2max"]
    earliest = readings[0]["vo2max"]
    return latest, round(latest - earliest, 1)


def find_next_race(plan_rows):
    today_str = date.today().isoformat()
    upcoming_races = [
        r for r in plan_rows
        if r.get("session_type") == "race" and r.get("date", "") >= today_str
    ]
    if not upcoming_races:
        return None
    upcoming_races.sort(key=lambda r: r["date"])
    return upcoming_races[0]


def predict_for_distance(clean_races, target_km, latest_vo2, vo2_change):
    predictions = []
    for race in clean_races:
        predicted_s = riegel_predict(race["time_seconds"], race["distance_km"], target_km)
        predictions.append(predicted_s)

    if not predictions:
        return {
            "predicted_time_fmt": None,
            "low_estimate_fmt": None,
            "high_estimate_fmt": None,
            "note": "No clean (unaffected) races available for a Riegel-based estimate.",
        }

    central = sum(predictions) / len(predictions)
    spread = max(predictions) - min(predictions)
    band = max(15, spread / 2)

    note = f"Based on {len(clean_races)} clean race result(s)."
    if latest_vo2 is not None and vo2_change is not None:
        if vo2_change > 0:
            adjustment = min(vo2_change * 2, target_km * 2)
            central -= adjustment
            note += f" VO2max up {vo2_change} since earliest reading — nudged faster."
        elif vo2_change < 0:
            note += f" VO2max down {abs(vo2_change)} since earliest reading — no adjustment applied, treat as optimistic."

    if target_km >= 42:
        note += (" CAVEAT: marathon projections from short-race data are unreliable — "
                  "doesn't account for fuelling or sustained multi-hour effort. Rough ballpark only.")
    elif target_km >= 21:
        note += " Reasonably grounded if based on a 10K+ result; more of a stretch if only projected from 5K."

    return {
        "predicted_time_fmt": fmt_time(central),
        "low_estimate_fmt": fmt_time(central - band),
        "high_estimate_fmt": fmt_time(central + band),
        "note": note,
    }


def main():
    garmin_rows = load(GARMIN_FILE)
    all_races = load(RACES_FILE)
    plan_rows = load(PLAN_FILE)
    latest_vo2, vo2_change = vo2max_trend(garmin_rows)

    clean_races = [
        r for r in all_races
        if r.get("conditions_normal", True)
        and r.get("include_in_prediction", True)
        and r.get("time_seconds")
        and r.get("distance_km")
    ]
    excluded = [
        r for r in all_races
        if not (r.get("conditions_normal", True) and r.get("include_in_prediction", True))
    ]

    predictions_by_distance = {}
    for label, km in TARGET_DISTANCES.items():
        predictions_by_distance[label] = predict_for_distance(clean_races, km, latest_vo2, vo2_change)

    next_race = find_next_race(plan_rows)

    vo2_note = "VO2max trend not yet available."
    if latest_vo2 is not None:
        if vo2_change is not None:
            vo2_note = f"VO2max is {latest_vo2} ({'up' if vo2_change >= 0 else 'down'} {abs(vo2_change)} since earliest reading)."
        else:
            vo2_note = f"VO2max is {latest_vo2}, stable over the recent window."

    result = {
        "generated_date": date.today().isoformat(),
        "next_race": {
            "name": next_race.get("notes", "").split(".")[0] if next_race else None,
            "date": next_race["date"] if next_race else None,
            "distance_km": next_race.get("target_distance_km") if next_race else None,
        } if next_race else None,
        "predictions": predictions_by_distance,
        "vo2max_note": vo2_note,
        "based_on_races": [r["name"] for r in clean_races],
        "excluded_races": [
            {"name": r["name"], "reason": r.get("notes", "no reason given")} for r in excluded
        ],
        "recent_key_sessions": KEY_SESSIONS,
        "key_sessions_note": ("Logged for reference only — not converted into a predicted time. "
                               "Converting interval-rep pace to race pace has no reliable formula; "
                               "use your own judgement on what these sessions suggest."),
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print("Predictions:")
    for label, pred in predictions_by_distance.items():
        print(f"  {label}: {pred['low_estimate_fmt']} - {pred['high_estimate_fmt']} (central: {pred['predicted_time_fmt']})")
    if next_race:
        print(f"Next race: {result['next_race']['name']} on {result['next_race']['date']}")


if __name__ == "__main__":
    main()
