"""Geofence doma/venku (src/tractive_ingestor/home_geofence.py + /api/geofence/*).

Souřadnice domova v testech jsou SMYŠLENÉ (ne skutečný domov). DB testy běží jen
s TEST_DATABASE_URL (prázdná DB s PostGIS), jinak skip.

    python -m pytest tests/test_geofence.py -v
"""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tractive_ingestor.home_geofence import (
    AWAY,
    HOME,
    LEFT_HOME,
    RETURNED_HOME,
    UNKNOWN,
    Checkpoint,
    HomeConfig,
    Sample,
    classify,
    haversine_m,
    step,
)

HLAT, HLON = 48.0, 17.0  # smyšlený "domov"
T0 = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
CFG = HomeConfig(lat=HLAT, lon=HLON, radius_m=80, leave_s=120, return_s=60, max_accuracy_m=150)


def at(sec: float, north_m: float, *, acc: float | None = 10.0, sensor: str | None = "GPS",
       zone: str | None = None) -> Sample:
    return Sample(T0 + timedelta(seconds=sec), HLAT + north_m / 111_195.0, HLON, acc, sensor, zone)


def run(samples: list[Sample], cp: Checkpoint | None = None, cfg: HomeConfig = CFG):
    return step(cp or Checkpoint(), samples, cfg)


class ConfigTest(unittest.TestCase):
    def test_disabled_without_coords(self) -> None:
        self.assertIsNone(HomeConfig.build(None, None))
        self.assertIsNone(HomeConfig.build(48.0, None))
        self.assertIsNone(HomeConfig.build(123.0, 17.0))

    def test_settings_empty_string_disables(self) -> None:
        from tractive_ingestor.config import Settings

        s = Settings(_env_file=None, home_lat="", home_lon="")
        self.assertIsNone(s.home_config())
        s = Settings(_env_file=None, home_lat="48.0", home_lon="17.0", home_radius_m="50")
        cfg = s.home_config()
        assert cfg is not None
        self.assertEqual((cfg.radius_m, cfg.leave_s, cfg.return_s), (50.0, 120.0, 60.0))

    def test_repr_hides_home(self) -> None:
        self.assertNotIn("48.0", repr(CFG))

    def test_haversine(self) -> None:
        self.assertAlmostEqual(haversine_m(HLAT, HLON, HLAT + 1000 / 111_195.0, HLON), 1000, delta=2)


class ClassifyTest(unittest.TestCase):
    def test_radius(self) -> None:
        self.assertTrue(classify(at(0, 50), CFG).inside)
        self.assertFalse(classify(at(0, 120), CFG).inside)

    def test_tractive_flag_wins_over_distance(self) -> None:
        c = classify(at(0, 500, sensor="KNOWN_WIFI"), CFG)
        self.assertTrue(c.inside and c.by_tractive)
        self.assertTrue(classify(at(0, 500, zone="z1"), CFG).inside)

    def test_inaccurate_fix_ignored(self) -> None:
        self.assertIsNone(classify(at(0, 500, acc=400), CFG).inside)


class StateMachineTest(unittest.TestCase):
    def test_first_sample_sets_state_without_event(self) -> None:
        cp, ev = run([at(0, 10)])
        self.assertEqual((cp.state, cp.since, ev), (HOME, T0, []))
        cp, ev = run([at(0, 900)])
        self.assertEqual((cp.state, ev), (AWAY, []))

    def test_leave_needs_debounce(self) -> None:
        cp, ev = run([at(0, 10), at(30, 200), at(90, 250)])
        self.assertEqual((cp.state, ev, cp.pending_state), (HOME, [], AWAY))
        cp, ev = run([at(150, 300)], cp)  # 150 - 30 >= 120
        self.assertEqual(cp.state, AWAY)
        self.assertEqual([(e.event_type, e.happened_at) for e in ev], [(LEFT_HOME, T0 + timedelta(seconds=30))])
        self.assertEqual(cp.since, T0 + timedelta(seconds=30))
        self.assertAlmostEqual(ev[0].distance_m, 200, delta=1)

    def test_jitter_does_not_flip(self) -> None:
        # GPS skáče přes hranici, nikdy 120 s v kuse venku
        pts = [at(0, 10)] + [at(i * 20, 100 if i % 3 else 40) for i in range(1, 30)]
        cp, ev = run(pts)
        self.assertEqual((cp.state, ev), (HOME, []))

    def test_return_needs_debounce_by_radius(self) -> None:
        cp, _ = run([at(0, 900)])
        cp, ev = run([at(10, 50), at(40, 40)], cp)
        self.assertEqual((cp.state, ev), (AWAY, []))
        cp, ev = run([at(70, 30)], cp)  # 70 - 10 >= 60
        self.assertEqual((cp.state, [e.event_type for e in ev]), (HOME, [RETURNED_HOME]))
        self.assertEqual(ev[0].happened_at, T0 + timedelta(seconds=10))

    def test_return_by_tractive_is_immediate(self) -> None:
        cp, _ = run([at(0, 900)])
        # WiFi zóna: tracker může hlásit polohu nepřesně daleko od středu – pořád doma
        cp, ev = run([at(10, 400, sensor="KNOWN_WIFI", acc=None)], cp)
        self.assertEqual((cp.state, [e.event_type for e in ev]), (HOME, [RETURNED_HOME]))

    def test_return_by_tractive_respects_debounce_when_disabled(self) -> None:
        cfg = HomeConfig(lat=HLAT, lon=HLON, trust_tractive_home=False)
        cp, _ = run([at(0, 900)], cfg=cfg)
        cp, ev = run([at(10, 400, sensor="KNOWN_WIFI")], cp, cfg)
        self.assertEqual((cp.state, ev), (AWAY, []))
        cp, ev = run([at(80, 20, sensor="KNOWN_WIFI")], cp, cfg)
        self.assertEqual(cp.state, HOME)

    def test_inaccurate_fixes_do_not_break_or_extend_runs(self) -> None:
        cp, ev = run([at(0, 10), at(30, 300), at(60, 0, acc=999), at(160, 300)])
        self.assertEqual((cp.state, [e.event_type for e in ev]), (AWAY, [LEFT_HOME]))

    def test_idempotent_replay(self) -> None:
        pts = [at(0, 10), at(30, 300), at(160, 300), at(200, 20), at(300, 20)]
        cp1, ev1 = run(pts)
        self.assertEqual([e.event_type for e in ev1], [LEFT_HOME, RETURNED_HOME])
        cp2, ev2 = run(pts, cp1)  # stejné body znovu -> nic nového
        self.assertEqual(ev2, [])
        self.assertEqual((cp2.state, cp2.since), (cp1.state, cp1.since))
        # po bodech = stejný výsledek jako najednou
        cp3, evs = Checkpoint(), []
        for p in pts:
            cp3, e = run([p], cp3)
            evs += e
        self.assertEqual(evs, ev1)
        self.assertEqual(cp3.state, cp1.state)

    def test_unknown_initial(self) -> None:
        self.assertEqual(Checkpoint().state, UNKNOWN)
        cp, ev = run([at(0, 500, acc=999)])
        self.assertEqual((cp.state, ev), (UNKNOWN, []))


# ---------------------------------------------------------------------- API (bez DB)

try:
    from fastapi.testclient import TestClient

    from tractive_api.app import create_app
    from tractive_api.config import ApiSettings

    from test_api import TRACKER, FakeStore

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False


if HAVE_FASTAPI:

    class GeoFakeStore(FakeStore):
        def __init__(self, north_m: float = 500.0, state: str = AWAY, **kw: Any) -> None:
            super().__init__(home=False, **kw)
            self.north_m = north_m
            self.state = state
            self.evals = 0

        async def default_tracker(self) -> str | None:
            return TRACKER

        async def latest_position(self, tracker_id: str) -> dict[str, Any] | None:
            return {"ts": T0, "lat": HLAT + self.north_m / 111_195.0, "lng": HLON, "accuracy_m": 10.0}

        async def geofence_evaluate(self, tracker_id: str, cfg: HomeConfig) -> dict[str, Any] | None:
            self.evals += 1
            return {"state": self.state, "since": T0, "last_ts": T0, "pending_state": None, "pending_since": None}

        async def geofence_events(self, tracker_id: str, limit: int) -> list[dict[str, Any]]:
            return [{"id": 1, "tracker_id": tracker_id, "event_type": LEFT_HOME, "happened_at": T0,
                     "distance_m": 120.0, "created_at": T0}][:limit]


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx nejsou nainstalované")
class GeofenceApiTest(unittest.TestCase):
    def make(self, store: Any, **kw: Any) -> TestClient:
        settings = ApiSettings(_env_file=None, database_url=None, api_web_dist=Path("/nonexistent"), **kw)
        return TestClient(create_app(settings, store))

    def test_disabled_without_home(self) -> None:
        store = GeoFakeStore()
        with self.make(store) as c:
            body = c.get("/api/geofence/state").json()
        self.assertEqual((body["enabled"], body["state"], body["home"]), (False, "unknown", None))
        self.assertEqual(store.evals, 0)

    def test_state_escaped(self) -> None:
        with self.make(GeoFakeStore(500), home_lat=HLAT, home_lon=HLON) as c:
            body = c.get("/api/geofence/state").json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["state"], AWAY)
        self.assertTrue(body["escaped"])
        self.assertAlmostEqual(body["distance_m"], 500, delta=2)
        self.assertEqual(body["home_radius_m"], 80)
        self.assertIn("since", body)
        self.assertFalse(body["in_home_zone"])

    def test_state_away_not_escaped(self) -> None:
        with self.make(GeoFakeStore(150), home_lat=HLAT, home_lon=HLON) as c:
            self.assertFalse(c.get("/api/geofence/state").json()["escaped"])

    def test_events_and_validation(self) -> None:
        with self.make(GeoFakeStore(), home_lat=HLAT, home_lon=HLON) as c:
            ev = c.get("/api/geofence/events?limit=5").json()
            self.assertEqual(ev[0]["event_type"], LEFT_HOME)
            self.assertEqual(c.get("/api/geofence/events?limit=0").status_code, 422)
            self.assertEqual(c.get("/api/geofence/state?tracker_id=NOPE").status_code, 404)

    def test_gated_by_pin(self) -> None:
        with self.make(GeoFakeStore(), home_lat=HLAT, home_lon=HLON, app_pin="1234",
                       app_session_secret="ab" * 16) as c:
            self.assertEqual(c.get("/api/geofence/state").status_code, 401)
            self.assertEqual(c.get("/api/geofence/events").status_code, 401)
            c.post("/api/login", json={"pin": "1234"})
            # TestClient je http:// a cookie je Secure -> předat ručně
            from tractive_api.auth import COOKIE_NAME, make_token

            c.cookies.set(COOKIE_NAME, make_token(bytes.fromhex("ab" * 16)))
            self.assertEqual(c.get("/api/geofence/state").status_code, 200)


# ---------------------------------------------------------------------- volitelně proti Postgresu


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL není nastavené")
class PgGeofenceTest(unittest.IsolatedAsyncioTestCase):
    TRK = "TEST-GEO"

    async def asyncSetUp(self) -> None:
        from tractive_ingestor.storage import PgStorage

        self.storage = PgStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.connect()
        await self.storage.apply_schema()
        await self.clean()

    async def clean(self) -> None:
        for tbl in ("positions", "geofence_events", "geofence_state"):
            await self.storage.pool.execute(f"DELETE FROM {tbl} WHERE tracker_id = $1", self.TRK)

    async def asyncTearDown(self) -> None:
        await self.clean()
        await self.storage.close()

    async def add(self, *pts: Sample) -> None:
        for p in pts:
            await self.storage.pool.execute(
                """INSERT INTO positions (tracker_id, ts, geom, accuracy_m, sensor_used, power_saving_zone_id, origin)
                   VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6, $7, 'test')""",
                self.TRK, p.ts, p.lon, p.lat, p.accuracy_m, p.sensor_used, p.zone_id,
            )

    async def events(self) -> list[Any]:
        return await self.storage.pool.fetch(
            "SELECT event_type, happened_at FROM geofence_events WHERE tracker_id = $1 ORDER BY happened_at", self.TRK)

    async def test_incremental_and_idempotent(self) -> None:
        from tractive_ingestor.home_geofence import evaluate_pool

        now = T0 + timedelta(minutes=30)
        cfg = CFG
        await self.add(at(0, 10), at(30, 300), at(90, 300))
        async with self.storage.pool.acquire() as conn, conn.transaction():
            from tractive_ingestor.home_geofence import evaluate

            cp, ins = await evaluate(conn, self.TRK, cfg, now=now)
        self.assertEqual((cp.state, ins, cp.pending_state), (HOME, [], AWAY))
        await self.add(at(160, 400))
        cp, ins = await evaluate_pool(self.storage.pool, self.TRK, cfg)
        # bez `now` je bootstrap okno od teď – checkpoint už existuje, takže se nepoužije
        self.assertEqual((cp.state, [t.event_type for t in ins]), (AWAY, [LEFT_HOME]))
        # opakované + souběžné vyhodnocení nic nezdvojí
        res = await asyncio.gather(*(evaluate_pool(self.storage.pool, self.TRK, cfg) for _ in range(5)))
        self.assertTrue(all(r[1] == [] for r in res))
        self.assertEqual([r["event_type"] for r in await self.events()], [LEFT_HOME])
        # návrat přes WiFi zónu
        await self.add(at(600, 30, sensor="KNOWN_WIFI"))
        cp, ins = await evaluate_pool(self.storage.pool, self.TRK, cfg)
        self.assertEqual(cp.state, HOME)
        self.assertEqual([r["event_type"] for r in await self.events()], [LEFT_HOME, RETURNED_HOME])

    async def test_bootstrap_from_last_event_after_state_loss(self) -> None:
        from tractive_ingestor.home_geofence import evaluate_pool

        await self.add(at(0, 10), at(30, 300), at(160, 400))
        async with self.storage.pool.acquire() as conn, conn.transaction():
            from tractive_ingestor.home_geofence import evaluate

            await evaluate(conn, self.TRK, CFG, now=T0 + timedelta(minutes=5))
        # checkpoint ztracen -> obnoví se z poslední události, žádný duplikát
        await self.storage.pool.execute("DELETE FROM geofence_state WHERE tracker_id = $1", self.TRK)
        cp, ins = await evaluate_pool(self.storage.pool, self.TRK, CFG)
        self.assertEqual((cp.state, ins), (AWAY, []))
        self.assertEqual(len(await self.events()), 1)

    async def test_api_store_readonly_pool_can_evaluate(self) -> None:
        from tractive_api.store import PgApiStore

        await self.add(at(0, 10), at(30, 300), at(160, 400))
        store = PgApiStore(os.environ["TEST_DATABASE_URL"])
        try:
            # bootstrap okno je "posledních 24 h od teď" -> body z T0 by vypadly; nasadit checkpoint
            await self.storage.pool.execute(
                "INSERT INTO geofence_state (tracker_id, state, since, last_ts) VALUES ($1, 'home', $2, $2)",
                self.TRK, T0 - timedelta(seconds=1))
            cp = await store.geofence_evaluate(self.TRK, CFG)
            assert cp is not None
            self.assertEqual(cp["state"], AWAY)
            ev = await store.geofence_events(self.TRK, 10)
            self.assertEqual([e["event_type"] for e in ev], [LEFT_HOME])
        finally:
            await store.close()


if __name__ == "__main__":
    unittest.main()
