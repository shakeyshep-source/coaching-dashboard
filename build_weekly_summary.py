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
OUTPUT_FILE = "weekly_summary.json"

WEEKS_TO_KEEP = 8
HARD_SESSION_TYPES = {"intervals", "tempo", "long", "race"}


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


def main():
    activities = load(ACTIVITIES_FILE)
    garmin = load(GARMIN_FILE)
    manual = load(MANUAL_FILE)
    plan = load(PLAN_FILE)

    manual_by_date = {r["date"]: r for r in manual}
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
            session_log.append({
                "date": d,
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

    result = {
        "generated": today.isoformat(),
        "next_race": next_race,
        "weeks": weeks,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Built {len(weeks)} week(s) -> {OUTPUT_FILE}")
    for w in weeks:
        print(f"  {w['week_start']}: {w['km']}km in {w['runs']} runs, "
              f"avg RPE {w['avg_rpe']}, on-target {w['on_target_pct']}%")


if __name__ == "__main__":
    main()
