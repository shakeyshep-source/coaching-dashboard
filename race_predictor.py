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
from datetime import date, timedelta

GARMIN_FILE = "garmin_data.json"
# The long VO2max series, weekly back to January and to one decimal.
VO2_HISTORY_FILE = "vo2max_history.json"
# VO2max moves over months, so the trend that nudges a prediction is
# read over six weeks — long enough to be real, short enough to be
# "recent form" rather than the whole year's progress.
VO2_TREND_WINDOW_DAYS = 42
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


def vo2max_trend(garmin_rows, window_days=VO2_TREND_WINDOW_DAYS):
    """Latest VO2max, and how far it has moved over a RECENT window.

    Two separate fixes live here, and they pull in opposite directions.

    The reading itself now comes from vo2max_history.json, which keeps
    Garmin's one decimal place and reaches back to January.
    garmin_data.json carries the same figure rounded to a whole number
    across a 14-day window, and at a settled level every reading in
    that fortnight is identical — so the dashboard note read "VO2max is
    60.0 (up 0.0 since earliest reading)", which was true of the window
    and useless about the athlete.

    But the change must NOT simply become "since the earliest reading"
    of the long series. That is a year-to-date figure — 54.2 to 59.9,
    +5.7 — and the caller turns it into a pace adjustment. The races
    the projection is built from already include the most recent one,
    so his current fitness is in those times already; nudging them
    faster again because VO2max rose since January counts the same
    improvement twice, and hands him an optimistic goal time with no
    evidence behind it. The adjustment was written for a recent trend,
    so it gets a recent window and the note says which.
    """
    readings = [r for r in load(VO2_HISTORY_FILE) if r.get("vo2max")]
    if len(readings) < 2:
        readings = [r for r in garmin_rows if r.get("vo2max")]
    if len(readings) < 2:
        return None, None
    readings.sort(key=lambda r: r["date"])
    latest = readings[-1]["vo2max"]

    cutoff = (date.fromisoformat(readings[-1]["date"])
              - timedelta(days=window_days)).isoformat()
    in_window = [r for r in readings if r["date"] >= cutoff]
    # Fewer than two readings in the window means no trend to read —
    # better to apply no adjustment than to invent one.
    if len(in_window) < 2:
        return latest, None
    return latest, round(latest - in_window[0]["vo2max"], 1)


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
            note += (f" VO2max up {vo2_change} over the last "
                     f"{VO2_TREND_WINDOW_DAYS // 7} weeks — nudged faster.")
        elif vo2_change < 0:
            note += (f" VO2max down {abs(vo2_change)} over the last "
                     f"{VO2_TREND_WINDOW_DAYS // 7} weeks — no adjustment applied, "
                     "treat as optimistic.")

    if target_km >= 42:
        note += (" CAVEAT: marathon projections from short-race data are unreliable — "
                  "doesn't account for fuelling or sustained multi-hour effort. Rough ballpark only.")
    elif target_km >= 21:
        note += " Reasonably grounded if based on a 10K+ result; more of a stretch if only projected from 5K."

    return {
        "predicted_time_fmt": fmt_time(central),
        "predicted_time_seconds": round(central, 1),
        "low_estimate_fmt": fmt_time(central - band),
        "high_estimate_fmt": fmt_time(central + band),
        "note": note,
    }


def fmt_pace(sec_per_km):
    """Seconds per km -> '3:50/km'."""
    if sec_per_km is None:
        return None
    m = int(sec_per_km // 60)
    sec = int(round(sec_per_km - m * 60))
    if sec == 60:
        m, sec = m + 1, 0
    return f"{m}:{sec:02d}/km"


def riegel_pace(clean_races, target_km):
    """Average Riegel-projected race pace at target_km, seconds per km."""
    if not clean_races:
        return None
    preds = [riegel_predict(r["time_seconds"], r["distance_km"], target_km)
             for r in clean_races]
    return (sum(preds) / len(preds)) / target_km


def threshold_pace(clean_races):
    """Lactate threshold pace, taken as the pace sustainable for a race
    of about an hour — the standard working definition, and one this
    model can derive honestly rather than guessing a percentage.

    Rearranging Riegel for the distance that takes 3600s:
        D2 = D1 * (3600 / T1) ** (1 / exponent)
    """
    if not clean_races:
        return None
    paces = [
        3600 / (r["distance_km"] * (3600 / r["time_seconds"]) ** (1 / RIEGEL_EXPONENT))
        for r in clean_races
    ]
    return sum(paces) / len(paces)


def build_training_paces(clean_races, predictions):
    """Every pace a session might name, derived from the same clean race
    results as the time predictions - so it re-derives itself whenever a
    new race is logged, instead of being a fixed table that quietly goes
    stale as fitness moves.

    Easy and long-run bands are multiples of threshold rather than of
    race pace: the aerobic end scales with the aerobic ceiling, and this
    keeps the two ends of the range from drifting apart.
    """
    t = threshold_pace(clean_races)
    if t is None:
        return None

    def race_pace(label, km):
        secs = (predictions.get(label) or {}).get("predicted_time_seconds")
        return secs / km if secs else None

    def band(lo_mult, hi_mult):
        return f"{fmt_pace(t * lo_mult)} - {fmt_pace(t * hi_mult)}"

    zones = [
        {"name": "Easy / recovery", "pace": band(1.25, 1.35),
         "purpose": "Most of the week. Conversational - if in doubt, slower."},
        {"name": "Long run", "pace": band(1.20, 1.30),
         "purpose": "Sunday. Steady, not easy-plus - the last few km may drift quicker."},
        {"name": "Half marathon (goal)", "pace": fmt_pace(race_pace("Half Marathon", 21.0975)),
         "purpose": "Cheltenham race pace. Race-specific blocks late in the build."},
        {"name": "Threshold / tempo", "pace": fmt_pace(t),
         "purpose": "Comfortably hard, ~1hr race pace. The Saturday session."},
        {"name": "10K pace", "pace": fmt_pace(race_pace("10K", 10.0)),
         "purpose": "The '10K effort' in interval sessions - 1km reps sit here."},
        {"name": "5K pace", "pace": fmt_pace(race_pace("5K", 5.0)),
         "purpose": "Short, sharp reps of 3-5 minutes."},
        {"name": "Interval / VO2max", "pace": fmt_pace(riegel_pace(clean_races, 3.0)),
         "purpose": "3K race pace. Hard 800m-1km reps with full recovery."},
        {"name": "Reps / strides", "pace": fmt_pace(riegel_pace(clean_races, 1.5)),
         "purpose": "1500m pace. 200-400m reps and strides - speed, not stamina."},
    ]
    return {
        "zones": zones,
        "basis": (f"Derived from {len(clean_races)} clean race result(s) via the same Riegel "
                  f"projection as the time predictions, so these update themselves whenever a "
                  f"new race is logged."),
        "caveat": ("Guides, not gospel. Heat, hills, wind and fatigue all justify running slower "
                   "than the table on any given day - effort beats pace. Super shoes flatter pace "
                   "by roughly 5-8 sec/km, so a session in racers is not directly comparable."),
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
            weeks = VO2_TREND_WINDOW_DAYS // 7
            direction = 'up' if vo2_change > 0 else 'down' if vo2_change < 0 else 'level'
            vo2_note = (f"VO2max is {latest_vo2} — {direction}"
                        + (f" {abs(vo2_change)}" if vo2_change else "")
                        + f" over the last {weeks} weeks.")
        else:
            vo2_note = f"VO2max is {latest_vo2}, with too few readings lately to call a trend."

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
        "training_paces": build_training_paces(clean_races, predictions_by_distance),
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
