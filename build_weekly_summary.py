"""
build_weekly_summary.py — WEEKLY AGGREGATION LAYER

Job: collapse the daily data (Garmin activities + wellness, manual log,
plan) into one row per training week. This file is what the weekly
coach review reads first, and what the dashboard's Coach tab charts.

Weeks run Monday-Sunday to match the plan structure.

Stats worth explaining:
  - on_target_pct: of logged sessions this week, how many the athlete
    marked "on target" in the daily form. Compliance signal.
  - efficiency: on easy runs only, metres-per-minute divided by average
    HR. Higher = covering more ground per heartbeat = aerobic fitness
    improving. Only meaningful as a TREND across weeks, never as a
    single number (heat, terrain and fatigue all move it day to day).
  - planned_vs_done: sessions planned vs activities recorded — catches
    both missed sessions and unplanned extras.

Output (weekly_summary.json):

    {
      "generated": "YYYY-MM-DD",
      "next_race": {...} | null,
      "weeks": [ oldest -> newest, up to 8:
        {
          "week_start": "YYYY-MM-DD",
          "km": float, "runs": int, "time_min": float,
          "elevation_m": float,
          "planned_sessions": int, "logged_sessions": int,
          "hard_sessions_planned": int,
          "on_target_pct": float | null,
          "avg_rpe": float | null, "max_achilles": int | null,
          "avg_hrv": float | null, "avg_rhr": float | null,
          "avg_sleep_score": float | null, "avg_sleep_hrs": float | null,
          "easy_pace_min_km": float | null, "easy_avg_hr": float | null,
          "efficiency": float | null,
          "session_log": [ {date, session_type, rpe, on_target,
                            achilles_score, note, km, pace, avg_hr} ]
        }
      ]
    }
"""

import json
from datetime import date, datetime, timedelta
from statistics import mean

ACTIVITIES_FILE = "garmin_activities.json"
GARMIN_FILE = "garmin_data.json"
MANUAL_FILE = "manual_log.json"
PLAN_FILE = "training_plan.json"
WEATHER_LOG_FILE = "weather_log.json"
PREDICTION_FILE = "race_prediction.json"
OUTPUT_FILE = "weekly_summary.json"

WEEKS_TO_KEEP = 8
HARD_SESSION_TYPES = {"intervals", "tempo", "long", "race"}

# Quality = the sessions that convert fitness into race performance.
# Long runs are deliberately excluded — they're easy-paced volume, and
# counting them as "hard" would flatter the intensity balance.
QUALITY_TYPES = {"intervals", "tempo", "race"}

# HM long-run target: the build needs the long run comfortably at 90+
# minutes before taper. Used for the progression chart's target line.
LONG_RUN_TARGET_MIN = 95

# Taper: final meaningful load ends ~10 days out for a HM.
TAPER_DAYS = 10


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def week_start(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (d - timedelta(days=d.weekday())).isoformat()


def avg(values, digits=1):
    vals = [v for v in values if v is not None]
    return round(mean(vals), digits) if vals else None


def is_run(activity):
    return "running" in (activity.get("type") or "")


def fmt_pace(min_per_km):
    if min_per_km is None:
        return None
    m = int(min_per_km)
    s = round((min_per_km - m) * 60)
    return f"{m}:{s:02d}/km"


def main():
    activities = load(ACTIVITIES_FILE)
    garmin = load(GARMIN_FILE)
    manual = load(MANUAL_FILE)
    plan = load(PLAN_FILE)
    weather_log = load(WEATHER_LOG_FILE)

    weather_by_date = {r["date"]: r for r in weather_log}
    manual_by_date = {r["date"]: r for r in manual}
    plan_by_date = {r["date"]: r for r in plan}
    activities_by_date = {}
    for a in activities:
        if a.get("date"):
            activities_by_date.setdefault(a["date"], []).append(a)

    today = date.today()
    this_week = (today - timedelta(days=today.weekday())).isoformat()
    starts = sorted(
        {week_start(a["date"]) for a in activities if a.get("date")}
        | {week_start(r["date"]) for r in manual}
        | {this_week}
    )[-WEEKS_TO_KEEP:]

    weeks = []
    for start in starts:
        days = [(datetime.strptime(start, "%Y-%m-%d").date() + timedelta(days=i)).isoformat()
                for i in range(7)]

        runs = [a for d in days for a in activities_by_date.get(d, []) if is_run(a)]
        logs = [manual_by_date[d] for d in days if d in manual_by_date]
        wellness = [g for g in garmin if g.get("date") in days]
        planned = [p for p in plan if p.get("date") in days
                   and p.get("session_type") not in (None, "rest")]

        def day_type(d):
            """What kind of day was this? The athlete's own log wins;
            the plan is the fallback for days he didn't log."""
            logged = manual_by_date.get(d, {}).get("session_type")
            if logged:
                return logged
            return (plan_by_date.get(d) or {}).get("session_type")

        quality_time = sum(
            a.get("duration_min") or 0 for a in runs
            if day_type(a["date"]) in QUALITY_TYPES
        )
        total_time = sum(a.get("duration_min") or 0 for a in runs)
        easy_pct = round(100 * (total_time - quality_time) / total_time) if total_time else None

        longest = max(runs, key=lambda a: a.get("duration_min") or 0, default=None)

        on_target_vals = [1 if l.get("on_target") else 0 for l in logs if l.get("on_target") is not None]
        easy_runs = [
            a for a in runs
            if a.get("avg_hr") and a.get("avg_pace_min_per_km")
            and (manual_by_date.get(a["date"], {}).get("session_type") in ("easy", "long", None))
        ]
        efficiency_vals = [
            (1000 / a["avg_pace_min_per_km"]) / a["avg_hr"] for a in easy_runs
        ]

        session_log = []
        for d in days:
            log = manual_by_date.get(d, {})
            day_runs = [a for a in activities_by_date.get(d, []) if is_run(a)]
            if not log and not day_runs:
                continue
            main_run = max(day_runs, key=lambda a: a.get("distance_km") or 0) if day_runs else {}
            day_weather = weather_by_date.get(d, {})
            session_log.append({
                "date": d,
                "temp_max_c": day_weather.get("temp_max_c"),
                "heat_risk": day_weather.get("heat_risk"),
                "session_type": log.get("session_type"),
                "rpe": log.get("rpe"),
                "on_target": log.get("on_target"),
                "achilles_score": log.get("achilles_score"),
                "note": log.get("session_notes"),
                "km": round(sum(a.get("distance_km") or 0 for a in day_runs), 1) or None,
                "pace": main_run.get("avg_pace_min_per_km"),
                "avg_hr": main_run.get("avg_hr"),
            })

        weeks.append({
            "week_start": start,
            "km": round(sum(a.get("distance_km") or 0 for a in runs), 1),
            "runs": len(runs),
            "time_min": round(sum(a.get("duration_min") or 0 for a in runs), 0),
            "elevation_m": round(sum(a.get("elevation_gain_m") or 0 for a in runs), 0),
            "planned_sessions": len(planned),
            "logged_sessions": len(logs),
            "hard_sessions_planned": len([p for p in planned if p.get("session_type") in HARD_SESSION_TYPES]),
            "quality_time_min": round(quality_time),
            "easy_pct": easy_pct,
            "long_run_min": round(longest["duration_min"]) if longest and longest.get("duration_min") else None,
            "long_run_km": longest.get("distance_km") if longest else None,
            "on_target_pct": round(100 * mean(on_target_vals)) if on_target_vals else None,
            "avg_rpe": avg([l.get("rpe") for l in logs]),
            "max_achilles": max([l.get("achilles_score") or 0 for l in logs], default=None) if logs else None,
            "avg_hrv": avg([g.get("hrv_last_night") for g in wellness]),
            "avg_rhr": avg([g.get("rhr") for g in wellness]),
            "avg_sleep_score": avg([g.get("sleep_score") for g in wellness]),
            "avg_sleep_hrs": avg([g.get("sleep_duration_hrs") for g in wellness]),
            "easy_pace_min_km": avg([a.get("avg_pace_min_per_km") for a in easy_runs], 2),
            "easy_avg_hr": avg([a.get("avg_hr") for a in easy_runs]),
            "efficiency": avg(efficiency_vals, 3),
            "session_log": session_log,
        })

    next_race = None
    upcoming = [p for p in plan if p.get("session_type") == "race"
                and p.get("date", "") >= today.isoformat()]
    if upcoming:
        upcoming.sort(key=lambda r: r["date"])
        r = upcoming[0]
        next_race = {
            "name": (r.get("notes") or "").split(".")[0] or "Race",
            "date": r["date"],
            "distance_km": r.get("target_distance_km"),
            "days_away": (datetime.strptime(r["date"], "%Y-%m-%d").date() - today).days,
        }

    # --- Race readiness: everything the countdown decisions hang on ---
    race_readiness = None
    if next_race:
        race_date = datetime.strptime(next_race["date"], "%Y-%m-%d").date()
        taper_start = (race_date - timedelta(days=TAPER_DAYS)).isoformat()

        # Key sessions still to come before taper begins
        pre_taper = [p for p in plan
                     if today.isoformat() < p.get("date", "") < taper_start]
        remaining = {t: len([p for p in pre_taper if p.get("session_type") == t])
                     for t in ("tempo", "intervals", "long")}

        # Goal pace from the current prediction for this distance
        goal_pace = None
        goal_time = None
        prediction = load(PREDICTION_FILE)
        if isinstance(prediction, dict) and next_race.get("distance_km"):
            label = {5.0: "5K", 10.0: "10K", 21.0975: "Half Marathon",
                     42.195: "Marathon"}.get(next_race["distance_km"])
            p = (prediction.get("predictions") or {}).get(label) or {}
            goal_time = p.get("predicted_time_fmt")
            if goal_time:
                parts = [float(x) for x in goal_time.split(":")]
                total_s = sum(v * 60 ** (len(parts) - 1 - i) for i, v in enumerate(parts))
                goal_pace = fmt_pace(total_s / 60 / next_race["distance_km"])

        # Current capability, not history — a big run 6 weeks ago doesn't
        # say what the legs can do this week, so look at the last 4 only.
        long_runs = [w["long_run_min"] for w in weeks[-4:] if w.get("long_run_min")]
        race_readiness = {
            "race": next_race,
            "weeks_to_race": round(next_race["days_away"] / 7, 1),
            "taper_start": taper_start,
            "remaining_key_sessions": remaining,
            "goal_time": goal_time,
            "goal_pace": goal_pace,
            "long_run_target_min": LONG_RUN_TARGET_MIN,
            "longest_run_recent_min": max(long_runs) if long_runs else None,
        }

    result = {
        "generated": today.isoformat(),
        "next_race": next_race,
        "race_readiness": race_readiness,
        "weeks": weeks,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Built {len(weeks)} week(s) -> {OUTPUT_FILE}")
    for w in weeks:
        print(f"  {w['week_start']}: {w['km']}km in {w['runs']} runs, "
              f"quality {w['quality_time_min']}min, easy {w['easy_pct']}%, "
              f"long run {w['long_run_min']}min")
    if race_readiness:
        r = race_readiness
        print(f"Race readiness: {r['race']['name']} in {r['weeks_to_race']} weeks, "
              f"taper from {r['taper_start']}, remaining {r['remaining_key_sessions']}, "
              f"goal {r['goal_time']} ({r['goal_pace']})")


if __name__ == "__main__":
    main()
