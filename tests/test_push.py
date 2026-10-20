"""Web Push (src/tractive_ingestor/push.py + /api/push/*).

Idempotence upozornění a úklid odběrů se ověřují proti Postgresu (TEST_DATABASE_URL,
jinak skip); validace, texty, šifrování/VAPID a API routy běží bez DB.

    TEST_DATABASE_URL=postgresql://… python -m pytest tests/test_push.py -v
"""

from __future__ import annotations

import base64
import json
import os
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from tractive_ingestor.home_geofence import HomeConfig
from tractive_ingestor.push import (
    InvalidSubscription,
    PushConfig,
    WebPushSender,
    fmt_duration,
    generate_vapid_keys,
    text_returned_home,
    validate_subscription,
)

TRK = "TEST-PUSH-TRK"
APPLE = "https://web.push.apple.com/QGx-test-"
P256 = "B" + "A" * 86  # tvar klíče (87 znaků base64url), obsah nehraje roli
AUTH = "c2VjcmV0c2VjcmV0MTI"
HOME = HomeConfig(lat=48.0, lon=17.0, radius_m=80, escape_m=300)  # smyšlený domov


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class FakeSender:
    """Vrací naprogramované statusy podle endpointu (default 201) a pamatuje si payloady."""

    def __init__(self, statuses: dict[str, int] | None = None) -> None:
        self.statuses = statuses or {}
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, sub: Mapping[str, Any], payload: str, *, ttl: int = 3600, urgency: str = "normal") -> int:
        self.sent.append((sub["endpoint"], json.loads(payload)))
        return self.statuses.get(sub["endpoint"], 201)

    def titles(self) -> list[str]:
        return [p["title"] for _, p in self.sent]


class PureTest(unittest.TestCase):
    def test_validate(self) -> None:
        validate_subscription(APPLE + "1", P256, AUTH)
        validate_subscription("https://fcm.googleapis.com/fcm/send/abc", P256, AUTH)
        validate_subscription("https://updates.push.services.mozilla.com/wpush/v2/x", P256, AUTH)
        for ep in ("http://web.push.apple.com/x", "https://evil.example.com/x", "https://push.apple.com.evil.io/x",
                   "https://127.0.0.1/x", "ftp://fcm.googleapis.com/x"):
            with self.assertRaises(InvalidSubscription, msg=ep):
                validate_subscription(ep, P256, AUTH)
        with self.assertRaises(InvalidSubscription):
            validate_subscription(APPLE, "short", AUTH)
        with self.assertRaises(InvalidSubscription):
            validate_subscription(APPLE, P256, "bad auth!")

    def test_config(self) -> None:
        self.assertIsNone(PushConfig.build(None, None, None))
        self.assertIsNone(PushConfig.build("pub", "", None))
        self.assertIsNone(PushConfig.build("pub", "priv", "nobody@example.com"))
        cfg = PushConfig.build(" pub ", "priv", "")
        assert cfg is not None
        self.assertEqual((cfg.public_key, cfg.subject), ("pub", "mailto:you@example.com"))
        self.assertNotIn("priv", repr(cfg))

    def test_settings(self) -> None:
        from tractive_ingestor.config import Settings

        self.assertIsNone(Settings(_env_file=None).push_config())
        pub, priv = generate_vapid_keys()
        s = Settings(_env_file=None, vapid_public_key=pub, vapid_private_key=priv)
        self.assertIsNotNone(s.push_config())

    def test_texts(self) -> None:
        self.assertEqual(fmt_duration(timedelta(minutes=72)), "1 h 12 min")
        self.assertEqual(fmt_duration(timedelta(minutes=5, seconds=59)), "5 min")
        self.assertEqual(fmt_duration(timedelta(hours=2)), "2 h")
        title, body = text_returned_home("Mája", timedelta(minutes=45))
        self.assertEqual(title, "Mája je zpátky doma 🏠")
        self.assertIn("45 min", body)

    def test_gen_vapid_cli(self) -> None:
        import contextlib
        import io

        from tractive_ingestor.__main__ import main

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["gen-vapid"]), 0)
        lines = dict(line.split("=", 1) for line in out.getvalue().split())
        self.assertEqual(len(base64.urlsafe_b64decode(lines["VAPID_PUBLIC_KEY"] + "==")), 65)
        self.assertEqual(len(base64.urlsafe_b64decode(lines["VAPID_PRIVATE_KEY"] + "==")), 32)
        self.assertEqual(lines["VAPID_SUBJECT"], "mailto:you@example.com")

    def test_webpush_sender_encrypts_and_signs(self) -> None:
        """Skutečný pywebpush: aes128gcm tělo + VAPID JWT; HTTP volání je podvržené."""
        import asyncio

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        pub, priv = generate_vapid_keys()
        ua_key = ec.generate_private_key(ec.SECP256R1())  # „prohlížeč“
        p256dh = b64u(ua_key.public_key().public_bytes(serialization.Encoding.X962,
                                                       serialization.PublicFormat.UncompressedPoint))
        sub = {"endpoint": APPLE + "enc", "p256dh": p256dh, "auth": b64u(os.urandom(16))}
        calls: list[dict[str, Any]] = []

        def fake_post(url: str, data: bytes, headers: dict[str, str], timeout: Any = None) -> Any:
            calls.append({"url": url, "data": data, "headers": headers})
            return mock.Mock(status_code=201, reason="Created", text="")

        sender = WebPushSender(PushConfig(pub, priv, "mailto:you@example.com"))
        with mock.patch("requests.post", side_effect=fake_post):
            status = asyncio.run(sender.send(sub, json.dumps({"title": "x"}), ttl=60, urgency="high"))
        self.assertEqual(status, 201)
        h = calls[0]["headers"]
        self.assertEqual(calls[0]["url"], sub["endpoint"])
        self.assertEqual(h.get("content-encoding"), "aes128gcm")
        self.assertTrue(h["Authorization"].startswith("vapid t="))
        self.assertIn(f"k={pub}", h["Authorization"])
        self.assertEqual(h.get("Urgency"), "high")
        self.assertNotIn(b'"title"', calls[0]["data"])  # tělo je zašifrované
        import http_ece  # prohlížeč (s privátním klíčem odběru) to rozšifruje zpátky

        plain = http_ece.decrypt(calls[0]["data"], private_key=ua_key, auth_secret=unb64u(sub["auth"]),
                                 version="aes128gcm")
        self.assertEqual(json.loads(plain), {"title": "x"})

        with mock.patch("requests.post", return_value=mock.Mock(status_code=410, reason="Gone", text="")):
            self.assertEqual(asyncio.run(sender.send(sub, "{}", ttl=60, urgency="normal")), 410)


# ---------------------------------------------------------------------- Postgres


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL není nastavené")
class PgPushTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from tractive_ingestor.storage import PgStorage

        self.storage = PgStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.connect()
        await self.storage.apply_schema()
        self.pool = self.storage.pool
        await self._cleanup()
        await self.pool.execute("INSERT INTO trackers (tracker_id) VALUES ($1)", TRK)
        await self.pool.execute("INSERT INTO pets (pet_id, tracker_id, name) VALUES ('test-push-pet', $1, 'Mája')", TRK)
        # testy počítají jen se svými odběry
        self._saved = await self.pool.fetch("DELETE FROM push_subscriptions RETURNING endpoint, p256dh, auth")
        await self.add_sub("a")
        await self.add_sub("b")

    async def _cleanup(self) -> None:
        for sql in ("DELETE FROM geofence_events WHERE tracker_id = $1", "DELETE FROM geofence_state WHERE tracker_id = $1",
                    "DELETE FROM notify_state WHERE tracker_id = $1", "DELETE FROM hw_reports WHERE tracker_id = $1",
                    "DELETE FROM pets WHERE tracker_id = $1", "DELETE FROM trackers WHERE tracker_id = $1"):
            await self.pool.execute(sql, TRK)
        await self.pool.execute("DELETE FROM push_subscriptions WHERE endpoint LIKE $1", APPLE + "%")

    async def asyncTearDown(self) -> None:
        await self._cleanup()
        for r in self._saved:
            await self.pool.execute("INSERT INTO push_subscriptions (endpoint, p256dh, auth) VALUES ($1,$2,$3) "
                                    "ON CONFLICT DO NOTHING", r["endpoint"], r["p256dh"], r["auth"])
        await self.storage.close()

    async def add_sub(self, name: str) -> None:
        from tractive_ingestor.push import upsert_subscription

        async with self.pool.acquire() as conn:
            await upsert_subscription(conn, APPLE + name, P256, AUTH, "test-UA")

    def notifier(self, sender: FakeSender, home: HomeConfig | None = HOME):
        from tractive_ingestor.push import Notifier

        return Notifier(self.pool, sender, home)

    async def add_event(self, typ: str, happened: datetime, *, created_ago_s: float = 0) -> None:
        await self.pool.execute(
            "INSERT INTO geofence_events (tracker_id, event_type, happened_at, distance_m, created_at) "
            "VALUES ($1, $2, $3, 120, now() - make_interval(secs => $4))", TRK, typ, happened, created_ago_s)

    async def test_geofence_events_once_even_after_restart(self) -> None:
        now = datetime.now(UTC)
        await self.add_event("left_home", now - timedelta(minutes=47))
        s1 = FakeSender()
        self.assertEqual(await self.notifier(s1).run_once(), 1)
        self.assertEqual(s1.titles(), ["Mája odešla z domova"] * 2)  # oba odběry
        self.assertEqual(s1.sent[0][1]["tag"], "geofence")
        self.assertEqual(await self.notifier(s1).run_once(), 0)  # druhé kolo nic
        s2 = FakeSender()  # „restart“ = nový notifier i sender
        self.assertEqual(await self.notifier(s2).run_once(), 0)
        self.assertEqual(s2.sent, [])

        await self.add_event("returned_home", now - timedelta(minutes=2))
        self.assertEqual(await self.notifier(s2).run_once(), 1)
        self.assertEqual(s2.titles()[0], "Mája je zpátky doma 🏠")
        self.assertIn("45 min", s2.sent[0][1]["body"])
        self.assertIsNone(await self.pool.fetchval(
            "SELECT 1 FROM geofence_events WHERE tracker_id = $1 AND notified_at IS NULL", TRK))

    async def test_stale_events_marked_not_sent(self) -> None:
        await self.add_event("left_home", datetime.now(UTC) - timedelta(hours=3), created_ago_s=3 * 3600)
        s = FakeSender()
        self.assertEqual(await self.notifier(s).run_once(), 0)
        self.assertEqual(s.sent, [])
        self.assertIsNotNone(await self.pool.fetchval(
            "SELECT notified_at FROM geofence_events WHERE tracker_id = $1", TRK))

    async def test_schema_backfills_notified_at_once(self) -> None:
        """První přidání sloupce označí staré události jako odbavené (žádná lavina po nasazení)."""
        await self.pool.execute("ALTER TABLE geofence_events DROP COLUMN notified_at")
        await self.pool.execute(
            "INSERT INTO geofence_events (tracker_id, event_type, happened_at) VALUES ($1, 'left_home', now())", TRK)
        await self.storage.apply_schema()
        self.assertIsNotNone(await self.pool.fetchval(
            "SELECT notified_at FROM geofence_events WHERE tracker_id = $1", TRK))
        await self.storage.apply_schema()  # podruhé se už nic nepřepisuje
        await self.add_event("returned_home", datetime.now(UTC))
        self.assertEqual(await self.pool.fetchval(
            "SELECT count(*) FROM geofence_events WHERE tracker_id = $1 AND notified_at IS NULL", TRK), 1)

    async def set_state(self, state: str, since: datetime, dist: float) -> None:
        await self.pool.execute(
            """INSERT INTO geofence_state (tracker_id, state, since, last_distance_m) VALUES ($1, $2, $3, $4)
               ON CONFLICT (tracker_id) DO UPDATE SET state = $2, since = $3, last_distance_m = $4""",
            TRK, state, since, dist)

    async def test_escape_once_per_episode(self) -> None:
        ep1 = datetime.now(UTC) - timedelta(minutes=10)
        s = FakeSender()
        await self.set_state("away", ep1, 150)  # venku, ale blízko
        self.assertEqual(await self.notifier(s).escape(TRK), 0)
        await self.set_state("away", ep1, 412.4)
        self.assertEqual(await self.notifier(s).escape(TRK), 1)
        self.assertEqual(s.titles()[0], "🚨 Mája utekla! 412 m od domova")
        self.assertEqual(s.sent[0][1]["tag"], "escape")
        await self.set_state("away", ep1, 900)  # pořád stejná epizoda
        self.assertEqual(await self.notifier(FakeSender()).escape(TRK), 0)
        await self.set_state("home", ep1 + timedelta(minutes=30), 5)
        self.assertEqual(await self.notifier(s).escape(TRK), 0)
        await self.set_state("away", ep1 + timedelta(hours=2), 500)  # nová epizoda
        s2 = FakeSender()
        self.assertEqual(await self.notifier(s2).escape(TRK), 1)
        # bez geofence (HOME_LAT nenastavené) se escape vůbec nekontroluje
        self.assertEqual(await self.notifier(FakeSender(), home=None).run_once(), 0)

    async def battery(self, pct: int, minutes_ago: int, charging: bool = False) -> None:
        await self.pool.execute(
            "INSERT INTO hw_reports (tracker_id, ts, battery_pct, charging, origin) "
            "VALUES ($1, now() - make_interval(mins => $3), $2, $4, 'test')", TRK, pct, minutes_ago, charging)

    async def test_battery_low_once_with_hysteresis(self) -> None:
        s = FakeSender()
        n = self.notifier(s)
        await self.battery(40, 60)
        self.assertEqual(await n.battery(TRK), 0)
        await self.battery(19, 50)
        self.assertEqual(await n.battery(TRK), 1)
        self.assertIn("19 %", s.titles()[0])
        await self.battery(12, 40)
        self.assertEqual(await self.notifier(FakeSender()).battery(TRK), 0)  # i po „restartu“
        await self.battery(25, 30)  # nad 20, ale pod 30 -> ještě se neresetuje
        await n.battery(TRK)
        await self.battery(18, 20)
        self.assertEqual(await n.battery(TRK), 0)
        await self.battery(31, 10)  # reset
        self.assertEqual(await n.battery(TRK), 0)
        await self.battery(17, 5, charging=True)  # nabíjí se -> neotravovat
        self.assertEqual(await n.battery(TRK), 0)
        await self.battery(16, 1)
        self.assertEqual(await n.battery(TRK), 1)
        self.assertEqual(len(s.sent), 4)  # 2 notifikace × 2 odběry

    async def test_cleanup_gone_and_failing_subscriptions(self) -> None:
        from tractive_ingestor.push import MAX_FAILURES, send_to_all

        await self.add_sub("c")
        sender = FakeSender({APPLE + "a": 410, APPLE + "b": 500})
        res = await send_to_all(self.pool, sender, '{"title":"t"}')
        self.assertEqual(res.as_dict(), {"sent": 1, "removed": 1, "failed": 1})
        rows = {r["endpoint"]: r for r in await self.pool.fetch(
            "SELECT endpoint, failures, last_ok_at FROM push_subscriptions WHERE endpoint LIKE $1", APPLE + "%")}
        self.assertNotIn(APPLE + "a", rows)  # 410 -> smazáno
        self.assertEqual(rows[APPLE + "b"]["failures"], 1)
        self.assertIsNotNone(rows[APPLE + "c"]["last_ok_at"])
        sender.statuses[APPLE + "c"] = 404
        for _ in range(MAX_FAILURES - 1):
            await send_to_all(self.pool, sender, "{}")
        left = [r["endpoint"] for r in await self.pool.fetch(
            "SELECT endpoint FROM push_subscriptions WHERE endpoint LIKE $1", APPLE + "%")]
        self.assertEqual(left, [])  # c: 404, b: 10 chyb v řadě
        # znovu přihlášený odběr začíná s čistým počítadlem
        await self.add_sub("b")
        self.assertEqual(await self.pool.fetchval(
            "SELECT failures FROM push_subscriptions WHERE endpoint = $1", APPLE + "b"), 0)

    async def test_api_store_push_on_readonly_pool(self) -> None:
        from tractive_api.store import PgApiStore

        store = PgApiStore(os.environ["TEST_DATABASE_URL"])
        try:
            await store.push_subscribe(APPLE + "api", P256, AUTH, "Safari")
            self.assertEqual(await store.push_count(), 3)
            res = await store.push_send_all(FakeSender({APPLE + "a": 410}), "{}")
            self.assertEqual(res, {"sent": 2, "removed": 1, "failed": 0})
            self.assertTrue(await store.push_unsubscribe(APPLE + "api"))
            self.assertFalse(await store.push_unsubscribe(APPLE + "api"))
            with self.assertRaises(InvalidSubscription):
                await store.push_subscribe("https://evil.example.com/x", P256, AUTH, None)
        finally:
            await store.close()


# ---------------------------------------------------------------------- API routy (bez DB)

try:
    from fastapi.testclient import TestClient

    from tractive_api.app import create_app
    from tractive_api.config import ApiSettings

    HAVE_FASTAPI = True
except ImportError:  # pragma: no cover
    HAVE_FASTAPI = False


class PushFakeStore:
    def __init__(self) -> None:
        self.subs: dict[str, tuple[str, str, str | None]] = {}

    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    async def push_subscribe(self, endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> int:
        validate_subscription(endpoint, p256dh, auth)
        self.subs[endpoint] = (p256dh, auth, user_agent)
        return len(self.subs)

    async def push_unsubscribe(self, endpoint: str) -> bool:
        return self.subs.pop(endpoint, None) is not None

    async def push_count(self) -> int:
        return len(self.subs)

    async def push_send_all(self, sender: Any, payload: str) -> dict[str, int]:
        for ep, (p, a, _) in self.subs.items():
            await sender.send({"endpoint": ep, "p256dh": p, "auth": a}, payload, ttl=60, urgency="high")
        return {"sent": len(self.subs), "removed": 0, "failed": 0}


@unittest.skipUnless(HAVE_FASTAPI, "fastapi/httpx nejsou nainstalované")
class PushApiTest(unittest.TestCase):
    def make(self, *, keys: bool = True, pin: str | None = None) -> tuple[Any, PushFakeStore, FakeSender]:
        pub, priv = generate_vapid_keys() if keys else (None, None)
        settings = ApiSettings(_env_file=None, database_url=None, api_web_dist=Path("/nonexistent"),
                               vapid_public_key=pub, vapid_private_key=priv, app_pin=pin,
                               app_session_secret="ab" * 32)
        store, sender = PushFakeStore(), FakeSender()
        return TestClient(create_app(settings, store, push_sender=sender), base_url="https://testserver"), store, sender

    def test_flow(self) -> None:
        c, store, sender = self.make()
        r = c.get("/api/push/vapid-public-key")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["enabled"])
        self.assertEqual(len(base64.urlsafe_b64decode(r.json()["public_key"] + "==")), 65)
        self.assertEqual(r.headers["cache-control"], "no-store")
        sub = {"endpoint": APPLE + "x", "expirationTime": None, "keys": {"p256dh": P256, "auth": AUTH}}
        r = c.post("/api/push/subscribe", json=sub, headers={"User-Agent": "iPhone Safari"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(store.subs[APPLE + "x"][2], "iPhone Safari")
        r = c.post("/api/push/test")
        self.assertEqual(r.json(), {"ok": True, "sent": 1, "removed": 0, "failed": 0})
        self.assertEqual(sender.sent[0][1]["tag"], "test")
        self.assertEqual(c.post("/api/push/unsubscribe", json={"endpoint": APPLE + "x"}).json()["removed"], True)

    def test_invalid_subscription_422(self) -> None:
        c, _, _ = self.make()
        bad = {"endpoint": "https://evil.example.com/x", "keys": {"p256dh": P256, "auth": AUTH}}
        self.assertEqual(c.post("/api/push/subscribe", json=bad).status_code, 422)
        self.assertEqual(c.post("/api/push/subscribe", json={"endpoint": APPLE}).status_code, 422)

    def test_disabled_without_keys(self) -> None:
        c, _, _ = self.make(keys=False)
        self.assertEqual(c.get("/api/push/vapid-public-key").json(), {"enabled": False, "public_key": None})
        sub = {"endpoint": APPLE + "x", "keys": {"p256dh": P256, "auth": AUTH}}
        self.assertEqual(c.post("/api/push/subscribe", json=sub).status_code, 503)
        self.assertEqual(c.post("/api/push/test").status_code, 503)

    def test_gated_by_login(self) -> None:
        c, _, _ = self.make(pin="tajne-heslo-123")
        for method, path in (("get", "/api/push/vapid-public-key"), ("post", "/api/push/subscribe"),
                             ("post", "/api/push/unsubscribe"), ("post", "/api/push/test")):
            self.assertEqual(getattr(c, method)(path).status_code, 401, path)
