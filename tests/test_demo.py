"""Ukázková trasa: jen Central Park, žádná adresa domova, časy v posledních 24 h."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from tractive_ingestor.demo import (
    DEMO_PET_NAME,
    DEMO_TRACKER_ID,
    ZONE_LAT,
    ZONE_LON,
    history_events,
    in_central_park,
    walk_points,
)
from tractive_ingestor.home_geofence import haversine_m


class DemoTrackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 20, 15, 30, tzinfo=UTC)
        self.points = walk_points(self.now)

    def test_identity_is_obviously_fake(self) -> None:
        self.assertEqual(DEMO_PET_NAME, "Luna")
        self.assertTrue(DEMO_TRACKER_ID.startswith("DEMO-"))

    def test_every_point_is_inside_central_park(self) -> None:
        self.assertGreaterEqual(len(self.points), 8)
        self.assertTrue(in_central_park(ZONE_LAT, ZONE_LON))
        for ts, lat, lon in self.points:
            self.assertTrue(in_central_park(lat, lon), (lat, lon))
            self.assertGreater(ts, self.now - timedelta(hours=24))
            self.assertLess(ts, self.now)

    def test_track_is_chronological_and_leaves_the_zone(self) -> None:
        stamps = [ts for ts, _, _ in self.points]
        self.assertEqual(stamps, sorted(stamps))
        start = haversine_m(self.points[0][1], self.points[0][2], ZONE_LAT, ZONE_LON)
        end = haversine_m(self.points[-1][1], self.points[-1][2], ZONE_LAT, ZONE_LON)
        self.assertLess(start, 80)
        self.assertGreater(end, 80)
        self.assertLess(end, 300)

    def test_history_has_a_leave_and_a_return(self) -> None:
        events = history_events(self.now, self.points)
        kinds = [kind for kind, _, _ in events]
        self.assertIn("left_home", kinds)
        self.assertIn("returned_home", kinds)
        for _, ts, _ in events:
            self.assertLess(ts, self.now)


if __name__ == "__main__":
    unittest.main()
