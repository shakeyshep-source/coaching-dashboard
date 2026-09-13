"""
test_pace_target_check.py

Run with: python3 test_pace_target_check.py

No framework — the repo has no test dependency and this needs none.
Every derived metric in here has been wrong at least once against real
data before it was trusted (lap merging, decoupling, efficiency), so the
last test runs the checker over the actual training history and prints
what it makes of it.
"""

import json
import sys

import pace_target_check as p

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    got:  {got!r}\n    want: {want!r}")


# --- target_pace parsing: every format actually in training_plan.json ---
check("reps target", p.parse_target_pace("3:44/km for the reps"), [224])
check("blocks target", p.parse_target_pace("3:50/km for the blocks"), [230])
check("two blocks", p.parse_target_pace("3:50/km then 3:54/km"), [230, 234])
check("plain", p.parse_target_pace("3:54/km"), [234])
# A band collapses to its fast end — you have not beaten 3:52-3:56 at 3:54.
check("band -> fast end", p.parse_target_pace("3:52-3:56/km"), [232])
check("band 2", p.parse_target_pace("3:30-3:32/km"), [210])
check("long-run tail", p.parse_target_pace("easy, last 5km at 3:54/km"), [234])
check("prose, no times", p.parse_target_pace("race rhythm"), [])
check("n/a", p.parse_target_pace("N/A"), [])
check("none", p.parse_target_pace(None), [])
# "6min per km" carries no M:SS, so there is nothing to compare against.
check("no colon form", p.parse_target_pace("6min per km"), [])


def detail(mean_pace, count=5):
    return {"reps": {"mean_pace_sec_per_km": mean_pace, "count": count}}


# --- assess_session ---
easy_run = p.assess_session("2026-09-01", {"session_type": "easy"}, {"rpe": 3}, None)
check("easy run is not part of this check", easy_run, None)

fast_and_easy = p.assess_session(
    "2026-09-01", {"session_type": "intervals", "target_pace": "3:44/km for the reps"},
    {"rpe": 6.0}, detail(217.6))
check("beat target at low RPE qualifies", fast_and_easy["qualifying"], True)
check("beat_by", fast_and_easy["beat_by_sec_per_km"], 6.4)

fast_but_hard = p.assess_session(
    "2026-09-01", {"session_type": "intervals", "target_pace": "3:44/km for the reps"},
    {"rpe": 7.0}, detail(217.6))
check("quick but it cost him — no flag", fast_but_hard["qualifying"], False)

easy_but_on_pace = p.assess_session(
    "2026-09-01", {"session_type": "intervals", "target_pace": "3:44/km for the reps"},
    {"rpe": 5.0}, detail(223.0))
check("on target, felt easy — no flag", easy_but_on_pace["qualifying"], False)

# 2.9 sec/km is inside the margin: hitting the target, not beating it.
just_inside = p.assess_session(
    "2026-09-01", {"session_type": "intervals", "target_pace": "3:44/km for the reps"},
    {"rpe": 5.0}, detail(221.1))
check("2.9 sec/km does not qualify", just_inside["qualifying"], False)

no_rpe = p.assess_session(
    "2026-09-01", {"session_type": "intervals", "target_pace": "3:44/km for the reps"},
    {}, detail(217.6))
check("missing RPE is skipped, not guessed", no_rpe["qualifying"], False)
check("skip reason recorded", "no RPE logged" in (no_rpe["skipped"] or ""), True)

no_target = p.assess_session(
    "2026-09-01", {"session_type": "intervals"}, {"rpe": 5.0}, detail(217.6))
check("missing target is skipped", no_target["skipped"], "no pace target on the plan entry")

no_reps = p.assess_session(
    "2026-09-01", {"session_type": "intervals", "target_pace": "3:44/km for the reps"},
    {"rpe": 5.0}, None)
check("missing rep detail is skipped",
      "no rep detail" in (no_reps["skipped"] or ""), True)


# --- evaluate: one is noise, two is a pattern ---
def scenario(dates_and_paces, as_of, previous_flag_date=None):
    plan = {d: {"session_type": "intervals", "target_pace": "3:44/km for the reps"}
            for d, _, _ in dates_and_paces}
    manual = {d: {"rpe": rpe} for d, _, rpe in dates_and_paces}
    det = {d: detail(pace) for d, pace, _ in dates_and_paces}
    return p.evaluate(plan, manual, det, as_of, previous_flag_date)


one = scenario([("2026-09-02", 217.0, 6.0)], "2026-09-14")
check("one qualifying session raises nothing", one["flag"], None)
check("but it is recorded", one["qualifying_in_window"], ["2026-09-02"])

two = scenario([("2026-09-02", 217.0, 6.0), ("2026-09-09", 218.0, 6.0)], "2026-09-14")
check("two in the window flags", two["flag"] is not None, True)
check("flag names both", two["flag"]["sessions"], ["2026-09-02", "2026-09-09"])
check("suggests 3 sec/km", two["flag"]["suggested_tighten_sec_per_km"], 3)

# Same two sessions, but read from far enough away that both have aged out.
stale = scenario([("2026-07-02", 217.0, 6.0), ("2026-07-09", 218.0, 6.0)], "2026-09-14")
check("outside the 28-day window, no flag", stale["flag"], None)

# One in, one out.
straddle = scenario([("2026-08-10", 217.0, 6.0), ("2026-09-09", 218.0, 6.0)], "2026-09-14")
check("only one inside the window, no flag", straddle["flag"], None)

# --- cooldown ---
cooling = scenario(
    [("2026-09-02", 217.0, 6.0), ("2026-09-09", 218.0, 6.0), ("2026-09-12", 217.0, 6.0)],
    "2026-09-14", previous_flag_date="2026-09-09")
check("cooldown suppresses a re-flag", cooling["flag"], None)
check("and says how many sessions are left", cooling["cooldown_remaining"], 2)

served = scenario(
    [("2026-09-10", 217.0, 6.0), ("2026-09-12", 218.0, 6.0), ("2026-09-14", 217.0, 6.0)],
    "2026-09-14", previous_flag_date="2026-09-09")
check("after 3 sessions it can fire again", served["flag"] is not None, True)
check("cooldown served", served["cooldown_remaining"], 0)


# --- against the real training history ---
def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


plan_by_date = {r["date"]: r for r in load("training_plan.json", []) if r.get("date")}
manual_by_date = {r["date"]: r for r in load("manual_log.json", []) if r.get("date")}
detail_by_date = {r["date"]: r for r in load("session_detail.json", []) if r.get("date")}

if plan_by_date and manual_by_date:
    real = p.evaluate(plan_by_date, manual_by_date, detail_by_date, "2026-09-13")
    print("=== against the real history, as of 2026-09-13 ===")
    for r in real["sessions"]:
        if r.get("skipped"):
            print(f"  {r['date']}  skipped: {r['skipped']}")
        else:
            mark = "QUALIFIES" if r["qualifying"] else "         "
            print(f"  {r['date']}  {mark}  target {r['target_sec_per_km']:.0f}s "
                  f"actual {r['actual_sec_per_km']:.1f}s "
                  f"beat {r['beat_by_sec_per_km']:+.1f}  RPE {r['rpe']:.0f}")
    print(f"\n  qualifying in the last {real['window_days']} days: "
          f"{real['qualifying_in_window'] or 'none'}")
    print(f"  flag: {real['flag']['reason'] if real['flag'] else 'not raised'}\n")

if failures:
    print(f"{len(failures)} FAILED:\n")
    print("\n\n".join(failures))
    sys.exit(1)
print("all unit tests passed")
