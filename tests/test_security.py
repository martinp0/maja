"""Zámek přihlášení, read-only token, CORS a bezpečnostní hlavičky.

Bez databáze. Souřadnice v testech jsou smyšlené.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi.responses import Response
from fastapi.testclient import TestClient

from tractive_api.app import apply_security_headers, create_app
from tractive_api.auth import (
    READ_TOKEN_POSITION_MAX_POINTS,
    LoginRateLimiter,
    check_read_token,
    client_address,
    make_token,
    parse_read_tokens,
)
from tractive_api.config import ApiSettings

from test_api import TRACKER, FakeStore

SECRET = "ab" * 32

HOME_LAT, HOME_LNG = 12.5, 10.5
TOK_A = "a" * 32
TOK_B = "b" * 32
TOK_C = "c" * 64


class LimitAndAddressTest(unittest.TestCase):
    def test_limiter_is_per_ip_without_global_cap(self) -> None:
        lim = LoginRateLimiter(max_failures=2, window=300)
        for i in range(40):
            lim.fail(f"198.51.100.{i}")
        self.assertFalse(lim.blocked("203.0.113.10"))
        lim.fail("a")
        self.assertFalse(lim.blocked("a"))
        lim.fail("a")
        self.assertTrue(lim.blocked("a"))
        self.assertFalse(lim.blocked("b"))
        lim.success("a")
        self.assertFalse(lim.blocked("a"))

    def test_forwarded_ip_only_from_trusted_proxy(self) -> None:
        headers = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "192.0.2.1, 172.18.0.1"}
        self.assertEqual(client_address("127.0.0.1", headers), "203.0.113.9")
        self.assertEqual(client_address("::1", headers), "203.0.113.9")
        self.assertEqual(client_address("::ffff:127.0.0.1", headers), "203.0.113.9")
        self.assertEqual(client_address("172.18.0.1", headers), "203.0.113.9")
        # přímý klient hlavičku nepodstrčí
        self.assertEqual(client_address("198.51.100.8", headers), "198.51.100.8")
        self.assertEqual(client_address("10.1.2.3", headers), "10.1.2.3")
        self.assertEqual(client_address("testclient", headers), "testclient")
        # bez CF se bere první adresa z X-Forwarded-For
        xff = {"x-forwarded-for": " 192.0.2.10 , 172.18.0.1"}
        self.assertEqual(client_address("172.17.0.1", xff), "192.0.2.10")
        # neplatná CF hlavička spadne na XFF, obě špatné na peer
        bad = {"cf-connecting-ip": "not-an-ip", "x-forwarded-for": "also-bad"}
        self.assertEqual(client_address("127.0.0.1", bad), "127.0.0.1")
        self.assertEqual(client_address(None, {}), "unknown")

    def test_parse_named_and_legacy_tokens(self) -> None:
        legacy = parse_read_tokens(f" {TOK_A}, {TOK_B}, short ")
        self.assertEqual(legacy, [("token-1", TOK_A), ("token-2", TOK_B)])
        named = parse_read_tokens(f"iphone={TOK_A}, watch={TOK_B}")
        self.assertEqual(named, [("iphone", TOK_A), ("watch", TOK_B)])
        # stejné jméno se nepřebije – oba tokeny zůstanou, druhé jméno se odliší
        dup = parse_read_tokens(f"iphone={TOK_A},iphone={TOK_B}")
        self.assertEqual([name for name, _ in dup], ["iphone", "iphone-2"])
        self.assertEqual([secret for _, secret in dup], [TOK_A, TOK_B])
        padded = "A" * 40 + "=="
        self.assertEqual(parse_read_tokens(padded), [("token-1", padded)])
        self.assertEqual(parse_read_tokens(""), [])
        self.assertEqual(parse_read_tokens(None), [])
        # konstantní čas přes seznam pořád sedí na secret, ne na jméno
        self.assertTrue(check_read_token([TOK_A, TOK_B], TOK_B))
        self.assertFalse(check_read_token([TOK_A], TOK_A + "x"))

    def test_settings_read_names_and_empty_cors(self) -> None:
        s = ApiSettings(_env_file=None, api_read_tokens=f"phone={TOK_A}, {TOK_B}", api_cors_origins="")
        self.assertEqual(s.read_token_entries, [("phone", TOK_A), ("token-1", TOK_B)])
        self.assertEqual(s.read_tokens, [TOK_A, TOK_B])
        self.assertEqual(s.cors_origins, [])
        bare = ApiSettings(_env_file=None, api_cors_origins="http://localhost:5173, ,http://127.0.0.1:5173")
        self.assertEqual(bare.cors_origins, ["http://localhost:5173", "http://127.0.0.1:5173"])
        self.assertEqual(ApiSettings(_env_file=None).cors_origins, [])


class _HomeStore(FakeStore):
    async def latest_position(self, tracker_id: str) -> dict[str, Any]:
        return {"ts": datetime(2026, 10, 19, 10, 0, tzinfo=UTC), "lat": 50.0, "lng": 14.0, "accuracy_m": 10.0}

    async def geofence_evaluate(self, tracker_id: str, cfg: Any) -> dict[str, Any]:
        t0 = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
        return {"state": "away", "since": t0, "last_ts": t0, "pending_state": None, "pending_since": None}

    async def geofence_events(self, tracker_id: str, limit: int) -> list[dict[str, Any]]:
        return [{"event_type": "left_home", "distance_m": 400.0, "home_lat": HOME_LAT, "home_lng": HOME_LNG}]


def _app(store: FakeStore | None = None, **kw: Any) -> TestClient:
    settings = ApiSettings(
        _env_file=None,
        database_url=None,
        api_web_dist=Path("/nonexistent"),
        app_pin="4321",
        app_session_secret=SECRET,
        home_lat=HOME_LAT,
        home_lon=HOME_LNG,
        **kw,
    )
    return TestClient(create_app(settings, store or _HomeStore()), base_url="https://testserver",
                      client=("127.0.0.1", 50000))


class ReadTokenExposureTest(unittest.TestCase):
    def test_positions_clamped_and_home_omitted_for_token_only(self) -> None:
        store = _HomeStore()
        tok = TOK_C
        with _app(store, api_read_tokens=f"iphone={tok}") as c:
            h = {"Authorization": f"Bearer {tok}"}
            old = "2020-01-01T00:00:00+00:00"
            r = c.get(f"/api/trackers/{TRACKER}/positions", params={"since": old, "limit": 99999}, headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            _, since, limit = store.positions_calls[-1]
            self.assertLess(abs((datetime.now(UTC) - timedelta(hours=48) - since).total_seconds()), 5)
            self.assertEqual(limit, READ_TOKEN_POSITION_MAX_POINTS)
            self.assertNotIn(str(HOME_LAT), r.text)

            recent = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0)
            r = c.get(f"/api/trackers/{TRACKER}/positions",
                      params={"since": recent.isoformat(), "limit": 10}, headers=h)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(store.positions_calls[-1][1], recent)
            self.assertEqual(store.positions_calls[-1][2], 10)

            state = c.get("/api/geofence/state", headers=h)
            self.assertEqual(state.status_code, 200, state.text)
            body = state.json()
            self.assertIsNone(body["home"])
            self.assertEqual(body["state"], "away")
            self.assertIsNotNone(body["distance_m"])
            self.assertNotIn(f'"lat":{HOME_LAT}', state.text.replace(" ", ""))
            self.assertNotIn(f'"lng":{HOME_LNG}', state.text.replace(" ", ""))
            events = c.get("/api/geofence/events", headers=h).json()
            self.assertEqual(events[0]["event_type"], "left_home")
            self.assertNotIn("home_lat", events[0])
            self.assertNotIn("home_lng", events[0])

            # cookie session: plná historie i souřadnice domova
            c.cookies.set("maja_session", make_token(bytes.fromhex(SECRET)))
            r = c.get(f"/api/trackers/{TRACKER}/positions", params={"since": old, "limit": 4000})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(store.positions_calls[-1][1], datetime(2020, 1, 1, tzinfo=UTC))
            self.assertEqual(store.positions_calls[-1][2], 4000)
            state = c.get("/api/geofence/state")
            self.assertEqual(state.json()["home"], {"lat": HOME_LAT, "lng": HOME_LNG})
            self.assertEqual(c.get("/api/geofence/events").json()[0]["home_lat"], HOME_LAT)

    def test_log_has_token_name_not_secret(self) -> None:
        tok = TOK_C
        with structlog.testing.capture_logs() as logs, _app(api_read_tokens=f"iphone={tok},watch={TOK_A}") as c:
            self.assertEqual(c.get("/api/pets", headers={"Authorization": f"Bearer {tok}"}).status_code, 200)
            self.assertEqual(c.get("/api/pets", headers={"Authorization": "Bearer " + "z" * 32}).status_code, 401)
        blob = json.dumps(logs, default=str)
        self.assertNotIn(tok, blob)
        self.assertNotIn(TOK_A, blob)
        self.assertTrue(any(e.get("event") == "api.read_token_ok" and e.get("token_name") == "iphone" for e in logs))
        self.assertTrue(any(e.get("event") == "api.read_token_failed" and e.get("token_name") == "unknown" for e in logs))

    def test_others_failures_do_not_lock_owner_or_valid_token(self) -> None:
        tok = TOK_C
        settings = ApiSettings(
            _env_file=None, database_url=None, api_web_dist=Path("/nonexistent"),
            app_pin="4321", app_session_secret=SECRET, api_read_tokens=f"iphone={tok}",
        )
        app = create_app(settings, _HomeStore())
        with TestClient(app, base_url="https://testserver", client=("198.51.100.8", 443)) as outsider:
            for _ in range(5):
                r = outsider.post("/api/login", json={"pin": "0000"}, headers={
                    "CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9",
                })
                self.assertEqual(r.status_code, 401)
            # podvržená hlavička peer, který není proxy, nepřesune zámek jinam
            self.assertEqual(outsider.post("/api/login", json={"pin": "4321"}, headers={
                "CF-Connecting-IP": "203.0.113.50"}).status_code, 429)
            # a nezamkne platný token na tom peeru – token se ověří dřív
            self.assertEqual(outsider.get("/api/pets", headers={"Authorization": f"Bearer {tok}"}).status_code, 200)
        with TestClient(app, base_url="https://testserver", client=("127.0.0.1", 9)) as proxy:
            # IP, kterou outsider jen podstrčil, není zamčená
            self.assertEqual(proxy.post("/api/login", json={"pin": "4321"},
                                        headers={"CF-Connecting-IP": "203.0.113.9"}).status_code, 200)
        # desítky cizích IP (dřív globální strop ~30) nezamknou novou
        app2 = create_app(settings, _HomeStore())
        with TestClient(app2, base_url="https://testserver", client=("127.0.0.1", 9)) as proxy:
            for i in range(40):
                r = proxy.post("/api/login", json={"pin": "0000"}, headers={"CF-Connecting-IP": f"203.0.113.{i}"})
                self.assertEqual(r.status_code, 401, i)
            self.assertEqual(proxy.post("/api/login", json={"pin": "4321"},
                                        headers={"CF-Connecting-IP": "198.51.100.20"}).status_code, 200)
            self.assertEqual(proxy.get("/api/pets", headers={
                "Authorization": f"Bearer {tok}", "CF-Connecting-IP": "198.51.100.21"}).status_code, 200)

    def test_pin_lockout_does_not_block_token_limiter(self) -> None:
        tok = TOK_C
        with _app(api_read_tokens=tok) as c:
            for _ in range(5):
                self.assertEqual(c.post("/api/login", json={"pin": "0000"}).status_code, 401)
            self.assertEqual(c.post("/api/login", json={"pin": "4321"}).status_code, 429)
            self.assertEqual(c.get("/api/pets", headers={"Authorization": f"Bearer {tok}"}).status_code, 200)


class HeaderAndCorsTest(unittest.TestCase):
    def test_security_headers_keep_hsts(self) -> None:
        resp = Response(content="ok")
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        apply_security_headers(resp, "/api/pets")
        self.assertEqual(resp.headers["strict-transport-security"], "max-age=31536000")
        csp = resp.headers["content-security-policy"]
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("https://tile.openstreetmap.org", csp)
        self.assertIn("script-src 'self';", csp)
        self.assertNotIn("cdn.jsdelivr.net", csp)
        self.assertIn("style-src 'self' 'unsafe-inline'", csp)
        self.assertEqual(resp.headers["x-content-type-options"], "nosniff")
        self.assertEqual(resp.headers["referrer-policy"], "no-referrer")
        docs = Response()
        apply_security_headers(docs, "/api/docs")
        self.assertIn("cdn.jsdelivr.net", docs.headers["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", docs.headers["content-security-policy"])
        self.assertNotIn("strict-transport-security", docs.headers)

    def test_responses_carry_headers_without_adding_hsts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "index.html").write_text("<!doctype html><title>Mája</title>", encoding="utf-8")
            settings = ApiSettings(_env_file=None, api_web_dist=Path(d), api_cors_origins="")
            with TestClient(create_app(settings, FakeStore())) as c:
                for path in ("/", "/api/health", "/api/pets"):
                    r = c.get(path)
                    self.assertEqual(r.status_code, 200, path)
                    self.assertEqual(r.headers.get("x-content-type-options"), "nosniff", path)
                    self.assertEqual(r.headers.get("referrer-policy"), "no-referrer", path)
                    self.assertIn("frame-ancestors 'none'", r.headers.get("content-security-policy", ""), path)
                    self.assertNotIn("strict-transport-security", r.headers, path)
                page = c.get("/")
                self.assertIn("https://tile.openstreetmap.org", page.headers["content-security-policy"])
                docs = c.get("/api/docs")
                self.assertEqual(docs.status_code, 200)
                self.assertIn("cdn.jsdelivr.net", docs.headers["content-security-policy"])
                self.assertIsNone(c.get("/api/health", headers={"Origin": "http://localhost:5173"}).headers.get(
                    "access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()
