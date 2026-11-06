"""Unit tests for garmin_pull.py's route logic: official first, unofficial
for whatever the official route could not supply, Garmin's own training
status laid over our estimate. Fake sessions stand in for both APIs."""

import unittest
from unittest import mock

import garmin_pull
from garmin_pull import (build_days, merge_activities, official_activities, overlay_status,
                         pull_laps_and_streams)


class FakeOfficial:
    def __init__(self, days=None, listing=None, fit=None):
        self.days = days or {}
        self.listing = listing
        self.fit_data = fit or {}

    def get(self, path, **params):
        if path.startswith("/hrv-service/hrv/"):
            d = path.rsplit("/", 1)[1]
            return {"hrvSummary": {"lastNightAvg": self.days[d]}} if d in self.days else None
        if path.startswith("/usersummary-service"):
            d = params["calendarDate"]
            return {"restingHeartRate": 47} if d in self.days else None
        if path.startswith("/activitylist-service"):
            if self.listing is None:
                return None
            return self.listing[params["start"]:params["start"] + params["limit"]]
        return None

    def fit(self, activity_id):
        return self.fit_data.get(activity_id)


class FakeClient:
    """The unofficial garminconnect client, as much of it as is used."""

    def __init__(self, hrv=None, status=True, splits=None):
        self.hrv = hrv or {}
        self.status = status
        self.splits = splits or {}

    def get_rhr_day(self, d):
        return None

    def get_hrv_data(self, d):
        return {"hrvSummary": {"lastNightAvg": self.hrv[d]}} if d in self.hrv else None

    def get_sleep_data(self, d):
        return None

    def get_stats(self, d):
        return None

    def get_training_status(self, d):
        if not self.status:
            return None
        return {
            "mostRecentTrainingStatus": {"latestTrainingStatusData": {"dev": {
                "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 300, "dailyTrainingLoadChronic": 400,
                                         "dailyAcuteChronicWorkloadRatio": 0.75, "acwrStatus": "LOW"},
                "trainingStatusFeedbackPhrase": "RECOVERY_2"}}},
            "mostRecentVO2Max": {"generic": {"vo2MaxValue": 58, "calendarDate": d}},
        }

    def get_activity_splits(self, activity_id):
        return self.splits.get(activity_id)

    def get_activity_details(self, activity_id, *a):
        return None


class Days(unittest.TestCase):
    def test_official_wellness_with_unofficial_status(self):
        rows, status, sources = build_days(FakeOfficial({"2026-11-05": 40}), FakeClient(), ["2026-11-05"])
        self.assertEqual(sources["official"], 1)
        self.assertEqual(rows[0]["hrv_last_night"], 40)
        overlay_status(rows, status)
        self.assertEqual(rows[0]["acwr_garmin"], 0.75)
        self.assertEqual(rows[0]["vo2max"], 58)
        self.assertEqual(rows[0]["training_load_acute"], 300)

    def test_day_missing_from_official_falls_back(self):
        rows, _, sources = build_days(FakeOfficial({}), FakeClient(hrv={"2026-11-05": 38}), ["2026-11-05"])
        self.assertEqual(sources["unofficial"], 1)
        self.assertEqual(rows[0]["hrv_last_night"], 38)

    def test_no_official_session_uses_unofficial(self):
        rows, _, sources = build_days(None, FakeClient(hrv={"2026-11-05": 38}), ["2026-11-05"])
        self.assertEqual(sources["unofficial"], 1)
        self.assertEqual(rows[0]["hrv_last_night"], 38)

    def test_no_unofficial_session_keeps_official_and_estimate(self):
        rows, status, sources = build_days(FakeOfficial({"2026-11-05": 40}), None, ["2026-11-05"])
        self.assertEqual(sources["official"], 1)
        rows[0]["training_load_acute"] = 123.0  # as add_load_estimates would
        overlay_status(rows, status)
        self.assertEqual(rows[0]["training_load_acute"], 123.0)
        self.assertNotIn("acwr_garmin", rows[0])

    def test_status_never_blanks_an_estimate(self):
        rows = [{"date": "d", "training_load_acute": 50.0}]
        overlay_status(rows, {"d": {"training_load_acute": None, "vo2max": 58}})
        self.assertEqual(rows[0]["training_load_acute"], 50.0)
        self.assertEqual(rows[0]["vo2max"], 58)


class Activities(unittest.TestCase):
    def listing(self, n, newest="2026-11-05"):
        return [{"activityId": i, "startTimeLocal": f"{newest} 09:00:00", "distance": 5000.0,
                 "duration": 1500.0, "activityType": {"typeKey": "running"}} for i in range(n)]

    def test_list_failure_returns_none(self):
        self.assertIsNone(official_activities(FakeOfficial(listing=None), "2026-09-10"))

    def test_pages_past_100(self):
        rows = official_activities(FakeOfficial(listing=self.listing(130)), "2026-09-10")
        self.assertEqual(len(rows), 130)

    def test_old_activities_filtered(self):
        rows = official_activities(FakeOfficial(listing=self.listing(3, newest="2026-08-01")), "2026-09-10")
        self.assertEqual(rows, [])

    def test_merge_keeps_training_load_and_history(self):
        existing = [{"activity_id": 1, "date": "2026-01-01", "training_load": None},
                    {"activity_id": 2, "date": "2026-11-01", "training_load": 80.0, "avg_hr": 140}]
        fresh = [{"activity_id": 2, "date": "2026-11-01", "avg_hr": 141}]
        out = merge_activities(existing, fresh)
        self.assertEqual([a["activity_id"] for a in out], [1, 2])
        self.assertEqual(out[1]["avg_hr"], 141)
        self.assertEqual(out[1]["training_load"], 80.0)


class LapsAndStreams(unittest.TestCase):
    RUN = {"activity_id": 7, "date": "2026-11-05", "type": "running", "name": "Run", "duration_min": 30}

    def test_fit_first(self):
        fit_laps = [{"total_distance": 1000.0, "total_timer_time": 240.0}]
        with mock.patch.object(garmin_pull, "read_fit", return_value=(fit_laps, [])):
            laps, _, counts = pull_laps_and_streams(FakeOfficial(fit={7: b"x"}), FakeClient(), [self.RUN], [], [])
        self.assertEqual(counts["fit"], 1)
        self.assertEqual(counts["unofficial_laps"], 0)
        self.assertEqual(laps[0]["laps"][0]["pace_min_per_km"], 4.0)

    def test_missing_fit_falls_back_to_splits(self):
        splits = {7: {"lapDTOs": [{"distance": 1000.0, "duration": 250.0}]}}
        laps, _, counts = pull_laps_and_streams(FakeOfficial(), FakeClient(splits=splits), [self.RUN], [], [])
        self.assertEqual(counts["unofficial_laps"], 1)
        self.assertEqual(laps[0]["activity_id"], 7)

    def test_held_activities_not_refetched(self):
        api = mock.Mock()
        pull_laps_and_streams(api, None, [self.RUN], [{"activity_id": 7}], [])
        api.fit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
