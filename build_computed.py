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
import re
from datetime import date, datetime
from statistics import mean

GARMIN_FILE = "garmin_data.json"
HISTORY_FILE = "garmin_history.json"
MANUAL_FILE = "manual_log.json"
# His words to the coach count as notes too - see coach_thread_notes().
LATEST_REVIEW_FILE = "weekly_review_latest.json"
WEATHER_FILE = "weather.json"
# weather.json only looks forward; past days need the accumulated log.
WEATHER_LOG_FILE = "weather_log.json"
PLAN_FILE = "training_plan.json"
FLAGS_LOG_FILE = "flags_log.json"
RECOVERY_FILE = "recovery_log.json"
OUTPUT_FILE = "computed_data.json"

HARD_SESSION_TYPES = {"intervals", "tempo", "long", "race"}
# Long runs are hard, but they're aerobic volume — recovery from them is a
# different question, so the recovery metric watches the sharp stuff only.
QUALITY_TYPES = {"intervals", "tempo", "race"}


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

# Non-training explanations for a recovery dip. Alcohol, a short night,
# illness or travel all suppress HRV and lift RHR with no training stress
# behind them — and cutting the training block in response would be
# treating the wrong problem entirely.
#
# Deliberately a signal for a human to weigh, never an automatic
# override: alcohol explains one flat morning, it does not explain a
# baseline sliding for a week. Persistence beats the excuse.
CONFOUNDER_PATTERNS = {
    # His own word is "ciders", which this missed twice - 16 Aug ("a lot
    # of ciders were consumed") and 22 Aug ("a couple of ciders watching
    # the football") both came out with no alcohol flag. Match how he
    # actually writes, not a tidy list of drink names.
    "alcohol": r"\b(beers?|wine|pints?|drinks?|drinking|pub|alcohol|hangover"
               r"|ciders?|lagers?|ale|stout|guinness|prosecco|champagne"
               r"|gin|vodka|rum|whisk(e)?y|cocktails?|booze|boozy|tipsy"
               r"|night out|few too many)\b",
    "short night": r"\b(late night|bad night|barely slept|poor sleep|broken sleep|up all night|no sleep)\b",
    # "cold" on its own is weather far more often than illness in his
    # notes ("cold and windy", "cooler, 14c"), so it needs company.
    "illness": r"\b(ill|unwell|illness|flu|virus|bug|sore throat|fever|chesty"
               r"|man ?flu|streaming|snotty)\b|\b(a|bad|head|chest|full of|rotten) cold\b",
    "stress": r"\b(stress(ed|ful)?)\b",
    "travel": r"\b(travell?ing|travelled|flight|flew|jet ?lag|long drive)\b",
}


def detect_confounders(*notes):
    """Non-training causes the athlete himself flagged, from any of his
    own words for that day — the daily log note and anything he told the
    coach through the review form."""
    text = " ".join(n.lower() for n in notes if n)
    if not text:
        return []
    return sorted(name for name, pat in CONFOUNDER_PATTERNS.items() if re.search(pat, text))


def coach_thread_notes():
    """What he said to the coach, keyed by the day he said it.

    Principle 3 says never cut training for a dip he has already
    explained — but the explanation only counted if it went in the daily
    log. On 22 Aug he told the coach "a couple of ciders last night
    watching the football, in case recovery looks off" and the day still
    came out with no confounder, because this file only ever read
    manual_log.json. The ask-the-coach thread is his words too.
    """
    latest = load(LATEST_REVIEW_FILE) or {}
    if isinstance(latest, list):
        return {}
    entries = []
    current = latest.get("athlete_response")
    if current:
        entries.append((current.get("timestamp"), current.get("thoughts")))
    for turn in latest.get("conversation") or []:
        entries.append((turn.get("athlete_timestamp"), turn.get("athlete_thoughts")))

    by_date = {}
    for stamp, text in entries:
        if not stamp or not text:
            continue
        try:
            day = datetime.strptime(stamp.split()[0], "%d/%m/%Y").date().isoformat()
        except (ValueError, IndexError):
            continue
        by_date[day] = (by_date.get(day, "") + " " + text).strip()
    return by_date


def readiness_level(rhr_delta, hrv_delta, sleep_score, achilles, heat_risk):
    """Traffic light for the dashboard hero: 'good' | 'caution' | 'flag'.
    Mirrors summarise() severity so the light and the sentence never
    disagree."""
    rhr_elevated = rhr_delta is not None and rhr_delta > 2
    hrv_depressed = hrv_delta is not None and hrv_delta < -2
    if (achilles and achilles >= 3) or (rhr_elevated and hrv_depressed):
        return "flag"
    if rhr_elevated or hrv_depressed or heat_risk == "high" or \
       (sleep_score is not None and sleep_score < 70):
        return "caution"
    return "good"


def summarise(rhr_delta, hrv_delta, sleep_score, achilles, heat_risk, confounders=()):
    """One sentence for the hero strip.

    When a dip coincides with a non-training cause the athlete has noted
    himself, name that cause rather than implying training load — and say
    plainly that the week doesn't need changing. A drink genuinely does
    suppress recovery, so "keep today easy" still stands; what would be
    wrong is concluding the block is too hard and cutting it.
    """
    if achilles and achilles >= 3:
        return "Achilles flagged. Consider an easy day or rest."

    rhr_elevated = rhr_delta is not None and rhr_delta > 2
    hrv_depressed = hrv_delta is not None and hrv_delta < -2
    heat = " Heat risk is high today too — watch for respiratory symptoms." if heat_risk == "high" else ""
    reason = ", ".join(confounders)

    if rhr_elevated and hrv_depressed:
        if confounders:
            return (f"Recovery down on both HRV and RHR, but you noted {reason} — read it as that, "
                    f"not training load. Keep today easy; no reason to change the week." + heat)
        return "Recovery below baseline on both HRV and RHR. Keep today easy." + heat

    if rhr_elevated or hrv_depressed:
        if confounders:
            return (f"Recovery slightly below baseline, but you noted {reason} — "
                    f"likely that rather than training load. Train as planned unless it persists." + heat)
        return "Recovery slightly below baseline. Fine to train but stay conservative." + heat

    if heat_risk == "high":
        return "Recovery looks normal but heat risk is high today. Adjust effort accordingly."
    if sleep_score is not None and sleep_score < 70:
        return "Sleep below average last night. Ease into today."
    return "Recovery looks normal. Fine for planned training."


# How many days after a hard session to keep looking for a return to
# normal before calling it unrecovered.
RECOVERY_WINDOW_DAYS = 7


def recovery_after_sessions(history, plan_by_date, manual_by_date, weather_by_date,
                            thread_by_date=None):
    """How long HRV and RHR take to come back after each quality session.

    The point of this metric is the DURATION, tracked over months. Fresh,
    you bounce back inside a day; as fatigue accumulates it stretches to
    two or three, and that stretching shows up before anything hurts.

    Crucially the reference is the 7-day baseline as it stood the day
    BEFORE the session, not the rolling baseline on the day. A rolling
    baseline drifts down to meet a tiring athlete — through the week of
    3-9 Aug it fell 45.7 to 36.3, so by the Saturday a genuinely
    suppressed HRV of 37 read as +0.3, "fully recovered". Freezing the
    reference before the session avoids grading the week against its own
    decline.
    """
    thread_by_date = thread_by_date or {}
    rows = sorted(history, key=lambda r: r["date"])
    dates = [r["date"] for r in rows]
    idx = {d: i for i, d in enumerate(dates)}
    hrv = [r.get("hrv_last_night") for r in rows]
    rhr = [r.get("rhr") for r in rows]

    def day_type(d):
        logged = (manual_by_date.get(d) or {}).get("session_type")
        return logged or (plan_by_date.get(d) or {}).get("session_type")

    sessions = []
    pending = []
    for i, d in enumerate(dates):
        if day_type(d) not in QUALITY_TYPES:
            continue
        # Reference = normal as it stood before this session landed.
        hrv_ref = rolling_baseline(hrv, i)
        rhr_ref = rolling_baseline(rhr, i)
        if hrv_ref is None and rhr_ref is None:
            continue

        def days_back(series, ref, recovered):
            """Days until the metric returns to ref, or None if it hasn't
            yet (and None too if we simply ran out of data to look at)."""
            if ref is None:
                return None, True
            for step in range(1, RECOVERY_WINDOW_DAYS + 1):
                j = i + step
                if j >= len(series):
                    return None, False        # window not yet complete
                if series[j] is not None and recovered(series[j], ref):
                    return step, True
            return None, True                  # looked, never came back

        hrv_days, hrv_settled = days_back(hrv, hrv_ref, lambda v, r: v >= r)
        rhr_days, rhr_settled = days_back(rhr, rhr_ref, lambda v, r: v <= r)
        if not (hrv_settled and rhr_settled):
            # Too recent to judge — the window has not run out yet, so a
            # number now would be a guess. Recorded as pending rather
            # than dropped: a session vanishing from the card for two or
            # three days looks like a bug, and asking about it is
            # reasonable.
            pending.append({
                "date": d,
                "session_type": day_type(d),
                "days_since": len(dates) - 1 - i,
                "hrv_back": hrv_days,
                "rhr_back": rhr_days,
                "hrv_reference": hrv_ref,
                "rhr_reference": rhr_ref,
            })
            continue

        found = [x for x in (hrv_days, rhr_days) if x is not None]
        # The slower metric governs — both have to be back to call it recovered.
        overall = max(found) if len(found) == 2 else None
        # Another hard session inside the window muddies the reading.
        interrupted = any(
            day_type(dates[k]) in QUALITY_TYPES
            for k in range(i + 1, min(i + (overall or RECOVERY_WINDOW_DAYS) + 1, len(dates)))
        )
        # A drink or a short night inside the window stretches recovery for
        # reasons that have nothing to do with the session, so record it
        # rather than letting it quietly inflate the trend.
        window_confounders = sorted({
            c
            for k in range(i, min(i + (overall or RECOVERY_WINDOW_DAYS) + 1, len(dates)))
            for c in detect_confounders(
                (manual_by_date.get(dates[k]) or {}).get("session_notes"),
                thread_by_date.get(dates[k]),
            )
        })
        sessions.append({
            "date": d,
            "session_type": day_type(d),
            "confounders_in_window": window_confounders,
            "hrv_reference": hrv_ref,
            "hrv_recovery_days": hrv_days,
            "rhr_reference": rhr_ref,
            "rhr_recovery_days": rhr_days,
            "recovery_days": overall,
            "recovered": overall is not None,
            "interrupted_by_next_session": interrupted,
            "temp_max_c": (weather_by_date.get(d) or {}).get("temp_max_c"),
            "note": (manual_by_date.get(d) or {}).get("session_notes"),
        })

    clean = [s for s in sessions if s["recovery_days"] is not None]
    trend = None
    if len(clean) >= 4:
        recent = [s["recovery_days"] for s in clean[-3:]]
        prior = [s["recovery_days"] for s in clean[-6:-3]] or None
        recent_avg = round(mean(recent), 1)
        prior_avg = round(mean(prior), 1) if prior else None
        direction = "not enough history"
        if prior_avg is not None:
            gap = recent_avg - prior_avg
            direction = ("lengthening — recovery is taking longer than it was"
                         if gap >= 0.5 else
                         "shortening — bouncing back quicker than before"
                         if gap <= -0.5 else "stable")
        trend = {"recent_avg_days": recent_avg, "prior_avg_days": prior_avg,
                 "direction": direction, "sessions_measured": len(clean)}

    return {"generated": date.today().isoformat() if hasattr(date, "today") else None,
            "window_days": RECOVERY_WINDOW_DAYS,
            "sessions": sessions, "pending": pending, "trend": trend}


def main():
    garmin_rows = load(GARMIN_FILE)
    manual_rows = load(MANUAL_FILE)
    # Merge the accumulated history with the forecast so past days carry
    # their real conditions and future days still carry the forecast.
    weather_rows = load(WEATHER_LOG_FILE) + load(WEATHER_FILE)
    plan_rows = load(PLAN_FILE)
    flags_log = load(FLAGS_LOG_FILE)
    manual_by_date = index_by_date(manual_rows)
    thread_by_date = coach_thread_notes()
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

        confounders = detect_confounders(
            manual.get("session_notes"), thread_by_date.get(row["date"])
        )
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
            "sleep_duration_hrs": row.get("sleep_duration_hrs"),
            "body_battery_high": row.get("body_battery_high"),
            "achilles_score": manual.get("achilles_score"),
            "rpe": manual.get("rpe"),
            "session_type_logged": manual.get("session_type"),
            "session_notes": manual.get("session_notes"),
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
            "recovery_confounders": confounders,
            "today_summary": summarise(rhr_delta, hrv_delta, row.get("sleep_score"), manual.get("achilles_score"), heat_risk, confounders),
            "readiness_level": readiness_level(rhr_delta, hrv_delta, row.get("sleep_score"), manual.get("achilles_score"), heat_risk),
            "plan_flag_reasons": flag_reasons,
        })

    # Recovery works off accumulated history, not the 14-day window, so
    # the trend can eventually span months rather than a fortnight.
    history = load(HISTORY_FILE) or garmin_rows
    recovery = recovery_after_sessions(history, plan_by_date, manual_by_date,
                                       weather_by_date, thread_by_date)
    with open(RECOVERY_FILE, "w") as f:
        json.dump(recovery, f, indent=2)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(computed, f, indent=2)
    with open(FLAGS_LOG_FILE, "w") as f:
        json.dump(flags_log, f, indent=2)

    print(f"Computed {len(computed)} days -> {OUTPUT_FILE}")
    meas = [x for x in recovery["sessions"] if x["recovery_days"] is not None]
    print(f"Recovery: {len(meas)} measurable session(s) -> {RECOVERY_FILE}"
          + (f"; recent avg {recovery['trend']['recent_avg_days']}d, "
             f"{recovery['trend']['direction']}" if recovery.get("trend") else ""))
    if flags_log:
        print(f"Flags log has {len(flags_log)} total entries")


if __name__ == "__main__":
    main()
