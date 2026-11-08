"""
volume_check.py — DERIVED (flag logic, imported by build_computed.py)

Job: notice, before Shep does, when the planned load is falling behind
the winter curve he agreed — and equally when it is running ahead of
what is safe. His instruction, 8 Nov 2026: "I want all the training load
stats to then be pushing me ... I won't be happy if I'm saying up it and
then you agree, I want you to be noticing ahead of me."

It never edits the plan. It writes volume_target.json, which the Sunday
review and any mid-week reply read: an "under" week obliges the next
proposal to raise it (or to say which gate is holding it), and the
dashboard shows the line under the volume bars so the gap is visible
before anyone asks.

How a week is judged (the current week and the next two):

  curve_km      where the agreed curve says that week should be
                (load_plan.json, linear within each month).
  ramp_cap_km   how far it may climb from the week before when it is
                below the curve — the smaller of +max_pct and +max_km.
                A return from injury climbs back at this rate, not in
                one jump; once on the curve the curve's own slope rules.
  recommended   min(curve_km, ramp_cap_km).

  under   planned < recommended - tolerance, and no gate holds it.
  over    planned > max(curve_km, ramp_cap_km) + tolerance — a spike.
  held    a gate holds the week (see below), so "under" is correct.
  race    a target race falls in the week: lower volume is the taper.
  down    the plan calls it a down week (the curve wants one every 3-4).
  on      otherwise.

Gates — any one and the check does not push:
  * a hold in load_plan.json covering the week (agreed by him);
  * achilles score of 3 or more in the last 7 days;
  * a pain or tightness word in a session note in the last 7 days
    ("no pain" and "pain free" do not count);
  * two or more unexplained readiness flags in the last 7 days.
These are the brief's progression rules: each level is earned —
achilles 0 with no prodrome, HRV/RHR steady.

LOCKED SCHEMA — add fields, never rename or remove.
"""

import re
from datetime import date, timedelta

PAIN_WORDS = re.compile(r"\b(tight\w*|sore\w*|pain\w*|niggl\w*|stiff\w*|ach(e|es|ing|y))\b", re.I)
NOT_PAIN = re.compile(r"\b(no|zero|without)\s+(\w+\s+){0,2}?(pain|pains|niggles?|soreness|tightness)\b"
                      r"|\bpain[- ]?free\b", re.I)


def week_start(d):
    return d - timedelta(days=d.weekday())


def curve_at(ranges, d):
    """Linear within the month: the first of the month is the low end, the
    last day is the high end. None outside the curve."""
    key = d.strftime("%Y-%m")
    if key not in ranges:
        return None
    lo, hi = ranges[key]
    first = d.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    span = (nxt - first).days - 1
    return round(lo + (hi - lo) * (d - first).days / span, 1)


def note_has_pain(note):
    if not note:
        return False
    return bool(PAIN_WORDS.search(NOT_PAIN.sub(" ", note)))


def gates_as_of(today, manual_by_date, computed_rows):
    gates = []
    window = [(today - timedelta(days=k)).isoformat() for k in range(7)]
    ach = [(d, (manual_by_date.get(d) or {}).get("achilles_score")) for d in window]
    high = [(d, s) for d, s in ach if s is not None and s >= 3]
    if high:
        gates.append(f"achilles {high[0][1]} on {high[0][0]}")
    for d in sorted(window):
        if note_has_pain((manual_by_date.get(d) or {}).get("session_notes")):
            gates.append(f"pain/tightness in the note on {d}")
            break
    flags = [r for r in computed_rows if r.get("date") in window
             and r.get("readiness_level") == "flag" and not r.get("recovery_confounders")]
    if len(flags) >= 2:
        gates.append(f"{len(flags)} unexplained readiness flags in 7 days")
    return gates


def evaluate(plan_by_date, weekly, load_plan, manual_by_date, computed_rows, fixtures, today):
    """Judge the current week (or next, on a Sunday) and the two after it."""
    tol = load_plan.get("tolerance_km", 3)
    ramp = load_plan.get("ramp") or {}
    max_pct, max_km = ramp.get("max_pct", 25), ramp.get("max_km", 12)

    first = week_start(today) + (timedelta(days=7) if today.weekday() == 6 else timedelta(0))
    done = [w for w in (weekly or []) if not w.get("in_progress")]
    actual = {w["week_start"]: w.get("km") or 0 for w in (weekly or [])}
    gates = gates_as_of(today, manual_by_date, computed_rows)

    # The week before the first judged week is the base for the climb.
    # Done: what he ran. Still in progress (a Sunday review, or a weekday
    # check of the current week's predecessor): the larger of run and
    # planned, since the last session may not have synced yet.
    prev_start = (first - timedelta(days=7)).isoformat()
    prev_row = next((w for w in weekly or [] if w["week_start"] == prev_start), None)
    prev_planned = sum((plan_by_date.get((first - timedelta(days=7 - k)).isoformat()) or {})
                       .get("target_distance_km") or 0 for k in range(7))
    if prev_row is None:
        prev_km = prev_planned or (done[-1]["km"] if done else None)
    elif prev_row.get("in_progress"):
        prev_km = max(prev_row.get("km") or 0, prev_planned)
    else:
        prev_km = prev_row.get("km") or 0

    race_dates = {f["date"] for f in (fixtures or [])
                  if f.get("date") and f.get("intent") in ("target", "race")}

    weeks = []
    for i in range(3):
        ws = first + timedelta(days=7 * i)
        days = [(ws + timedelta(days=k)).isoformat() for k in range(7)]
        rows = [plan_by_date[d] for d in days if d in plan_by_date]
        planned = round(sum(r.get("target_distance_km") or 0 for r in rows), 1)
        long_km = max([r.get("target_distance_km") or 0 for r in rows
                       if (r.get("session_type") or "").lower() == "long"] or [0])
        mid = ws + timedelta(days=3)
        curve = curve_at(load_plan.get("weekly_km") or {}, mid)
        long_curve = curve_at(load_plan.get("long_run_km") or {}, mid)
        cap = None
        if prev_km:
            cap = round(min(prev_km * (1 + max_pct / 100), prev_km + max_km), 1)
        recommended = (round(min(curve, cap), 1) if curve is not None and cap is not None
                       else curve if curve is not None else None)

        holds = [h for h in load_plan.get("holds") or []
                 if h.get("from", "0000") <= days[-1] and h.get("until", "9999") >= days[0]]
        is_race = any(d in race_dates for d in days)
        is_down = any("down week" in (r.get("notes") or "").lower() for r in rows)

        reason = ""
        if not rows:
            status, reason = "unplanned", "nothing planned yet"
        elif curve is None:
            status = "on"
            reason = "outside the agreed curve"
        elif holds:
            status, reason = "held", holds[0].get("reason", "held by agreement")
        elif is_race:
            status, reason = "race", "a target race this week: lower volume is the taper"
        elif is_down:
            status, reason = "down", "planned down week"
        elif i == 0 and gates:
            status, reason = "held", "gate: " + "; ".join(gates)
        elif recommended is not None and planned < recommended - tol:
            status = "under"
            reason = (f"plan {planned:g} km, curve {curve:g}, safe climb allows {cap:g} — "
                      f"raise towards {recommended:g} km" if cap is not None else
                      f"plan {planned:g} km against a curve of {curve:g}")
        elif cap is not None and planned > cap + tol:
            status = "over"
            reason = (f"plan {planned:g} km climbs too fast from {prev_km:g} — "
                      f"about {cap:g} km is a safe step")
        elif planned > curve * 1.1 + tol:
            status = "over"
            reason = f"plan {planned:g} km is well above the agreed curve ({curve:g})"
        else:
            status = "on"
        if status == "under" and long_curve is not None and long_km and long_km < long_curve - 2:
            reason += f"; long run {long_km:g} km against {long_curve:g}"

        weeks.append({
            "week_start": ws.isoformat(),
            "planned_km": planned,
            "planned_long_km": long_km or None,
            "curve_km": curve,
            "long_curve_km": long_curve,
            "ramp_cap_km": cap,
            "recommended_km": recommended,
            "actual_km": actual.get(ws.isoformat()),
            "status": status,
            "reason": reason,
        })
        # The next week climbs from this one's plan — or its recommendation,
        # if the plan was short, so the curve is chased rather than the gap.
        if status == "under":
            prev_km = recommended
        elif status == "over" and cap is not None:
            prev_km = min(planned, cap)
        elif planned:
            prev_km = planned

    under = [w for w in weeks if w["status"] == "under"]
    over = [w for w in weeks if w["status"] == "over"]
    headline = None
    if under:
        w = under[0]
        headline = (f"Week of {w['week_start']}: plan {w['planned_km']:g} km is under the agreed curve — "
                    f"the next proposal raises it towards {w['recommended_km']:g} km.")
    elif over:
        w = over[0]
        headline = (f"Week of {w['week_start']}: {w['reason']}.")
    return {
        "as_of": today.isoformat(),
        "status": "under" if under else "over" if over else weeks[0]["status"],
        "headline": headline,
        "gates": gates,
        "weeks": weeks,
    }
