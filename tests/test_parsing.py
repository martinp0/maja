"""Parsování reálných tvarů Tractive payloadů (zachyceno 2026-10-19, tracker TG6C).

Vzorky níže mají stejnou STRUKTURU jako reálné fixtures, ale FIKTIVNÍ souřadnice
a ID (skutečné fixtures obsahují polohu domova a do gitu nepatří).
Pokud lokálně existuje `fixtures/*.jsonl`, projede se parserem i ta (jinak skip).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tractive_ingestor import parsing

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
PRAGUE = ZoneInfo("Europe/Prague")

POS_REPORT = {
    "time": 1792368676,
    "time_rcvd": 1792368678,
    "pos_status": None,
    "latlong": [50.0, 14.0],  # fiktivní
    "speed": None,
    "altitude": 260,
    "report_id": "rep-pos-1",
    "sensor_used": "KNOWN_WIFI",
    "nearby_user_id": None,
    "pos_uncertainty": 30,
    "power_saving_zone_id": "zone-1",
    "_id": "TRK00001",
    "_type": "device_pos_report",
    "_version": "v1",
}

HW_REPORT = {
    "time": 1792342783,
    "battery_level": 63,
    "clip_mounted_state": None,
    "temperature_state": None,
    "report_id": "rep-hw-1",
    "power_saving_zone_id": "zone-1",
    "hw_status": None,
    "_id": "TRK00001",
    "_type": "device_hw_report",
    "_version": "v1",
}

TRACKER = {
    "hw_id": "TRK00001",
    "model_number": "TG6C",
    "geofence_sensitivity": "HIGH",
    "hw_edition": "BROWN-LINES",
    "bluetooth_mac": None,
    "capabilities": ["LT", "BUZZER", "LED", "BLE", "WIFI_ZONE", "SLEEP_TRACKING", "BARK_DETECTION"],
    "supported_geofence_types": ["CIRCLE", "RECTANGLE", "POLYGON"],
    "fw_version": "011.092-TRV6-009-e7ce01",
    "battery_save_mode": False,
    "state": "OPERATIONAL",
    "state_reason": "POWER_SAVING",
    "charging_state": "NOT_CHARGING",
    "battery_state": "REGULAR",
    "power_saving_zone_id": "zone-1",
    "prioritized_zone_id": "zone-1",
    "prioritized_zone_type": "POWER_SAVING",
    "prioritized_zone_last_seen_at": 1792368676,
    "prioritized_zone_entered_at": 1792341883,
    "_id": "TRK00001",
    "_type": "tracker",
}

PET = {
    "created_at": 1790083178,
    "device_id": "TRK00001",
    "details": {
        "name": "Testpes",
        "pet_type": "DOG",
        "breed_ids": ["101"],
        "gender": "F",
        "birthday": 1537574400,  # 2018-09-22
        "height": 0.4,
        "weight": 12000,
        "neutered": False,
        "profile_picture_id": "pic",
        "gallery_picture_ids": ["a", "b"],
        "activity_settings": {"daily_goal": 1000, "daily_distance_goal": 0, "daily_active_minutes_goal": 140},
    },
    "home_location": [50.5, 14.5],  # fiktivní
    "_id": "PET00001",
    "_type": "pet",
}

HEALTH = {
    "petId": "PET00001",
    "activityDataSyncedAt": "2026-10-19T00:11:31.681516621Z",
    "activity": {"minutesActive": 1, "minutesGoal": 140, "hourlyDistribution": [1] + [0] * 23},
    "rest": None,
    "sleep": {"minutesDaySleep": 0, "minutesNightSleep": 129, "minutesCalm": 1},
    "bark": {"status": "NORMAL", "dayOffset": -1},
    "restingHeartRate": {"status": "NORMAL", "dayOffset": 0},
    "restingRespiratoryRate": {"status": "NORMAL", "dayOffset": 0},
    "scratch": {"status": "INFREQUENT", "dayOffset": 0},
    "healthAlerts": {"unseenCount": 0},
    "associatedData": [{"type": "HEALTH_WEEKLY_REPORT"}],
    "separationPhaseStatus": {"isPhaseOngoing": False, "phaseStartedAt": None},
    "stateTimeline": None,
}


class PosReportTest(unittest.TestCase):
    def test_real_shape(self) -> None:
        rec = parsing.position_from_pos_report("TRK00001", POS_REPORT)
        assert rec is not None
        self.assertEqual(rec.tracker_id, "TRK00001")
        self.assertEqual(rec.ts, datetime.fromtimestamp(1792368676, tz=UTC))  # sekundy, ne ms
        self.assertEqual((rec.lat, rec.lon), (50.0, 14.0))
        self.assertEqual(rec.accuracy_m, 30.0)
        self.assertIsNone(rec.speed)
        self.assertEqual(rec.altitude_m, 260.0)
        self.assertEqual(rec.sensor_used, "KNOWN_WIFI")
        self.assertEqual(rec.power_saving_zone_id, "zone-1")
        self.assertEqual(rec.origin, "pos_report")
        self.assertIs(rec.raw, POS_REPORT)

    def test_tolerant(self) -> None:
        self.assertIsNone(parsing.position_from_pos_report("T", {}))
        self.assertIsNone(parsing.position_from_pos_report("T", {"time": 1, "latlong": None}))
        rec = parsing.position_from_pos_report(
            "T", {"time": 1792368676000, "latlong": [1, 2], "altitude": None, "sensor_used": "GPS"}
        )
        assert rec is not None
        self.assertEqual(rec.ts, datetime.fromtimestamp(1792368676, tz=UTC))  # ms heuristika
        self.assertIsNone(rec.altitude_m)
        self.assertIsNone(rec.accuracy_m)
        self.assertEqual(rec.sensor_used, "GPS")


class HwReportTest(unittest.TestCase):
    def test_real_shape_with_tracker_details(self) -> None:
        rec = parsing.hw_from_report("TRK00001", HW_REPORT, TRACKER)
        assert rec is not None
        self.assertEqual(rec.ts, datetime.fromtimestamp(1792342783, tz=UTC))
        self.assertEqual(rec.battery_pct, 63)
        self.assertIs(rec.charging, False)
        self.assertEqual(rec.charging_state, "NOT_CHARGING")
        self.assertEqual(rec.battery_state, "REGULAR")
        self.assertEqual(rec.tracker_state, "OPERATIONAL")
        self.assertIs(rec.power_saving, True)
        self.assertEqual(rec.power_saving_zone_id, "zone-1")
        self.assertIsNone(rec.clip_mounted_state)
        self.assertIsNone(rec.temperature_state)
        self.assertIsNone(rec.hw_status)
        self.assertEqual(rec.report_id, "rep-hw-1")
        self.assertFalse(hasattr(rec, "firmware"))  # firmware je v trackers, ne v hw_reportu

    def test_without_details(self) -> None:
        rec = parsing.hw_from_report("T", HW_REPORT)
        assert rec is not None
        self.assertIsNone(rec.charging)
        self.assertIsNone(rec.power_saving)
        self.assertIsNone(parsing.hw_from_report("T", {}))


class TrackableTest(unittest.TestCase):
    def test_tracker(self) -> None:
        tr = parsing.tracker_from_raw("TRK00001", TRACKER)
        self.assertEqual(tr.model_number, "TG6C")
        self.assertEqual(tr.fw_version, "011.092-TRV6-009-e7ce01")
        self.assertIn("BARK_DETECTION", tr.capabilities)
        self.assertEqual((tr.state, tr.state_reason), ("OPERATIONAL", "POWER_SAVING"))
        self.assertEqual(tr.charging_state, "NOT_CHARGING")
        self.assertEqual(parsing.tracker_from_raw("X", None).capabilities, [])

    def test_pet(self) -> None:
        pet = parsing.pet_from_raw("PET00001", PET)
        self.assertEqual(pet.name, "Testpes")
        self.assertEqual(pet.tracker_id, "TRK00001")
        self.assertEqual(pet.pet_type, "DOG")
        self.assertEqual(pet.breed_ids, ["101"])
        self.assertEqual(pet.gender, "F")
        self.assertEqual(pet.birthday, date(2018, 9, 22))
        self.assertEqual(pet.weight_g, 12000)
        self.assertIs(pet.neutered, False)
        self.assertEqual((pet.home_lat, pet.home_lon), (50.5, 14.5))
        self.assertEqual(pet.daily_active_minutes_goal, 140)

    def test_pet_tolerant(self) -> None:
        pet = parsing.pet_from_raw("P", {"details": None, "home_location": None}, tracker_id="T")
        self.assertEqual(pet.tracker_id, "T")
        self.assertIsNone(pet.home_lat)
        self.assertIsNone(pet.birthday)


class HealthTest(unittest.TestCase):
    def test_real_shape(self) -> None:
        rec = parsing.health_from_payload(HEALTH, fallback_day=date(2000, 1, 1), tz=PRAGUE)
        assert rec is not None
        self.assertEqual(rec.pet_id, "PET00001")
        self.assertEqual(rec.day, date(2026, 10, 19))  # 00:11 UTC = 02:11 Praha
        self.assertEqual(rec.synced_at, datetime(2026, 10, 19, 0, 11, 31, 681516, tzinfo=UTC))
        self.assertEqual((rec.minutes_active, rec.minutes_goal), (1, 140))
        self.assertEqual(len(rec.hourly_distribution or []), 24)
        self.assertEqual((rec.sleep_day_min, rec.sleep_night_min, rec.sleep_calm_min), (0, 129, 1))
        self.assertEqual(rec.resting_hr_status, "NORMAL")
        self.assertEqual(rec.resting_rr_status, "NORMAL")
        self.assertEqual(rec.bark_status, "NORMAL")
        self.assertEqual(rec.scratch_status, "INFREQUENT")

    def test_day_uses_local_tz(self) -> None:
        data = {**HEALTH, "activityDataSyncedAt": "2026-10-18T22:30:00Z"}  # 00:30 Praha dalšího dne
        self.assertEqual(parsing.health_from_payload(data, fallback_day=date(2000, 1, 1), tz=PRAGUE).day, date(2026, 10, 19))
        self.assertEqual(parsing.health_from_payload(data, fallback_day=date(2000, 1, 1)).day, date(2026, 10, 18))

    def test_tolerant(self) -> None:
        rec = parsing.health_from_payload({"content": {"activity": None, "sleep": None}}, fallback_day=date(2026, 1, 2), pet_id="P")
        assert rec is not None
        self.assertEqual(rec.day, date(2026, 1, 2))
        self.assertIsNone(rec.minutes_active)
        self.assertIsNone(rec.hourly_distribution)
        self.assertIsNone(rec.bark_status)
        self.assertIsNone(parsing.health_from_payload({}, fallback_day=date(2026, 1, 2)))


@unittest.skipUnless((FIXTURES / "pos_report.jsonl").exists(), "lokální fixtures/*.jsonl nejsou k dispozici")
class LocalFixturesTest(unittest.TestCase):
    """Projede skutečné zachycené payloady (necommitují se) – jen kontrola, že parser nic nezahodí."""

    @staticmethod
    def rows(name: str) -> list[dict]:
        path = FIXTURES / f"{name}.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_all(self) -> None:
        trackers = {}
        for row in self.rows("trackable"):
            tr = parsing.tracker_from_raw("?", row["payload"]["tracker"])
            pet = parsing.pet_from_raw("?", row["payload"]["pet"])
            self.assertTrue(tr.model_number and tr.fw_version and tr.capabilities)
            self.assertTrue(pet.name and pet.home_lat is not None and pet.weight_g)
            trackers[tr.tracker_id] = row["payload"]["tracker"]
        for row in self.rows("pos_report"):
            rec = parsing.position_from_pos_report(row["tracker_id"], row["payload"])
            assert rec is not None
            self.assertIsNotNone(rec.sensor_used)
            self.assertGreater(rec.ts.year, 2020)
        for row in self.rows("hw_report"):
            rec = parsing.hw_from_report(row["tracker_id"], row["payload"], trackers.get(row["tracker_id"]))
            assert rec is not None
            self.assertIsNotNone(rec.battery_pct)
            self.assertIsNotNone(rec.charging)
        for row in self.rows("health"):
            rec = parsing.health_from_payload(row["payload"], fallback_day=date(2000, 1, 1), tz=PRAGUE)
            assert rec is not None
            self.assertNotEqual(rec.day, date(2000, 1, 1))
            self.assertEqual(len(rec.hourly_distribution or []), 24)


if __name__ == "__main__":
    unittest.main()
