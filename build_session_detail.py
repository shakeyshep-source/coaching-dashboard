"""
build_session_detail.py — DERIVED LAYER

Job: turn raw per-lap splits (garmin_laps.json) into a read of how each
quality session was actually executed, and write session_detail.json.

Why this exists: garmin_activities.json holds whole-run averages, and a
rep session averaged over its warm-up, recoveries and cool-down says
almost nothing. "14.01 km at 4:39/km" was 5x1200m inside 1.6 seconds of
each other, and no field in the raw layer showed that.

What it answers, per session:
  - how many reps, at what pace, and how consistent
  - whether he faded, held, or finished fastest
  - what the recoveries actually were
  - warm-up and cool-down volume, so principle 6 (target_distance_km is
    the WHOLE session) can be checked against what he ran

Structured workouts carry Garmin's own intensityType per lap, which is
authoritative. Unstructured runs get a pace-based fallback, flagged as
inferred so nothing downstream mistakes a guess for a fact.

LOCKED SCHEMA — add fields, never rename or remove.
"""

import json
import os
from statistics import mean

LAPS_FILE = "garmin_laps.json"
ACTIVITIES_FILE = "garmin_activities.json"
OUTPUT_FILE = "session_detail.json"

WORK_TYPES = {"ACTIVE", "INTERVAL"}
REST_TYPES = {"REST", "RECOVERY"}
WARMUP_TYPES = {"WARMUP"}
COOLDOWN_TYPES = {"COOLDOWN"}

# A rep shorter than this is a stride or a GPS artefact, not a rep.
MIN_REP_DISTANCE_KM = 0.2
# Fallback only: a lap this much faster than the session's median counts
# as work. 12% is wide enough to catch tempo blocks and tight enough not
# to catch the quick end of an easy run.
FALLBACK_WORK_MARGIN = 0.88


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def fmt_pace(sec_per_km):
    if sec_per_km is None:
        return None
    minutes, seconds = divmod(int(round(sec_per_km)), 60)
    return f"{minutes}:{seconds:02d}"


def pace_sec(lap):
    pace = lap.get("pace_min_per_km")
    return pace * 60 if pace else None


def classify(laps):
    """Split laps into work and recovery.

    Returns (work, recovery, structured). Garmin's own intensityType is
    used when the workout was structured on the watch; otherwise pace
    relative to the session median is the only signal available.
    """
    typed = [l for l in laps if l.get("intensity_type")]
    if typed:
        work = [l for l in laps if (l.get("intensity_type") or "").upper() in WORK_TYPES]
        rest = [l for l in laps if (l.get("intensity_type") or "").upper() in REST_TYPES]
        if work:
            return work, rest, True

    paces = [p for p in (pace_sec(l) for l in laps) if p]
    if len(paces) < 3:
        return [], [], False
    ordered = sorted(paces)
    median = ordered[len(ordered) // 2]
    threshold = median * FALLBACK_WORK_MARGIN
    work, rest = [], []
    for lap in laps:
        p = pace_sec(lap)
        if p is None:
            continue
        (work if p <= threshold else rest).append(lap)
    return work, rest, False


def summarise_reps(work):
    """Pace, spread and shape of the work intervals."""
    reps = [l for l in work
            if (l.get("distance_km") or 0) >= MIN_REP_DISTANCE_KM and pace_sec(l)]
    if not reps:
        return None

    paces = [pace_sec(l) for l in reps]
    durations = [l.get("duration_sec") for l in reps if l.get("duration_sec")]
    hrs = [l["avg_hr"] for l in reps if l.get("avg_hr")]

    fastest = min(range(len(paces)), key=lambda i: paces[i])
    slowest = max(range(len(paces)), key=lambda i: paces[i])

    # Did he hold it together? Compare the first and last thirds rather
    # than first and last rep, so one quick opener doesn't read as a fade.
    third = max(1, len(paces) // 3)
    drift = mean(paces[-third:]) - mean(paces[:third])

    return {
        "count": len(reps),
        "distance_km_each": [l.get("distance_km") for l in reps],
        "pace_sec_per_km": [round(p, 1) for p in paces],
        "pace_fmt": [fmt_pace(p) for p in paces],
        "duration_sec": durations or None,
        "mean_pace_sec_per_km": round(mean(paces), 1),
        "mean_pace_fmt": fmt_pace(mean(paces)),
        "fastest_pace_fmt": fmt_pace(min(paces)),
        "slowest_pace_fmt": fmt_pace(max(paces)),
        # Spread across the whole set — the single best number for how
        # evenly a rep session was judged.
        "spread_sec_per_km": round(max(paces) - min(paces), 1),
        "fastest_rep": fastest + 1,
        "slowest_rep": slowest + 1,
        "finished_fastest": fastest == len(paces) - 1,
        # Positive = slowed through the session, negative = negative split.
        "drift_sec_per_km": round(drift, 1),
        "mean_hr": round(mean(hrs), 1) if hrs else None,
        "max_hr": max((l["max_hr"] for l in reps if l.get("max_hr")), default=None),
    }


def summarise_recoveries(rest):
    if not rest:
        return None
    durations = [l["duration_sec"] for l in rest if l.get("duration_sec")]
    distances = [l["distance_km"] for l in rest if l.get("distance_km")]
    return {
        "count": len(rest),
        "duration_sec": durations or None,
        "distance_km": distances or None,
        "mean_duration_sec": round(mean(durations), 1) if durations else None,
        # Distance covered in a fixed recovery is NOT a fatigue signal on
        # his lapped course — he paces recoveries to finish where the next
        # rep starts. See CLAUDE.md. Recorded, deliberately not judged.
        "mean_pace_fmt": fmt_pace(mean([pace_sec(l) for l in rest if pace_sec(l)]))
                          if any(pace_sec(l) for l in rest) else None,
    }


def volume_split(laps, work, rest):
    """Warm-up and cool-down volume, so the session's real total can be
    checked against target_distance_km."""
    typed = {id(l): (l.get("intensity_type") or "").upper() for l in laps}
    warmup = sum(l.get("distance_km") or 0 for l in laps if typed[id(l)] in WARMUP_TYPES)
    cooldown = sum(l.get("distance_km") or 0 for l in laps if typed[id(l)] in COOLDOWN_TYPES)
    return {
        "warmup_km": round(warmup, 2) or None,
        "cooldown_km": round(cooldown, 2) or None,
        "work_km": round(sum(l.get("distance_km") or 0 for l in work), 2) or None,
        "recovery_km": round(sum(l.get("distance_km") or 0 for l in rest), 2) or None,
        "total_km": round(sum(l.get("distance_km") or 0 for l in laps), 2) or None,
    }


def build(laps_rows, activities):
    by_id = {a.get("activity_id"): a for a in activities}
    sessions = []
    for row in laps_rows:
        laps = row.get("laps") or []
        if len(laps) < 2:
            continue
        work, rest, structured = classify(laps)
        reps = summarise_reps(work)
        activity = by_id.get(row.get("activity_id"), {})
        sessions.append({
            "date": row.get("date"),
            "activity_id": row.get("activity_id"),
            "name": row.get("name"),
            "structured": structured,
            "lap_count": len(laps),
            # A session with no distinguishable work laps is a steady run:
            # recorded so the display can show splits, but with reps null
            # rather than an invented interpretation.
            "reps": reps,
            "recoveries": summarise_recoveries(rest) if reps else None,
            "volume": volume_split(laps, work if reps else [], rest if reps else []),
            "avg_hr": activity.get("avg_hr"),
            "aerobic_te": activity.get("aerobic_te"),
            "anaerobic_te": activity.get("anaerobic_te"),
        })
    sessions.sort(key=lambda s: (s.get("date") or "", s.get("activity_id") or 0))
    return sessions


def main():
    laps_rows = load(LAPS_FILE, default=[])
    activities = load(ACTIVITIES_FILE, default=[])
    if not laps_rows:
        print(f"No {LAPS_FILE} yet — run garmin_pull.py first. Nothing to build.")
        if not os.path.exists(OUTPUT_FILE):
            with open(OUTPUT_FILE, "w") as f:
                json.dump([], f, indent=2)
        return

    sessions = build(laps_rows, activities)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(sessions, f, indent=2)

    with_reps = [s for s in sessions if s.get("reps")]
    print(f"Built {len(sessions)} session(s) to {OUTPUT_FILE}, "
          f"{len(with_reps)} with identifiable reps.")
    for s in with_reps[-3:]:
        r = s["reps"]
        print(f"  {s['date']}: {r['count']} reps, mean {r['mean_pace_fmt']}/km, "
              f"spread {r['spread_sec_per_km']}s, drift {r['drift_sec_per_km']:+.1f}s"
              f"{'' if s['structured'] else ' (inferred)'}")


if __name__ == "__main__":
    main()
