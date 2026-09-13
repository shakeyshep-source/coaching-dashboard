"""
pace_target_check.py — DERIVED (flag logic, imported by build_computed.py)

Job: notice when the prescribed paces have gone stale, and say so. It
never changes a pace target — it raises a flag with a suggested
adjustment, and the change stays a human decision like every other one
in this repo.

The problem it solves: a pace target written off a June race is still
sitting on the plan in September. Sessions get run inside it at an
effort that says there is more there, and nothing surfaces that until a
race eventually forces the correction. One session ahead of target is
noise — a good day, a cool morning, a downhill lap, taper freshness — so
the flag needs the pattern to repeat.

WHY THERE IS NO prescribed_rpe FIELD ON THE PLAN
------------------------------------------------
The trigger needs two halves: he ran quicker than the target, AND it
cost him less than that session should cost. The obvious way to get the
second half is a prescribed RPE per planned session. This does not do
that, deliberately.

CLAUDE.md: "RPE is an observation, never a target. No session in this
plan prescribes an effort score, and none should." That line exists
because on 3 Sep he was told elsewhere that a session "should" have felt
like an 8 and asked whether he ought to have run faster to get there —
which is the exact habit that cost him four years. Putting a
prescribed_rpe on the plan would print an effort target next to every
session on his Plan tab and invite precisely that.

So the expected effort lives here, as a constant, used to judge a
session after the fact and never shown as something to hit. If a plan
entry ever does carry `prescribed_rpe`, it is honoured — the schema
supports it — but nothing in this repo writes one.

LOCKED SCHEMA — add fields, never rename or remove.
"""

import re
from datetime import datetime
from statistics import mean

# How much faster than target counts as "beat it" rather than "hit it".
# 3 sec/km is outside GPS noise over a rep set but well inside a real
# fitness change.
BEAT_MARGIN_SEC = 3.0

# Effort at or below this means the session cost less than it should
# have. His quality sessions run 6-7 (Aug-Sep 2026), so 6 is "comfortably
# inside" rather than "easy" — 7 would make this half of the test do no
# work at all.
EASY_RPE = 6.0

# 28 days, not 21. He runs two quality sessions a week, so 28 days is
# about eight sessions and two qualifying ones is a quarter of the block
# — a pattern. Over 21 days it is two of six, which a single good
# fortnight can produce on its own. It also matches the chronic-load
# window already used elsewhere.
BEAT_WINDOW_DAYS = 28
MIN_QUALIFYING = 2

# After a flag, this many further assessable sessions must pass before
# it can fire again, so a tightened target gets tested before it is
# tightened a second time.
COOLDOWN_SESSIONS = 3
TIGHTEN_BY_SEC = 3

QUALITY_TYPES = ("intervals", "tempo")


def parse_target_pace(text):
    """Pull the work pace(s) out of a plan entry's target_pace string.

    The field is prose written for a human — "3:44/km for the reps",
    "3:50/km then 3:54/km", "3:52-3:56/km", "race rhythm". Returns a
    list of seconds/km, or [] when there is nothing to compare against.

    A hyphenated pair is a band, so it collapses to the FAST end: you
    have only beaten a 3:52-3:56 target by running quicker than 3:52.
    A "then" pair is two work blocks and stays as two values.
    """
    if not text:
        return []
    times = re.findall(r"(\d):(\d{2})", text)
    if not times:
        return []
    secs = [int(m) * 60 + int(s) for m, s in times]
    # "3:30-3:32/km" — a band, not a progression.
    if len(secs) == 2 and re.search(r"\d:\d{2}\s*[-–]\s*\d:\d{2}", text):
        return [min(secs)]
    return secs


def session_pace(detail):
    """Mean rep pace from session_detail.json — never the whole-run
    average, which folds in warm-up, recoveries and cool-down and is
    meaningless for a rep session (see CLAUDE.md)."""
    reps = (detail or {}).get("reps") or {}
    return reps.get("mean_pace_sec_per_km"), (reps.get("count") or 0)


def assess_session(date, planned, logged, detail):
    """Judge one completed session. Returns a dict describing what was
    found — qualifying True/False, or skipped with a reason.

    Skipped is not the same as not qualifying, and both are recorded:
    a session nobody can assess should be visible, not silently absent.
    """
    row = {"date": date, "qualifying": False, "skipped": None}

    session_type = (planned or {}).get("session_type") or (logged or {}).get("session_type")
    if session_type not in QUALITY_TYPES:
        return None  # not a quality session at all — not part of this check

    targets = parse_target_pace((planned or {}).get("target_pace"))
    if not targets:
        row["skipped"] = "no pace target on the plan entry"
        return row

    actual, rep_count = session_pace(detail)
    if actual is None:
        row["skipped"] = "no rep detail — laps not pulled, or no identifiable reps"
        return row

    rpe = (logged or {}).get("rpe")
    if rpe is None:
        # Deliberately NOT falling back to heart rate. CLAUDE.md records
        # that deep fatigue SUPPRESSES HR, so a low HR reads the same as
        # an easy session. Flagging "your targets are soft, run quicker"
        # off a suppressed HR would push a tired athlete to train harder
        # — backwards, and the exact mechanism behind the achilles years.
        row["skipped"] = "no RPE logged — not assessable (HR is not a safe substitute)"
        return row

    target = mean(targets)
    expected_rpe = (planned or {}).get("prescribed_rpe", EASY_RPE)
    beat_by = round(target - actual, 1)

    row.update({
        "session_type": session_type,
        "target_sec_per_km": round(target, 1),
        "actual_sec_per_km": round(actual, 1),
        "beat_by_sec_per_km": beat_by,
        "rpe": rpe,
        "expected_rpe": expected_rpe,
        "reps": rep_count,
        "qualifying": beat_by >= BEAT_MARGIN_SEC and rpe <= expected_rpe,
    })
    return row


def _d(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def evaluate(plan_by_date, manual_by_date, detail_by_date, as_of,
             previous_flag_date=None):
    """Walk every completed quality session up to `as_of` and decide
    whether the pace targets have gone stale.

    `previous_flag_date` is the last date this flag fired, so the
    cooldown can be honoured across runs.
    """
    dates = sorted(set(plan_by_date) | set(manual_by_date))
    assessed = []
    for date in dates:
        if date > as_of:
            break
        row = assess_session(date, plan_by_date.get(date),
                             manual_by_date.get(date), detail_by_date.get(date))
        if row is not None:
            assessed.append(row)

    result = {
        "as_of": as_of,
        "window_days": BEAT_WINDOW_DAYS,
        "beat_margin_sec_per_km": BEAT_MARGIN_SEC,
        "sessions": assessed,
        "qualifying_in_window": [],
        "flag": None,
        "cooldown_remaining": 0,
    }

    cutoff = _d(as_of).toordinal() - BEAT_WINDOW_DAYS
    in_window = [r for r in assessed
                 if r.get("qualifying") and _d(r["date"]).toordinal() > cutoff]
    result["qualifying_in_window"] = [r["date"] for r in in_window]

    if previous_flag_date:
        since = [r for r in assessed
                 if r["date"] > previous_flag_date and r.get("skipped") is None]
        remaining = max(0, COOLDOWN_SESSIONS - len(since))
        result["cooldown_remaining"] = remaining
        if remaining:
            return result

    if len(in_window) < MIN_QUALIFYING:
        return result

    beats = [r["beat_by_sec_per_km"] for r in in_window]
    targets = sorted({r["target_sec_per_km"] for r in in_window})
    result["flag"] = {
        "sessions": [r["date"] for r in in_window],
        "count": len(in_window),
        "mean_beat_sec_per_km": round(mean(beats), 1),
        "suggested_tighten_sec_per_km": TIGHTEN_BY_SEC,
        "current_targets_sec_per_km": targets,
        "reason": (
            f"{len(in_window)} quality sessions in the last {BEAT_WINDOW_DAYS} days "
            f"came in {mean(beats):.1f} sec/km inside target at RPE {EASY_RPE:.0f} "
            f"or below. The paces look stale — consider tightening the 10K target by "
            f"{TIGHTEN_BY_SEC} sec/km, then holding the new pace for "
            f"{COOLDOWN_SESSIONS} sessions before revisiting. Not applied: update the "
            f"target yourself if you agree."
        ),
    }
    return result
