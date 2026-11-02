"""
compare_official.py — does the official route match the current one?

Reads the dashboard's Garmin files (unofficial route, garmin_pull.py)
and their shadow/ twins (official route, garmin_official.py) and writes
shadow/comparison.json: field by field, how often the two agree. This
is the evidence for switching the dashboard over, so it reports
disagreements rather than hiding them. Values are compared, never
printed.
"""

import json
import os

WELLNESS_FIELDS = ["rhr", "hrv_last_night", "hrv_status", "hrv_weekly_avg", "sleep_score",
                   "sleep_duration_hrs", "body_battery_high", "body_battery_low"]
ACTIVITY_FIELDS = ["distance_km", "duration_min", "avg_hr", "max_hr", "elevation_gain_m", "cadence"]
NUMERIC_TOLERANCE = 0.02  # 2% — rounding and late Garmin revisions, not a different number


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def same(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= NUMERIC_TOLERANCE * max(abs(a), abs(b), 1)
    return a == b


def tally(pairs, fields):
    out = {}
    for f in fields:
        agree = sum(1 for a, b in pairs if same(a.get(f), b.get(f)))
        missing_official = sum(1 for a, b in pairs if a.get(f) is not None and b.get(f) is None)
        out[f] = {"compared": len(pairs), "agree": agree, "official_missing": missing_official}
    return out


def compare(main_dir=".", shadow_dir="shadow"):
    report = {}

    main_days = {r["date"]: r for r in (load(f"{main_dir}/garmin_history.json") or [])}
    shadow_days = {r["date"]: r for r in (load(f"{shadow_dir}/garmin_history.json") or [])}
    dates = sorted(set(main_days) & set(shadow_days))
    report["wellness"] = {"days": len(dates), "fields": tally([(main_days[d], shadow_days[d]) for d in dates], WELLNESS_FIELDS)}

    acwr = [(main_days[d].get("acwr_garmin"), shadow_days[d].get("acwr_estimate")) for d in dates
            if main_days[d].get("acwr_garmin") is not None and shadow_days[d].get("acwr_estimate") is not None]
    report["acwr"] = {
        "days": len(acwr),
        "mean_abs_difference": round(sum(abs(a - b) for a, b in acwr) / len(acwr), 3) if acwr else None,
    }

    main_acts = {a["activity_id"]: a for a in (load(f"{main_dir}/garmin_activities.json") or [])}
    shadow_acts = {a["activity_id"]: a for a in (load(f"{shadow_dir}/garmin_activities.json") or [])}
    window = {i for i, a in shadow_acts.items()}
    first = min((a["date"] for a in shadow_acts.values()), default=None)
    expected = {i for i, a in main_acts.items() if first and a.get("date", "") >= first}
    both = sorted(expected & window)
    report["activities"] = {
        "in_main_since_shadow_start": len(expected),
        "found_in_official": len(both),
        "missing_from_official": len(expected - window),
        "fields": tally([(main_acts[i], shadow_acts[i]) for i in both], ACTIVITY_FIELDS),
    }

    main_laps = {r["activity_id"]: r["laps"] for r in (load(f"{main_dir}/garmin_laps.json") or [])}
    shadow_laps = {r["activity_id"]: r["laps"] for r in (load(f"{shadow_dir}/garmin_laps.json") or [])}
    lap_ids = sorted(set(main_laps) & set(shadow_laps))
    count_match = sum(1 for i in lap_ids if len(main_laps[i]) == len(shadow_laps[i]))
    pace_diffs = []
    for i in lap_ids:
        if len(main_laps[i]) != len(shadow_laps[i]):
            continue
        for a, b in zip(main_laps[i], shadow_laps[i]):
            if a.get("pace_min_per_km") and b.get("pace_min_per_km"):
                pace_diffs.append(abs(a["pace_min_per_km"] - b["pace_min_per_km"]) * 60)
    report["laps"] = {
        "activities_compared": len(lap_ids),
        "same_lap_count": count_match,
        "max_pace_difference_sec_per_km": round(max(pace_diffs), 1) if pace_diffs else None,
        "pairs_fields": tally([(a, b) for i in lap_ids if len(main_laps[i]) == len(shadow_laps[i])
                               for a, b in zip(main_laps[i], shadow_laps[i])],
                              ["intensity_type", "distance_km", "duration_sec", "avg_hr", "cadence"]),
    }

    main_streams = {r["activity_id"]: r["samples"] for r in (load(f"{main_dir}/garmin_streams.json") or [])}
    shadow_streams = {r["activity_id"]: r["samples"] for r in (load(f"{shadow_dir}/garmin_streams.json") or [])}
    stream_ids = sorted(set(main_streams) & set(shadow_streams))

    def mean_hr(samples):
        hrs = [s["hr"] for s in samples if s.get("hr")]
        return sum(hrs) / len(hrs) if hrs else None

    hr_diffs = [abs(mean_hr(main_streams[i]) - mean_hr(shadow_streams[i])) for i in stream_ids
                if mean_hr(main_streams[i]) and mean_hr(shadow_streams[i])]
    report["streams"] = {
        "activities_compared": len(stream_ids),
        "max_mean_hr_difference": round(max(hr_diffs), 1) if hr_diffs else None,
    }
    return report


def main():
    if not os.path.isdir("shadow"):
        print("No shadow/ data yet - nothing to compare.")
        return
    report = compare()
    with open("shadow/comparison.json", "w") as f:
        json.dump(report, f, indent=2)
    w = report["wellness"]
    print(f"Official vs current: {w['days']} days compared")
    for field, t in w["fields"].items():
        print(f"  {field}: {t['agree']}/{t['compared']} agree, {t['official_missing']} missing from official")
    a = report["activities"]
    print(f"  activities: {a['found_in_official']}/{a['in_main_since_shadow_start']} found")
    print(f"  laps: {report['laps']['same_lap_count']}/{report['laps']['activities_compared']} same lap count, "
          f"max pace diff {report['laps']['max_pace_difference_sec_per_km']} s/km")
    print(f"  streams: {report['streams']['activities_compared']} compared, "
          f"max mean-HR diff {report['streams']['max_mean_hr_difference']}")


if __name__ == "__main__":
    main()
