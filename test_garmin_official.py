"""Unit tests for the official-route parsing in garmin_official.py.

The network half cannot run outside the Action, so these cover the pure
functions that turn Garmin payloads and FIT messages into the locked
schemas the dashboard reads."""

import unittest
from datetime import datetime, timedelta

from garmin_official import (activity_row, add_load_estimates, laps_from_fit,
                             samples_from_fit, wellness_row)


class WellnessRow(unittest.TestCase):
    def test_fields_map_to_locked_schema(self):
        row = wellness_row(
            "2026-10-29",
            {"hrvSummary": {"lastNightAvg": 41, "weeklyAvg": 38, "status": "BALANCED"}},
            {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 84}}},
            {"restingHeartRate": 47, "bodyBatteryHighestValue": 98, "bodyBatteryLowestValue": 30},
        )
        self.assertEqual(row["hrv_last_night"], 41)
        self.assertEqual(row["hrv_weekly_avg"], 38)
        self.assertEqual(row["hrv_status"], "BALANCED")
        self.assertEqual(row["sleep_score"], 84)
        self.assertEqual(row["sleep_duration_hrs"], 7.5)
        self.assertEqual(row["rhr"], 47)
        self.assertEqual((row["body_battery_high"], row["body_battery_low"]), (98, 30))
        self.assertIsNone(row["vo2max"])

    def test_missing_payloads_give_nulls_not_errors(self):
        row = wellness_row("2026-10-29", None, None, None)
        self.assertIsNone(row["rhr"])
        self.assertIsNone(row["sleep_score"])

    def test_unofficial_sleep_nesting_also_read(self):
        row = wellness_row("d", None, {"dailySleepDTO": {"sleepScores": {"overall": {"value": 70}}}}, None)
        self.assertEqual(row["sleep_score"], 70)


class ActivityRow(unittest.TestCase):
    def test_matches_unofficial_conversion(self):
        row = activity_row({
            "activityId": 1, "activityName": "Run", "startTimeLocal": "2026-10-25 09:20:00",
            "activityType": {"typeKey": "running"}, "distance": 10030.0, "duration": 2244.0,
            "averageSpeed": 4.47, "averageHR": 167.0, "activityTrainingLoad": 210.5,
        })
        self.assertEqual(row["date"], "2026-10-25")
        self.assertEqual(row["distance_km"], 10.03)
        self.assertEqual(row["duration_min"], 37.4)
        self.assertEqual(row["avg_pace_min_per_km"], 3.73)
        self.assertEqual(row["training_load"], 210.5)


class FitLaps(unittest.TestCase):
    def test_lap_rows(self):
        rows = laps_from_fit([{"total_distance": 1000.0, "total_timer_time": 215.4, "avg_heart_rate": 155,
                               "max_heart_rate": 162, "avg_cadence": 90, "avg_fractional_cadence": 0.25,
                               "total_ascent": 1, "intensity": "active"}])
        self.assertEqual(rows[0]["index"], 1)
        self.assertEqual(rows[0]["intensity_type"], "ACTIVE")
        self.assertEqual(rows[0]["distance_km"], 1.0)
        self.assertEqual(rows[0]["pace_min_per_km"], 3.59)
        self.assertEqual(rows[0]["cadence"], 180.5)  # strides/min doubled to steps/min
        self.assertEqual(rows[0]["elevation_gain_m"], 1)

    def test_zero_distance_has_no_pace(self):
        self.assertIsNone(laps_from_fit([{"total_distance": 0, "total_timer_time": 60}])[0]["pace_min_per_km"])


class FitSamples(unittest.TestCase):
    def test_thinned_and_relative_time(self):
        t0 = datetime(2026, 10, 25, 9, 20)
        recs = [{"timestamp": t0 + timedelta(seconds=s), "heart_rate": 150, "enhanced_speed": 4.0,
                 "distance": s * 4.0, "enhanced_altitude": 30.0} for s in range(2000)]
        out = samples_from_fit(recs, target=300)
        self.assertEqual(len(out), 300)
        self.assertEqual(out[0]["t_sec"], 0.0)
        self.assertEqual(out[-1]["t_sec"], 1999.0)
        self.assertTrue(all(a["t_sec"] < b["t_sec"] for a, b in zip(out, out[1:])))
        self.assertEqual(out[1]["distance_km"], round(out[1]["t_sec"] * 4.0 / 1000, 4))

    def test_no_timestamps(self):
        self.assertEqual(samples_from_fit([{"heart_rate": 100}]), [])


class LoadEstimates(unittest.TestCase):
    def test_acute_chronic_and_ratio(self):
        acts = [{"date": f"2026-10-{d:02d}", "training_load": 100} for d in range(1, 29)]
        days = add_load_estimates([{"date": "2026-10-28"}], acts)
        self.assertEqual(days[0]["training_load_acute"], 700)
        self.assertEqual(days[0]["training_load_chronic"], 700)
        self.assertEqual(days[0]["acwr_estimate"], 1.0)

    def test_no_load(self):
        days = add_load_estimates([{"date": "2026-10-28"}], [])
        self.assertIsNone(days[0]["acwr_estimate"])


if __name__ == "__main__":
    unittest.main()
