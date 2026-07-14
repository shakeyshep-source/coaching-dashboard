"""
race_predictor.py — PREDICTION LAYER

Job: read garmin_data.json (for VO2max trend) + a small hardcoded
recent-races list, produce a 5K time prediction with a confidence band.
Writes race_prediction.json, which computed_data.json / dashboard.html
can read alongside everything else.

Method: Riegel's formula (T2 = T1 * (D2/D1)^1.06) projects a 5K time
from a known race at another distance, then widens into a band based
on how much VO2max has moved recently (bigger recent swings = wider
band, since fitness is less settled).

This is a first pass — the band width and the VO2max-trend weighting
are estimates, not sports-science gospel. Treat the output as a
sanity-check number, not a guarantee.
"""

import json
from datetime import date

GARMIN_FILE = "garmin_data.json"
RACES_FILE = "races.json"
OUTPUT_FILE = "race_prediction.json"

RIEGEL_EXPONENT = 1.06

# Recent quality training sessions can be logged here for your own
# reference, but they are NOT converted into a predicted race time.
# There's no reliable formula for converting interval-rep pace (run
# with recovery, at sub-maximal RPE) into continuous race pace — any
# fixed conversion factor is a guess dressed up as a calculation. Use
# your own judgement on sessions like this alongside the Riegel
# estimate below, rather than trusting an automated number for it.
KEY_SESSIONS = [
    {
        "date": "2026-07-08",
        "description": "4x1km @ 3:37 avg, RPE 7-8",
    },
]

TARGET_DISTANCE_KM = 5.0
TARGET_RACE_NAME = "Cardiff 5K"
TARGET_RACE_DATE = "2026-07-22"


def riegel_predict(known_time_s, known_distance_km, target_distance_km):
    return known_time_s * (target_distance_km / known_distance_km) ** RIEGEL_EXPONENT


def fmt_time(seconds):
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}:{s:05.2f}"


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def vo2max_trend(garmin_rows):
    """Returns (latest_vo2max, change_vs_earliest) or (None, None) if
    there isn't enough data yet. Used to widen/narrow the confidence
    band: a rising VO2max suggests the Riegel-based prediction (from
    older races) may be conservative; a falling one suggests caution."""
    readings = [r for r in garmin_rows if r.get("vo2max")]
    if len(readings) < 2:
        return None, None
    readings.sort(key=lambda r: r["date"])
    latest = readings[-1]["vo2max"]
    earliest = readings[0]["vo2max"]
    return latest, round(latest - earliest, 1)


def main():
    garmin_rows = load(GARMIN_FILE)
    all_races = load(RACES_FILE)
    latest_vo2, vo2_change = vo2max_trend(garmin_rows)

    # A race only feeds the Riegel calculation if it was (a) run at a
    # genuine max effort comparable to a standalone race, and (b) not
    # compromised by illness/conditions. Relay legs, training races,
    # and disrupted efforts are excluded but still shown for context.
    clean_races = [
        r for r in all_races
        if r.get("conditions_normal", True)
        and r.get("include_in_prediction", True)
        and r.get("time_seconds")
    ]
    excluded = [
        r for r in all_races
        if not (r.get("conditions_normal", True) and r.get("include_in_prediction", True))
    ]

    predictions = []
    for race in clean_races:
        predicted_s = riegel_predict(race["time_seconds"], race["distance_km"], TARGET_DISTANCE_KM)
        predictions.append(predicted_s)

    if predictions:
        central = sum(predictions) / len(predictions)
        spread = max(predictions) - min(predictions)
        band = max(15, spread / 2)
        riegel_note = f"Riegel estimate based on: {', '.join(r['name'] for r in clean_races)}."
    else:
        central = None
        band = None
        riegel_note = "No clean (unaffected) races available for a Riegel-based estimate."

    if excluded:
        riegel_note += " Excluded (compromised conditions): " + ", ".join(
            f"{r['name']} ({r.get('notes', 'no reason given')})" for r in excluded
        )

    vo2_note = "VO2max trend not yet available."
    if latest_vo2 is not None and central is not None:
        if vo2_change is not None and vo2_change > 0:
            # Fitness trending up since the older races — the Riegel
            # prediction from those races is likely a touch conservative.
            # Shift the central estimate down slightly rather than
            # pretending the number is more precise than it is.
            adjustment = min(vo2_change * 2, 10)  # capped, deliberately modest
            central -= adjustment
            vo2_note = (f"VO2max is {latest_vo2} (up {vo2_change} since earliest reading). "
                        f"Central estimate nudged {adjustment:.0f}s faster to reflect improving fitness.")
        elif vo2_change is not None and vo2_change < 0:
            vo2_note = (f"VO2max is {latest_vo2} (down {abs(vo2_change)} since earliest reading). "
                        f"No adjustment applied — treat the central estimate as optimistic.")
        else:
            vo2_note = f"VO2max is {latest_vo2}, stable over the recent window."

    result = {
        "race_name": TARGET_RACE_NAME,
        "race_date": TARGET_RACE_DATE,
        "generated_date": date.today().isoformat(),
        "riegel_estimate": {
            "predicted_time_fmt": fmt_time(central) if central else None,
            "low_estimate_fmt": fmt_time(central - band) if central else None,
            "high_estimate_fmt": fmt_time(central + band) if central else None,
            "note": riegel_note,
        },
        "vo2max_note": vo2_note,
        "recent_key_sessions": KEY_SESSIONS,
        "key_sessions_note": ("Logged for reference only — not converted into a predicted time. "
                               "Converting interval-rep pace to race pace has no reliable formula; "
                               "use your own judgement on what these sessions suggest."),
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Riegel estimate: {result['riegel_estimate']['low_estimate_fmt']} - "
          f"{result['riegel_estimate']['high_estimate_fmt']} "
          f"(central: {result['riegel_estimate']['predicted_time_fmt']})")


if __name__ == "__main__":
    main()
