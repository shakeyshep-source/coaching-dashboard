"""
build_decoupling.py — DERIVED LAYER

Job: measure aerobic decoupling from within-run samples (garmin_streams.json)
and write decoupling.json.

What it measures. Efficiency factor (EF) is speed per heartbeat — how much
pace a given heart rate is buying. Split a steady run in half and compare:
if EF holds, the aerobic system is coping. If EF falls in the second half —
same pace costing more beats, or the same beats buying less pace — that is
decoupling, and it is the clearest single measure of aerobic durability
there is. Friel's convention: under 5% is good, over 5% says the effort
outran the aerobic base underneath it.

    decoupling % = (EF_first_half - EF_second_half) / EF_first_half * 100

Why it matters here more than most metrics: a half marathon is decided by
whether pace holds at 80 minutes, and a marathon by whether it holds at
three hours. Decoupling measures precisely that, and it moves over months,
not days.

What is deliberately excluded, because including it would produce numbers
that look meaningful and are not:
  - Quality sessions. Reps and recoveries make halves incomparable — this
    only applies to steady running.
  - The first 10 minutes. HR lags pace at the start of every run; including
    the lag manufactures decoupling that is not there.
  - Runs under 40 minutes, or without heart rate.
  - Hilly runs. Elevation changes the pace-for-HR bargain, so anything
    above the gradient threshold is measured but marked unreliable.

LOCKED SCHEMA — add fields, never rename or remove.
"""

import json
from statistics import mean

STREAMS_FILE = "garmin_streams.json"
ACTIVITIES_FILE = "garmin_activities.json"
SESSION_DETAIL_FILE = "session_detail.json"
WEATHER_LOG_FILE = "weather_log.json"
OUTPUT_FILE = "decoupling.json"

# HR chases pace at the start of a run; the lag is worth 10 minutes.
WARMUP_EXCLUDE_SEC = 600
MIN_ANALYSED_SEC = 1500          # 25 min of steady running after the warm-up
MIN_SAMPLES_PER_HALF = 8
# Friel's threshold. Under this, the aerobic system held.
GOOD_THRESHOLD_PCT = 5.0
# Above roughly this gradient the pace-for-HR bargain is about the hill.
HILLY_M_PER_KM = 12.0
# Decoupling assumes a steady effort. Shep habitually starts slow and
# finishes quicker, and on the first real data the biggest readings were
# simply the runs where pace moved most: 28 Jul ran 11.8% slower in the
# second half and scored +9.65%, 2 Aug ran 5.9% quicker and scored
# -9.64%. Those measure pacing, not durability. A run whose halves differ
# by more than this is recorded but not treated as comparable.
STEADY_PACE_TOLERANCE_PCT = 5.0


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def efficiency_factor(samples):
    """Metres per second per heartbeat, over a run of samples.

    Averaging speed and HR separately, rather than averaging per-sample
    ratios, keeps a couple of dropped-out HR samples from swinging the
    result.
    """
    speeds = [s["speed_mps"] for s in samples if s.get("speed_mps")]
    hrs = [s["hr"] for s in samples if s.get("hr")]
    if len(speeds) < MIN_SAMPLES_PER_HALF or len(hrs) < MIN_SAMPLES_PER_HALF:
        return None
    avg_hr = mean(hrs)
    if not avg_hr:
        return None
    return mean(speeds) / avg_hr


def usable(samples):
    """Samples after the warm-up exclusion, with both HR and speed."""
    return [s for s in samples
            if s.get("t_sec") is not None and s["t_sec"] >= WARMUP_EXCLUDE_SEC
            and s.get("hr") and s.get("speed_mps")]


def gradient_m_per_km(samples, distance_km):
    """Total climb per km — a rough terrain flag, not a route profile."""
    elevations = [s["elevation_m"] for s in samples if s.get("elevation_m") is not None]
    if len(elevations) < 2 or not distance_km:
        return None
    climb = sum(max(0.0, b - a) for a, b in zip(elevations, elevations[1:]))
    return round(climb / distance_km, 1)


def analyse(stream, activity, is_quality):
    samples = usable(stream.get("samples") or [])
    if not samples:
        return None

    span = samples[-1]["t_sec"] - samples[0]["t_sec"]
    if span < MIN_ANALYSED_SEC:
        return None

    midpoint = samples[0]["t_sec"] + span / 2
    first = [s for s in samples if s["t_sec"] < midpoint]
    second = [s for s in samples if s["t_sec"] >= midpoint]

    ef_first = efficiency_factor(first)
    ef_second = efficiency_factor(second)
    if not ef_first or not ef_second:
        return None

    decoupling = (ef_first - ef_second) / ef_first * 100
    gradient = gradient_m_per_km(stream.get("samples") or [],
                                 (activity or {}).get("distance_km"))

    unreliable = []
    if is_quality:
        unreliable.append("quality session — halves are not comparable")
    if gradient is not None and gradient > HILLY_M_PER_KM:
        unreliable.append(f"hilly ({gradient:.0f} m/km climb)")

    def pace_sec_per_km(ef_samples):
        speeds = [s["speed_mps"] for s in ef_samples if s.get("speed_mps")]
        return 1000 / mean(speeds) if speeds else None

    def pace_fmt(ef_samples):
        sec_per_km = pace_sec_per_km(ef_samples)
        if sec_per_km is None:
            return None
        return f"{int(sec_per_km // 60)}:{int(round(sec_per_km % 60)):02d}"

    pace_first, pace_second = pace_sec_per_km(first), pace_sec_per_km(second)
    pace_delta = (round((pace_second - pace_first) / pace_first * 100, 1)
                  if pace_first and pace_second else None)
    if pace_delta is not None and abs(pace_delta) > STEADY_PACE_TOLERANCE_PCT:
        faster_slower = "slower" if pace_delta > 0 else "quicker"
        unreliable.append(
            f"not steady — second half {abs(pace_delta):.0f}% {faster_slower}")

    return {
        "date": stream.get("date"),
        "activity_id": stream.get("activity_id"),
        "decoupling_pct": round(decoupling, 2),
        "within_threshold": decoupling <= GOOD_THRESHOLD_PCT,
        "analysed_min": round(span / 60, 1),
        "first_half": {"pace": pace_fmt(first),
                       "avg_hr": round(mean([s["hr"] for s in first]), 1),
                       "ef": round(ef_first, 5)},
        "second_half": {"pace": pace_fmt(second),
                        "avg_hr": round(mean([s["hr"] for s in second]), 1),
                        "ef": round(ef_second, 5)},
        "distance_km": (activity or {}).get("distance_km"),
        "gradient_m_per_km": gradient,
        "pace_delta_pct": pace_delta,
        # Present and non-empty means: read this one with caution, or not
        # at all. The number is still recorded rather than hidden.
        "unreliable_reasons": unreliable,
    }


def trend(rows):
    """Where it is heading. Only clean, steady runs count."""
    clean = [r for r in rows if not r["unreliable_reasons"]]
    if len(clean) < 4:
        return {"comparable_runs": len(clean), "direction": None,
                "note": "needs four clean steady runs before a direction means anything"}
    half = len(clean) // 2
    prior = mean(r["decoupling_pct"] for r in clean[:half])
    recent = mean(r["decoupling_pct"] for r in clean[half:])
    change = recent - prior
    if change <= -1.0:
        direction = "improving — pace is costing fewer beats late in runs"
    elif change >= 1.0:
        direction = "worsening — pace is costing more beats late in runs"
    else:
        direction = "stable"
    return {
        "comparable_runs": len(clean),
        "recent_avg_pct": round(recent, 2),
        "prior_avg_pct": round(prior, 2),
        "direction": direction,
    }


def build(streams, activities, session_detail):
    by_id = {a.get("activity_id"): a for a in activities}
    quality_ids = {s.get("activity_id") for s in session_detail if s.get("reps")}
    rows = []
    for stream in streams:
        row = analyse(stream, by_id.get(stream.get("activity_id")),
                      stream.get("activity_id") in quality_ids)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: (r.get("date") or "", r.get("activity_id") or 0))
    return {"threshold_pct": GOOD_THRESHOLD_PCT,
            "warmup_excluded_min": WARMUP_EXCLUDE_SEC // 60,
            "runs": rows,
            "trend": trend(rows)}


def main():
    streams = load(STREAMS_FILE, default=[])
    if not streams:
        print(f"No {STREAMS_FILE} yet — run garmin_pull.py first.")
        with open(OUTPUT_FILE, "w") as f:
            json.dump({"threshold_pct": GOOD_THRESHOLD_PCT, "runs": [],
                       "trend": {"comparable_runs": 0, "direction": None}}, f, indent=2)
        return

    result = build(streams, load(ACTIVITIES_FILE, default=[]),
                   load(SESSION_DETAIL_FILE, default=[]))
    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    clean = [r for r in result["runs"] if not r["unreliable_reasons"]]
    print(f"Decoupling: {len(result['runs'])} run(s) measured, "
          f"{len(clean)} clean and steady -> {OUTPUT_FILE}")
    for r in clean[-5:]:
        print(f"  {r['date']}: {r['decoupling_pct']:+.2f}% over {r['analysed_min']}min "
              f"({r['first_half']['pace']}@{r['first_half']['avg_hr']:.0f} -> "
              f"{r['second_half']['pace']}@{r['second_half']['avg_hr']:.0f})")
    if result["trend"].get("direction"):
        print(f"  trend: {result['trend']['direction']}")


if __name__ == "__main__":
    main()
