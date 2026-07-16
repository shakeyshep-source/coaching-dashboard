"""
build_computed.py — COMPUTED LAYER

Job: read garmin_data.json + manual_log.json, calculate everything
derived, write computed_data.json. This is the ONLY file every chart
and card on the dashboard should ever read from.

If a number on the dashboard looks wrong, the bug is either:
  (a) here, in the calculation, or
  (b) in the display code's rendering of a value that's already correct.
It is never "which raw field does this chart expect" — that question
no longer exists once this file is the single source of truth.

LOCKED SCHEMA:

    {
      "date": "YYYY-MM-DD",
      "rhr": float | null,
      "hrv_last_night": float | null,
      "sleep_score": float | null,
      "body_battery_high": float | null,
      "achilles_score": int | null,
      "acwr": float | null,
      "readiness_score": int | null,
      "hrv_baseline_7d": float | null,
      "hrv_delta_from_baseline": float | null,
      "rhr_baseline_7d": float | null,
      "rhr_delta_from_baseline": float | null,
      "today_summary": str
    }
"""

import json
from statistics import mean

GARMIN_FILE = "garmin_data.json"
MANUAL_FILE = "manual_log.json"
WEATHER_FILE = "weather.json"
PLAN_FILE = "training_plan.json"
FLAGS_LOG_FILE = "flags_log.json"
OUTPUT_FILE = "computed_data.json"

HARD_SESSION_TYPES = {"intervals", "tempo", "long", "race"}


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def index_by_date(rows):
    return {r["date"]: r for r in rows}


def rolling_baseline(values, i, window=7):
    """Mean of up to `window` days before index i, ignoring Nones."""
    window_vals = [v for v in values[max(0, i - window):i] if v is not None]
    return round(mean(window_vals), 1) if window_vals else None


def check_flags(planned_session, achilles_score, rhr_delta, hrv_delta, heat_risk):
    """Returns a list of reason strings if today's signals suggest the
    planned session should be reviewed. Never changes the plan itself —
    that decision stays with the person, always.

    Achilles flags fire regardless of what's planned (or not planned) —
    a sore achilles is worth surfacing as its own signal, not just a
    reason to skip a specific hard session. Recovery (HRV/RHR) and heat
    flags only fire when a genuinely hard session is planned that day,
    since those are specifically about whether that effort is wise —
    on a rest day or easy day there's nothing to reconsider there.
    """
    reasons = []

    if achilles_score is not None and achilles_score >= 3:
        reasons.append(f"Achilles scored {achilles_score} — worth keeping an eye on regardless of today's plan.")

    if not planned_session:
        return reasons
    is_hard_session = planned_session.get("session_type") in HARD_SESSION_TYPES

    rhr_elevated = rhr_delta is not None and rhr_delta > 2
    hrv_depressed = hrv_delta is not None and hrv_delta < -2
    if is_hard_session and rhr_elevated and hrv_depressed:
        reasons.append("Recovery below baseline on both HRV and RHR, and a hard session is planned.")

    if is_hard_session and heat_risk == "high":
        reasons.append("Heat risk is high and a hard session is planned.")

    return reasons


def update_flags_log(date_str, session_name, reasons, existing_log):
    """Appends a new flag event if today isn't already logged. Avoids
    duplicate entries when build_computed.py runs more than once on
    the same day (e.g. manual re-runs after the automated cron pull).
    """
    if not reasons:
        return existing_log
    if any(entry["date"] == date_str for entry in existing_log):
        return existing_log
    existing_log.append({
        "date": date_str,
        "planned_session": session_name,
        "reasons": reasons,
    })
    return existing_log
def summarise(rhr_delta, hrv_delta, sleep_score, achilles, heat_risk):
    if achilles and achilles >= 3:
        return "Achilles flagged. Consider an easy day or rest."
    rhr_elevated = rhr_delta is not None and rhr_delta > 2
    hrv_depressed = hrv_delta is not None and hrv_delta < -2
    if rhr_elevated and hrv_depressed:
        base = "Recovery below baseline on both HRV and RHR. Keep today easy."
        if heat_risk == "high":
            return base + " Heat risk is high today too — extra caution on intensity."
        return base
    if rhr_elevated or hrv_depressed:
        base = "Recovery slightly below baseline. Fine to train but stay conservative."
        if heat_risk == "high":
            return base + " Heat risk is high — watch for respiratory symptoms."
        return base
    if heat_risk == "high":
        return "Recovery looks normal but heat risk is high today. Adjust effort accordingly."
    if sleep_score is not None and sleep_score < 70:
        return "Sleep below average last night. Ease into today."
    return "Recovery looks normal. Fine for planned training."


def main():
    garmin_rows = load(GARMIN_FILE)
    manual_rows = load(MANUAL_FILE)
    weather_rows = load(WEATHER_FILE)
    plan_rows = load(PLAN_FILE)
    flags_log = load(FLAGS_LOG_FILE)
    manual_by_date = index_by_date(manual_rows)
    weather_by_date = index_by_date(weather_rows)
    plan_by_date = index_by_date(plan_rows)

    garmin_rows.sort(key=lambda r: r["date"])
    hrv_series = [r.get("hrv_last_night") for r in garmin_rows]
    rhr_series = [r.get("rhr") for r in garmin_rows]

    computed = []
    for i, row in enumerate(garmin_rows):
        manual = manual_by_date.get(row["date"], {})
        weather = weather_by_date.get(row["date"], {})
        heat_risk = weather.get("heat_risk")

        hrv_baseline = rolling_baseline(hrv_series, i)
        rhr_baseline = rolling_baseline(rhr_series, i)

        hrv_delta = (
            round(row["hrv_last_night"] - hrv_baseline, 1)
            if row.get("hrv_last_night") is not None and hrv_baseline is not None
            else None
        )
        rhr_delta = (
            round(row["rhr"] - rhr_baseline, 1)
            if row.get("rhr") is not None and rhr_baseline is not None
            else None
        )

        acwr = row.get("acwr_garmin")
        acwr_status = row.get("acwr_status")

        planned_session = plan_by_date.get(row["date"])
        flag_reasons = check_flags(
            planned_session, manual.get("achilles_score"), rhr_delta, hrv_delta, heat_risk
        )
        if flag_reasons:
            flags_log = update_flags_log(
                row["date"],
                planned_session.get("session_type") if planned_session else None,
                flag_reasons,
                flags_log,
            )

        computed.append({
            "date": row["date"],
            "rhr": row.get("rhr"),
            "hrv_last_night": row.get("hrv_last_night"),
            "sleep_score": row.get("sleep_score"),
            "body_battery_high": row.get("body_battery_high"),
            "achilles_score": manual.get("achilles_score"),
            "acwr": acwr,
            "acwr_status": acwr_status,
            "vo2max": row.get("vo2max"),
            "vo2max_date": row.get("vo2max_date"),
            "training_status_feedback": row.get("training_status_feedback"),
            "hrv_baseline_7d": hrv_baseline,
            "hrv_delta_from_baseline": hrv_delta,
            "rhr_baseline_7d": rhr_baseline,
            "rhr_delta_from_baseline": rhr_delta,
            "heat_risk": heat_risk,
            "today_summary": summarise(rhr_delta, hrv_delta, row.get("sleep_score"), manual.get("achilles_score"), heat_risk),
            "plan_flag_reasons": flag_reasons,
        })

    with open(OUTPUT_FILE, "w") as f:
        json.dump(computed, f, indent=2)
    with open(FLAGS_LOG_FILE, "w") as f:
        json.dump(flags_log, f, indent=2)

    print(f"Computed {len(computed)} days -> {OUTPUT_FILE}")
    if flags_log:
        print(f"Flags log has {len(flags_log)} total entries")


if __name__ == "__main__":
    main()
