"""Unit tests for volume_check.py: is the planned load on the agreed
curve, behind it (push), or climbing faster than is safe (hold)?"""

import unittest
from datetime import date, timedelta

from volume_check import curve_at, evaluate, note_has_pain

LOAD_PLAN = {"weekly_km": {"2026-12": [70, 80]}, "long_run_km": {"2026-12": [22, 26]},
             "ramp": {"max_pct": 25, "max_km": 12}, "tolerance_km": 3, "holds": []}
SUNDAY = date(2026, 11, 29)  # judges weeks of 30 Nov, 7 Dec, 14 Dec


def plan_week(start, km_per_day, long_km=None):
    rows = {}
    for k in range(7):
        d = (start + timedelta(days=k)).isoformat()
        rows[d] = {"date": d, "session_type": "easy", "target_distance_km": km_per_day}
    if long_km:
        d = (start + timedelta(days=6)).isoformat()
        rows[d] = {"date": d, "session_type": "long", "target_distance_km": long_km}
    return rows


def plan_weeks(*kms):
    plan, start = {}, date(2026, 11, 30)
    for i, km in enumerate(kms):
        plan.update(plan_week(start + timedelta(days=7 * i), km / 7))
    return plan


WEEKLY = [{"week_start": "2026-11-23", "in_progress": True, "km": 60.0}]


class Curve(unittest.TestCase):
    def test_linear_within_month(self):
        self.assertEqual(curve_at({"2026-12": [70, 80]}, date(2026, 12, 1)), 70)
        self.assertEqual(curve_at({"2026-12": [70, 80]}, date(2026, 12, 31)), 80)
        self.assertIsNone(curve_at({"2026-12": [70, 80]}, date(2027, 1, 5)))


class Judgement(unittest.TestCase):
    def run_check(self, plan, manual=None, computed=None, fixtures=None, load_plan=LOAD_PLAN):
        return evaluate(plan, WEEKLY, load_plan, manual or {}, computed or [], fixtures or [], SUNDAY)

    def test_plan_behind_curve_is_flagged_under(self):
        r = self.run_check(plan_weeks(60, 62, 64))
        self.assertEqual(r["status"], "under")
        self.assertEqual(r["weeks"][0]["status"], "under")
        self.assertIn("raises it", r["headline"])

    def test_plan_on_curve_is_on(self):
        r = self.run_check(plan_weeks(71, 74, 76))
        self.assertEqual([w["status"] for w in r["weeks"]], ["on", "on", "on"])

    def test_jump_is_flagged_over(self):
        r = self.run_check(plan_weeks(85, 80, 80))  # from 60, cap is 72
        self.assertEqual(r["weeks"][0]["status"], "over")

    def test_ramp_cap_limits_the_push(self):
        weekly = [{"week_start": "2026-11-23", "in_progress": False, "km": 40.0}]
        r = evaluate(plan_weeks(40, 40, 40), weekly, LOAD_PLAN, {}, [], [], SUNDAY)
        self.assertEqual(r["weeks"][0]["recommended_km"], 50.0)  # 40 * 1.25, not the 70 curve

    def test_hold_by_agreement(self):
        lp = dict(LOAD_PLAN, holds=[{"from": "2026-11-30", "until": "2026-12-06", "reason": "agreed"}])
        r = self.run_check(plan_weeks(40, 72, 75), load_plan=lp)
        self.assertEqual(r["weeks"][0]["status"], "held")

    def test_achilles_gate_holds_the_push(self):
        manual = {"2026-11-27": {"achilles_score": 3}}
        r = self.run_check(plan_weeks(60, 62, 64), manual=manual)
        self.assertEqual(r["weeks"][0]["status"], "held")
        self.assertTrue(r["gates"])

    def test_race_week_is_not_under(self):
        fixtures = [{"date": "2026-12-05", "intent": "target"}]
        r = self.run_check(plan_weeks(50, 72, 75), fixtures=fixtures)
        self.assertEqual(r["weeks"][0]["status"], "race")

    def test_down_week_is_not_under(self):
        plan = plan_weeks(55, 72, 75)
        plan["2026-11-30"]["notes"] = "Down week."
        self.assertEqual(self.run_check(plan)["weeks"][0]["status"], "down")

    def test_unplanned_weeks_are_not_flagged(self):
        r = self.run_check(plan_weeks(71))
        self.assertEqual(r["weeks"][1]["status"], "unplanned")


class Notes(unittest.TestCase):
    def test_pain_words(self):
        self.assertTrue(note_has_pain("calf a bit tight on the hill"))
        self.assertTrue(note_has_pain("achilles sore this morning"))

    def test_no_pain_is_not_pain(self):
        self.assertFalse(note_has_pain("No pain at all.. Ignore HRV"))
        self.assertFalse(note_has_pain("No pains"))
        self.assertFalse(note_has_pain("Leg work.. No pain :)"))
        self.assertFalse(note_has_pain("pain-free"))


if __name__ == "__main__":
    unittest.main()
