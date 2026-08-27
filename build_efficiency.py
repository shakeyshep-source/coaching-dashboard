"""
build_efficiency.py — DERIVED LAYER

Job: track speed per heartbeat on easy runs across months, and write
efficiency_history.json.

    efficiency = metres per minute / average heart rate

If aerobic fitness improves, a given easy pace costs fewer beats and this
rises. It is the most direct read on aerobic fitness available from
ordinary training, needs no racing, and — unlike Garmin's VO2max, which
models the same relationship behind closed doors — every input is visible
and checkable.

Read it monthly. Run to run it is noise; across a season it is the
clearest line there is.

What is excluded, and why it matters more than the number:
  - Quality sessions. Reps and tempo work sit at a different intensity
    entirely, and averaging them in flatters or wrecks the figure
    depending on the week's mix.
  - Runs without heart rate, and anything under 20 minutes.
  - Hilly runs, above the same gradient threshold decoupling uses.

Two cautions that belong with any reading:
  - Heat inflates heart rate, so a summer dip is weather rather than lost
    fitness. Temperature is attached to every run where it is known, and
    the monthly figure carries the average.
  - Deep fatigue can SUPPRESS heart rate. A sudden rise during a heavy
    block is a question, not a triumph.

LOCKED SCHEMA — add fields, never rename or remove.
"""

import json
from collections import defaultdict
from statistics import mean, median

ACTIVITIES_FILE = "garmin_activities.json"
PLAN_FILE = "training_plan.json"
MANUAL_FILE = "manual_log.json"
WEATHER_LOG_FILE = "weather_log.json"
SESSION_DETAIL_FILE = "session_detail.json"
OUTPUT_FILE = "efficiency_history.json"

RUN_TYPES = ("running", "trail_running", "track_running", "treadmill_running")
QUALITY_TYPES = {"intervals", "tempo", "threshold", "race", "fartlek", "hills"}
MIN_DURATION_MIN = 20
# Same threshold as build_decoupling: above this the pace-for-HR bargain
# is about the terrain.
HILLY_M_PER_KM = 12.0
MIN_RUNS_PER_MONTH = 3
# Anything this far from his own median is not steady running, whatever
# Garmin calls it. The Hyrox doubles on 19 Apr 2026 arrives as a
# "running" activity — 8.55km at 7:47/km with an average HR of 152,
# because the sled pushes and wall balls sit between the runs — and
# scored 0.85 against a norm near 1.40. Left in, one event pulled a whole
# month down. Walk-run hikes land the same way.
OUTLIER_LOW = 0.80
OUTLIER_HIGH = 1.25
# Events Garmin files as runs but which are nothing of the sort.
EVENT_KEYWORDS = ("hyrox", "parkrun", "race", "relay", "duathlon", "triathlon")


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def easy_runs(activities, plan_by_date, manual_by_date, quality_ids):
    """Everyday aerobic running, with the hard days taken out.

    A session counts as quality if the plan said so, if he logged it as
    such, or if the lap data shows identifiable reps — three sources,
    because any one of them alone misses cases.
    """
    rows = []
    for a in activities:
        if (a.get("type") or "") not in RUN_TYPES:
            continue
        if not a.get("avg_hr") or not a.get("avg_pace_min_per_km"):
            continue
        if (a.get("duration_min") or 0) < MIN_DURATION_MIN:
            continue
        if a.get("activity_id") in quality_ids:
            continue
        date = a.get("date")
        planned = (plan_by_date.get(date) or {}).get("session_type")
        logged = (manual_by_date.get(date) or {}).get("session_type")
        if (planned or "").lower() in QUALITY_TYPES or (logged or "").lower() in QUALITY_TYPES:
            continue

        gradient = None
        if a.get("elevation_gain_m") is not None and a.get("distance_km"):
            gradient = round(a["elevation_gain_m"] / a["distance_km"], 1)
        if gradient is not None and gradient > HILLY_M_PER_KM:
            continue

        rows.append({
            "date": date,
            "activity_id": a.get("activity_id"),
            "name": a.get("name"),
            "distance_km": a.get("distance_km"),
            "pace_min_per_km": a["avg_pace_min_per_km"],
            "avg_hr": a["avg_hr"],
            # metres per minute per beat
            "efficiency": round((1000 / a["avg_pace_min_per_km"]) / a["avg_hr"], 4),
            "gradient_m_per_km": gradient,
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def drop_outliers(rows):
    """Remove what is not steady running, and say what went.

    Judged against his own median rather than a fixed band, so it stays
    right as fitness changes.
    """
    if len(rows) < 5:
        return rows, []
    mid = median(r["efficiency"] for r in rows)
    kept, dropped = [], []
    for r in rows:
        ratio = r["efficiency"] / mid
        name = (r.get("name") or "").lower()
        if any(k in name for k in EVENT_KEYWORDS):
            dropped.append({**r, "excluded": "event, not an easy run"})
        elif ratio < OUTLIER_LOW or ratio > OUTLIER_HIGH:
            dropped.append({**r, "excluded": f"{ratio:.0%} of median — not steady running"})
        else:
            kept.append(r)
    return kept, dropped


def by_month(rows, temp_by_date):
    """Monthly medians. Median rather than mean so one 30C plod or one
    unusually brisk 'easy' run cannot move the month."""
    buckets = defaultdict(list)
    for r in rows:
        buckets[r["date"][:7]].append(r)

    months = []
    for month in sorted(buckets):
        group = buckets[month]
        if len(group) < MIN_RUNS_PER_MONTH:
            continue
        temps = [temp_by_date[r["date"]] for r in group if r["date"] in temp_by_date]
        months.append({
            "month": month,
            "runs": len(group),
            "efficiency": round(median(r["efficiency"] for r in group), 4),
            "pace_min_per_km": round(median(r["pace_min_per_km"] for r in group), 2),
            "avg_hr": round(median(r["avg_hr"] for r in group), 1),
            "avg_temp_c": round(mean(temps), 1) if temps else None,
        })
    return months


def summarise(months):
    if len(months) < 2:
        return {"note": "needs two months of easy running before a change means anything"}
    first, last = months[0], months[-1]
    change = (last["efficiency"] - first["efficiency"]) / first["efficiency"] * 100
    return {
        "from_month": first["month"],
        "to_month": last["month"],
        "from_efficiency": first["efficiency"],
        "to_efficiency": last["efficiency"],
        "change_pct": round(change, 1),
        "from_hr_at_pace": f"{first['avg_hr']:.0f} bpm at {first['pace_min_per_km']:.2f} min/km",
        "to_hr_at_pace": f"{last['avg_hr']:.0f} bpm at {last['pace_min_per_km']:.2f} min/km",
        # Named so nobody reads a summer dip as lost fitness.
        "warmer_now": (last["avg_temp_c"] is not None and first["avg_temp_c"] is not None
                       and last["avg_temp_c"] > first["avg_temp_c"] + 3),
    }


def main():
    activities = load(ACTIVITIES_FILE, default=[])
    plan_by_date = {r["date"]: r for r in load(PLAN_FILE, default=[]) if r.get("date")}
    manual_by_date = {r["date"]: r for r in load(MANUAL_FILE, default=[]) if r.get("date")}
    quality_ids = {s.get("activity_id") for s in load(SESSION_DETAIL_FILE, default=[])
                   if s.get("reps")}
    temp_by_date = {r["date"]: r["temp_max_c"] for r in load(WEATHER_LOG_FILE, default=[])
                    if r.get("date") and r.get("temp_max_c") is not None}

    candidates = easy_runs(activities, plan_by_date, manual_by_date, quality_ids)
    rows, dropped = drop_outliers(candidates)
    months = by_month(rows, temp_by_date)
    result = {"runs": rows, "excluded": dropped, "months": months,
              "summary": summarise(months)}

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Efficiency: {len(rows)} easy run(s) across {len(months)} month(s) -> {OUTPUT_FILE}")
    for d in dropped:
        print(f"  excluded {d['date']} ({d.get('name') or 'unnamed'}): {d['excluded']}")
    for m in months:
        temp = f", {m['avg_temp_c']}C" if m["avg_temp_c"] is not None else ""
        print(f"  {m['month']}: {m['efficiency']} "
              f"({m['avg_hr']:.0f} bpm at {m['pace_min_per_km']:.2f} min/km, "
              f"{m['runs']} runs{temp})")
    s = result["summary"]
    if "change_pct" in s:
        print(f"  {s['from_month']} to {s['to_month']}: {s['change_pct']:+.1f}%")


if __name__ == "__main__":
    main()
