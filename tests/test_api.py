"""API (src/tractive_api): routy, validace povelu, chybové stavy – bez DB.

In-memory `FakeStore` místo `PgApiStore`; potřebuje fastapi + httpx
(`pip install -e '.[dev]'`), jinak se testy přeskočí. SQL v `PgApiStore` se navíc ověří
proti skutečnému Postgresu, pokud je nastavené TEST_DATABASE_URL (jako v test_commands.py).

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from fastapi.testclient import TestClient

    from tractive_api.app import create_app
    from tractive_api.config import ApiSettings
    from tractive_api.store import StoreUnavailable

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False

from tractive_ingestor.client import Command

T0 = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
TRACKER = "TESTTRK1"


class FakeStore:
    def __init__(self, *, home: bool | None = True, fail: bool = False) -> None:
        self.home = home
        self.fail = fail
        self.commands: list[dict[str, Any]] = []
        self.positions_calls: list[tuple[str, datetime, int]] = []

    async def connect(self) -> None:
        if self.fail:
            raise StoreUnavailable("down")

    async def close(self) -> None: ...

    async def ping(self) -> bool:
        await self.connect()
        return True

    async def tracker_exists(self, tracker_id: str) -> bool:
        await self.connect()
        return tracker_id == TRACKER

    async def list_pets(self) -> list[dict[str, Any]]:
        return [{"pet_id": "pet1", "tracker_id": TRACKER, "name": "Mája", "daily_active_minutes_goal": 60}]

    async def list_trackers(self) -> list[dict[str, Any]]:
        return [{"tracker_id": TRACKER, "battery_pct": 63, "state": "OPERATIONAL", "sensor_used": "KNOWN_WIFI",
                 "last_position_ts": T0}]

    async def latest(self, tracker_id: str) -> dict[str, Any]:
        return {"tracker_id": tracker_id,
                "position": {"ts": T0, "lat": 50.0, "lng": 14.0, "accuracy_m": 20.0, "sensor_used": "KNOWN_WIFI"},
                "hw": {"ts": T0, "battery_pct": 63, "tracker_state": "OPERATIONAL", "power_saving": True},
                "tracker": None}

    async def positions(self, tracker_id: str, since: datetime, limit: int) -> list[dict[str, Any]]:
        self.positions_calls.append((tracker_id, since, limit))
        return [{"ts": T0, "lat": 50.0, "lng": 14.0, "accuracy_m": 10.0, "sensor_used": "GPS"}]

    async def health(self, tracker_id: str, days: int) -> dict[str, Any]:
        return {"tracker_id": tracker_id, "pet_id": "pet1", "latest": {"minutes_active": 30, "minutes_goal": 60},
                "days": []}

    async def list_commands(self, tracker_id: str, limit: int) -> list[dict[str, Any]]:
        return self.commands[:limit]

    async def enqueue_command(self, tracker_id: str, command: Command, action: str) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "tracker_id": tracker_id, "command": command.value, "action": action,
               "status": "queued", "attempts": 0, "created_at": T0}
        self.commands.insert(0, row)
        return dict(row)

    async def in_home_zone(self, tracker_id: str) -> bool | None:
        return self.home


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx nejsou nainstalované")
class ApiRoutesTest(unittest.TestCase):
    def make(self, store: FakeStore | None = None, **kw: Any) -> tuple[TestClient, FakeStore]:
        store = store or FakeStore()
        settings = ApiSettings(_env_file=None, database_url=None, api_web_dist=Path("/nonexistent"), **kw)
        return TestClient(create_app(settings, store)), store

    def test_routes_wired(self) -> None:
        client, _ = self.make()
        paths = {r.path for r in client.app.routes}  # type: ignore[attr-defined]
        for p in ("/api/health", "/api/ready", "/api/pets", "/api/trackers",
                  "/api/trackers/{tracker_id}/latest", "/api/trackers/{tracker_id}/positions",
                  "/api/trackers/{tracker_id}/health", "/api/trackers/{tracker_id}/commands"):
            self.assertIn(p, paths)

    def test_read_endpoints(self) -> None:
        with self.make()[0] as client:
            self.assertEqual(client.get("/api/health").json()["status"], "ok")
            self.assertEqual(client.get("/api/pets").json()[0]["name"], "Mája")
            self.assertEqual(client.get("/api/trackers").json()[0]["battery_pct"], 63)
            latest = client.get(f"/api/trackers/{TRACKER}/latest").json()
            self.assertEqual(latest["position"]["sensor_used"], "KNOWN_WIFI")
            self.assertTrue(latest["in_home_zone"])
            self.assertEqual(client.get(f"/api/trackers/{TRACKER}/health").json()["latest"]["minutes_goal"], 60)

    def test_api_never_cacheable(self) -> None:
        with self.make()[0] as client:
            for path in ("/api/pets", f"/api/trackers/{TRACKER}/latest", "/api/health", "/api/nope"):
                self.assertEqual(client.get(path).headers.get("cache-control"), "no-store", path)

    def test_unknown_tracker_404(self) -> None:
        with self.make()[0] as client:
            self.assertEqual(client.get("/api/trackers/NOPE/latest").status_code, 404)
            self.assertEqual(client.post("/api/trackers/NOPE/commands", json={"command": "live"}).status_code, 404)
            self.assertEqual(client.get("/api/trackers/bad%20id!/latest").status_code, 422)

    def test_positions_default_window_and_cap(self) -> None:
        client, store = self.make(api_positions_max_limit=100)
        with client:
            r = client.get(f"/api/trackers/{TRACKER}/positions", params={"limit": 99999})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["count"], 1)
            _, since, limit = store.positions_calls[-1]
            self.assertEqual(limit, 100)
            self.assertLess(abs((datetime.now(UTC) - timedelta(hours=24) - since).total_seconds()), 60)
            client.get(f"/api/trackers/{TRACKER}/positions", params={"since": "2026-10-18T08:00:00"})
            self.assertEqual(store.positions_calls[-1][1], datetime(2026, 10, 18, 8, 0, tzinfo=UTC))

    def test_post_command_valid(self) -> None:
        client, store = self.make()
        with client:
            r = client.post(f"/api/trackers/{TRACKER}/commands", json={"command": "Light", "action": "OFF"})
            self.assertEqual(r.status_code, 201, r.text)
            body = r.json()
            self.assertEqual(body["status"], "queued")
            self.assertEqual(body["command"], Command.LED.value)
            self.assertEqual(body["action"], "off")
            self.assertTrue(body["in_home_zone"])
            self.assertIn("doma", body["note"])
            # default action = on
            r = client.post(f"/api/trackers/{TRACKER}/commands", json={"command": "buzzer"})
            self.assertEqual(r.json()["action"], "on")
            self.assertEqual(len(store.commands), 2)
            self.assertEqual(len(client.get(f"/api/trackers/{TRACKER}/commands").json()), 2)

    def test_post_command_invalid(self) -> None:
        client, store = self.make()
        with client:
            for body in ({"command": "explode"}, {"command": "live", "action": "maybe"}, {}):
                r = client.post(f"/api/trackers/{TRACKER}/commands", json=body)
                self.assertEqual(r.status_code, 422, body)
            self.assertEqual(store.commands, [])

    def test_db_unavailable_503(self) -> None:
        with self.make(FakeStore(fail=True))[0] as client:
            self.assertEqual(client.get("/api/health").status_code, 200)  # liveness bez DB
            r = client.get("/api/ready")
            self.assertEqual(r.status_code, 503)
            self.assertEqual(client.get(f"/api/trackers/{TRACKER}/latest").status_code, 503)

    def test_serves_web_dist(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "index.html").write_text("<!doctype html><title>Mája</title>", encoding="utf-8")
            (Path(d) / "assets").mkdir()
            (Path(d) / "assets" / "app-123.js").write_text("console.log(1)", encoding="utf-8")
            settings = ApiSettings(_env_file=None, api_web_dist=Path(d))
            with TestClient(create_app(settings, FakeStore())) as client:
                r = client.get("/")
                self.assertEqual(r.status_code, 200)
                self.assertIn("Mája", r.text)
                self.assertEqual(r.headers["cache-control"], "no-cache")
                self.assertIn("immutable", client.get("/assets/app-123.js").headers["cache-control"])
                self.assertEqual(client.get("/api/pets").status_code, 200)

    def test_cors_dev_origin(self) -> None:
        with self.make()[0] as client:
            r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
            self.assertEqual(r.headers.get("access-control-allow-origin"), "http://localhost:5173")
            r = client.get("/api/health", headers={"Origin": "http://evil.example"})
            self.assertIsNone(r.headers.get("access-control-allow-origin"))


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx nejsou nainstalované")
class ApiAuthTest(unittest.TestCase):
    SECRET = "ab" * 32

    def make(self, pin: str | None = "4321", secret: str | None = SECRET, web: Path | None = None) -> TestClient:
        settings = ApiSettings(_env_file=None, database_url=None, api_web_dist=web or Path("/nonexistent"),
                               app_pin=pin, app_session_secret=secret)
        # https base_url: cookie má Secure, přes http by ji httpx neposlal zpět
        return TestClient(create_app(settings, FakeStore()), base_url="https://testserver")

    def test_disabled_without_pin(self) -> None:
        with self.make(pin=None) as c:
            self.assertEqual(c.get("/api/pets").status_code, 200)
            self.assertEqual(c.get("/api/me").json()["authed"], True)

    def test_gate_login_logout(self) -> None:
        with self.make() as c:
            for path in ("/api/health", "/api/me"):
                self.assertEqual(c.get(path).status_code, 200, path)
            self.assertFalse(c.get("/api/me").json()["authed"])
            for path in ("/api/pets", "/api/trackers", f"/api/trackers/{TRACKER}/latest",
                         f"/api/trackers/{TRACKER}/positions", f"/api/trackers/{TRACKER}/commands",
                         "/api/commands/types", "/api/docs", "/api/openapi.json", "/api/nonexistent"):
                self.assertEqual(c.get(path).status_code, 401, path)
            r = c.post(f"/api/trackers/{TRACKER}/commands", json={"command": "buzzer"})
            self.assertEqual(r.status_code, 401)

            r = c.post("/api/login", json={"pin": "0000"})
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.json(), {"ok": False})
            r = c.post("/api/login", json={"pin": "4321"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), {"ok": True})
            sc = r.headers["set-cookie"].lower()
            for attr in ("maja_session=", "httponly", "secure", "samesite=lax", "path=/", "max-age=7776000"):
                self.assertIn(attr, sc)
            self.assertTrue(c.get("/api/me").json()["authed"])
            self.assertEqual(c.get("/api/pets").status_code, 200)
            self.assertEqual(c.post(f"/api/trackers/{TRACKER}/commands", json={"command": "buzzer"}).status_code, 201)

            self.assertEqual(c.post("/api/logout").json(), {"ok": True})
            self.assertFalse(c.get("/api/me").json()["authed"])
            self.assertEqual(c.get("/api/pets").status_code, 401)

    def test_forged_and_expired_cookie(self) -> None:
        from tractive_api.auth import SESSION_MAX_AGE, make_token, verify_token

        key = bytes.fromhex(self.SECRET)
        self.assertTrue(verify_token(key, make_token(key)))
        self.assertFalse(verify_token(key, make_token(b"other")))
        self.assertFalse(verify_token(key, make_token(key, now=1_000_000), now=1_000_000 + SESSION_MAX_AGE))
        self.assertFalse(verify_token(key, "garbage"))
        with self.make() as c:
            c.cookies.set("maja_session", make_token(b"wrong-secret"))
            self.assertEqual(c.get("/api/pets").status_code, 401)
            c.cookies.set("maja_session", make_token(key))
            self.assertEqual(c.get("/api/pets").status_code, 200)

    def test_passphrase_login(self) -> None:
        phrase = "kopr-Vlak7-modry-Tulipan-42"
        with self.make(pin=phrase) as c:
            # celý řetězec, rozlišuje velikost písmen, žádné číselné/délkové předpoklady
            for wrong in ("kopr-vlak7-modry-tulipan-42", phrase[:-1], phrase + "x", "", "4321"):
                self.assertEqual(c.post("/api/login", json={"pin": wrong},
                                        headers={"CF-Connecting-IP": "198.51.100.1"}).status_code, 401, wrong)
            self.assertEqual(c.post("/api/login", json={"pin": phrase}).status_code, 200)
            self.assertEqual(c.post("/api/logout").status_code, 200)
            self.assertEqual(c.post("/api/login", json={"password": phrase}).status_code, 200)
            self.assertTrue(c.get("/api/me").json()["authed"])
            self.assertEqual(c.post("/api/login", json={}).status_code, 401)
            self.assertEqual(c.post("/api/login", json={"pin": "x" * 300}).status_code, 422)

    def test_rate_limit(self) -> None:
        with self.make() as c:
            for _ in range(5):
                self.assertEqual(c.post("/api/login", json={"pin": "1111"}).status_code, 401)
            self.assertEqual(c.post("/api/login", json={"pin": "4321"}).status_code, 429)
            # jiná IP (za Cloudflare) není blokovaná
            r = c.post("/api/login", json={"pin": "4321"}, headers={"CF-Connecting-IP": "203.0.113.9"})
            self.assertEqual(r.status_code, 200)

    def test_static_shell_open(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "index.html").write_text("<!doctype html><title>Mája</title>", encoding="utf-8")
            with self.make(web=Path(d)) as c:
                self.assertEqual(c.get("/").status_code, 200)
                self.assertEqual(c.get("/api/pets").status_code, 401)


@unittest.skipUnless(HAVE_FASTAPI and os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL není nastavené")
class PgApiStoreTest(unittest.IsolatedAsyncioTestCase):
    TRK = "TEST-API-TRK"

    async def asyncSetUp(self) -> None:
        from tractive_api.store import PgApiStore
        from tractive_ingestor.storage import PgStorage

        self.storage = PgStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.connect()
        await self.storage.apply_schema()
        await self._cleanup()
        await self.storage.pool.execute(
            "INSERT INTO trackers (tracker_id, state, state_reason) VALUES ($1, 'OPERATIONAL', 'POWER_SAVING')", self.TRK
        )
        await self.storage.pool.execute(
            "INSERT INTO pets (pet_id, tracker_id, name, daily_active_minutes_goal) VALUES ('test-api-pet', $1, 'Mája', 60)",
            self.TRK,
        )
        await self.storage.pool.execute(
            """
            INSERT INTO positions (tracker_id, ts, geom, accuracy_m, sensor_used, origin)
            SELECT $1, now() - make_interval(mins => g), ST_SetSRID(ST_MakePoint(14.0 + g / 1000.0, 50.0), 4326),
                   10, CASE WHEN g = 1 THEN 'KNOWN_WIFI' ELSE 'GPS' END, 'channel'
            FROM generate_series(1, 5) g
            """,
            self.TRK,
        )
        await self.storage.pool.execute(
            "INSERT INTO hw_reports (tracker_id, ts, battery_pct, tracker_state, power_saving, origin) "
            "VALUES ($1, now() - interval '30 min', 63, 'OPERATIONAL', true, 'pos_report')",
            self.TRK,
        )
        await self.storage.pool.execute(
            "INSERT INTO health_daily (pet_id, day, minutes_active, minutes_goal, sleep_night_min) "
            "VALUES ('test-api-pet', current_date, 34, NULL, 500)"
        )
        self.store = PgApiStore(os.environ["TEST_DATABASE_URL"])

    async def _cleanup(self) -> None:
        for sql in ("DELETE FROM commands WHERE tracker_id = $1", "DELETE FROM positions WHERE tracker_id = $1",
                    "DELETE FROM hw_reports WHERE tracker_id = $1", "DELETE FROM pets WHERE tracker_id = $1",
                    "DELETE FROM trackers WHERE tracker_id = $1"):
            await self.storage.pool.execute(sql, self.TRK)
        await self.storage.pool.execute("DELETE FROM health_daily WHERE pet_id = 'test-api-pet'")

    async def asyncTearDown(self) -> None:
        await self.store.close()
        await self._cleanup()
        await self.storage.close()

    async def test_queries(self) -> None:
        s = self.store
        self.assertTrue(await s.ping())
        self.assertTrue(await s.tracker_exists(self.TRK))
        self.assertFalse(await s.tracker_exists("NOPE-NOPE"))
        tr = next(t for t in await s.list_trackers() if t["tracker_id"] == self.TRK)
        self.assertEqual((tr["battery_pct"], tr["pet_name"], tr["sensor_used"]), (63, "Mája", "KNOWN_WIFI"))
        latest = await s.latest(self.TRK)
        self.assertAlmostEqual(latest["position"]["lng"], 14.001)
        self.assertAlmostEqual(latest["position"]["lat"], 50.0)
        self.assertTrue(await s.in_home_zone(self.TRK))
        pts = await s.positions(self.TRK, datetime.now(UTC) - timedelta(hours=1), 3)
        self.assertEqual(len(pts), 3)  # nejnovější 3, chronologicky
        self.assertLess(pts[0]["ts"], pts[-1]["ts"])
        self.assertEqual(pts[-1]["sensor_used"], "KNOWN_WIFI")
        h = await s.health(self.TRK, 7)
        self.assertEqual((h["latest"]["minutes_active"], h["latest"]["minutes_goal"]), (34, 60))  # cíl z pets

    async def test_enqueue_is_only_write(self) -> None:
        row = await self.store.enqueue_command(self.TRK, Command.BUZZER, "on")
        self.assertEqual((row["status"], row["command"], row["command_type"]), ("queued", "buzzer", "buzzer_control"))
        self.assertEqual((await self.store.list_commands(self.TRK, 5))[0]["id"], row["id"])
        await self.store.connect()
        import asyncpg

        with self.assertRaises(asyncpg.ReadOnlySQLTransactionError):
            await self.store._pool.execute("DELETE FROM commands WHERE tracker_id = $1", self.TRK)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
