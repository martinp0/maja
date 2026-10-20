"""Passkeys (WebAuthn) – registrace a přihlášení se softwarovým „autentikátorem“ (P-256, attestation none).

Bez DB (in-memory store); SQL ve PgApiStore se ověří proti Postgresu s TEST_DATABASE_URL.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from tractive_api.app import create_app
from tractive_api.config import ApiSettings
from tractive_api.passkeys import ChallengeStore, device_name

RP_ID = "maja.example.test"
ORIGIN = "https://maja.example.test"
SECRET = "cd" * 32
PIN = "dlouhe-tajne-heslo"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SoftAuthenticator:
    """Minimální platform authenticator: ES256, UV vždy, sign_count roste."""

    def __init__(self, rp_id: str = RP_ID, origin: str = ORIGIN) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.rp_id, self.origin = rp_id, origin
        self.count = 0

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def _client_data(self, typ: str, challenge: str, origin: str | None = None) -> bytes:
        return json.dumps({"type": typ, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def create(self, options: dict[str, Any], *, uv: bool = True) -> dict[str, Any]:
        flags = 0x01 | (0x04 if uv else 0) | 0x40  # UP | UV | AT
        auth_data = (hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
                     + b"\0" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose())
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cd = self._client_data("webauthn.create", options["challenge"])
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64u(cd), "attestationObject": b64u(att), "transports": ["internal", "hybrid"]},
                "clientExtensionResults": {}, "authenticatorAttachment": "platform"}

    def get(self, options: dict[str, Any], *, uv: bool = True, origin: str | None = None,
            challenge: str | None = None) -> dict[str, Any]:
        self.count += 1
        flags = 0x01 | (0x04 if uv else 0)
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        cd = self._client_data("webauthn.get", challenge or options["challenge"], origin)
        sig = self.key.sign(auth_data + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64u(cd), "authenticatorData": b64u(auth_data), "signature": b64u(sig),
                             "userHandle": None},
                "clientExtensionResults": {}, "authenticatorAttachment": "platform"}


class MemStore:
    def __init__(self) -> None:
        self.creds: dict[int, dict[str, Any]] = {}
        self.next = 1

    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    async def webauthn_list(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self.creds.values()]

    async def webauthn_get(self, credential_id: bytes) -> dict[str, Any] | None:
        return next((dict(c) for c in self.creds.values() if c["credential_id"] == credential_id), None)

    async def webauthn_add(self, cred: dict[str, Any]) -> dict[str, Any]:
        row = {**cred, "id": self.next, "created_at": datetime.now(UTC), "last_used_at": None}
        self.creds[self.next] = row
        self.next += 1
        return dict(row)

    async def webauthn_touch(self, credential_id: bytes, sign_count: int) -> None:
        for c in self.creds.values():
            if c["credential_id"] == credential_id:
                c["sign_count"], c["last_used_at"] = sign_count, datetime.now(UTC)

    async def webauthn_delete(self, id_: int) -> bool:
        return self.creds.pop(id_, None) is not None

    async def webauthn_count(self) -> int:
        return len(self.creds)


class PasskeyApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemStore()
        settings = ApiSettings(_env_file=None, database_url=None, api_web_dist=Path("/nonexistent"),
                               app_pin=PIN, app_session_secret=SECRET, webauthn_rp_id=RP_ID, webauthn_origin=ORIGIN)
        self.app = create_app(settings, self.store)  # type: ignore[arg-type]
        self.c = TestClient(self.app, base_url="https://testserver")
        self.auth = SoftAuthenticator()

    def login_password(self) -> None:
        self.assertEqual(self.c.post("/api/login", json={"password": PIN}).status_code, 200)

    def register(self, a: SoftAuthenticator | None = None) -> dict[str, Any]:
        a = a or self.auth
        opts = self.c.post("/api/webauthn/register/options").json()
        r = self.c.post("/api/webauthn/register/verify", json={"credential": a.create(opts)},
                        headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def passkey_login(self, a: SoftAuthenticator | None = None, **kw: Any):
        opts = self.c.post("/api/webauthn/login/options")
        self.assertEqual(opts.status_code, 200, opts.text)
        return self.c.post("/api/webauthn/login/verify", json=(a or self.auth).get(opts.json(), **kw))

    def test_register_requires_session(self) -> None:
        self.assertEqual(self.c.post("/api/webauthn/register/options").status_code, 401)
        self.assertEqual(self.c.post("/api/webauthn/register/verify", json={}).status_code, 401)
        self.assertEqual(self.c.get("/api/webauthn/credentials").status_code, 401)

    def test_full_flow(self) -> None:
        self.assertEqual(self.c.get("/api/me").json()["passkey_count"], 0)
        self.assertEqual(self.c.post("/api/webauthn/login/options").status_code, 404)  # nic registrovaného
        self.login_password()
        opts = self.c.post("/api/webauthn/register/options").json()
        self.assertEqual(opts["rp"]["id"], RP_ID)
        self.assertEqual(opts["authenticatorSelection"],
                         {"authenticatorAttachment": "platform", "residentKey": "preferred",
                          "requireResidentKey": False, "userVerification": "required"})
        out = self.register()
        self.assertEqual(out["credential"]["name"], "iPhone")
        self.assertEqual(out["credential"]["credential_id"], b64u(self.auth.cred_id))
        self.assertEqual(self.c.get("/api/me").json()["passkey_count"], 1)
        # exclude_credentials obsahuje už registrovaný passkey
        opts2 = self.c.post("/api/webauthn/register/options").json()
        self.assertEqual([e["id"] for e in opts2["excludeCredentials"]], [b64u(self.auth.cred_id)])

        self.c.post("/api/logout")
        self.c.cookies.clear()
        self.assertFalse(self.c.get("/api/me").json()["authed"])
        r = self.passkey_login()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.c.get("/api/me").json()["authed"])
        self.assertEqual(self.c.get("/api/webauthn/credentials").status_code, 200)  # gated route projde
        stored = next(iter(self.store.creds.values()))
        self.assertEqual(stored["sign_count"], 1)
        self.assertIsNotNone(stored["last_used_at"])

        lst = self.c.get("/api/webauthn/credentials").json()
        self.assertEqual(len(lst), 1)
        self.assertNotIn("public_key", lst[0])
        self.assertEqual(self.c.post(f"/api/webauthn/credentials/{lst[0]['id']}/delete").json(), {"ok": True})
        self.assertEqual(self.c.post(f"/api/webauthn/credentials/{lst[0]['id']}/delete").status_code, 404)
        self.assertEqual(self.c.get("/api/me").json()["passkey_count"], 0)

    def test_rejects_without_user_verification(self) -> None:
        self.login_password()
        opts = self.c.post("/api/webauthn/register/options").json()
        r = self.c.post("/api/webauthn/register/verify", json={"credential": self.auth.create(opts, uv=False)})
        self.assertEqual(r.status_code, 400)
        self.register()
        self.c.cookies.clear()
        self.assertEqual(self.passkey_login(uv=False).status_code, 401)
        self.assertFalse(self.c.get("/api/me").json()["authed"])

    def test_wrong_origin_unknown_key_replay(self) -> None:
        self.login_password()
        self.register()
        self.c.cookies.clear()
        self.assertEqual(self.passkey_login(origin="https://evil.example").status_code, 401)
        self.assertEqual(self.passkey_login(SoftAuthenticator()).status_code, 401)  # neregistrovaný klíč
        # challenge jde použít jen jednou
        opts = self.c.post("/api/webauthn/login/options").json()
        wa_cookie = self.c.cookies.get("maja_wa")
        ok = self.c.post("/api/webauthn/login/verify", json=self.auth.get(opts))
        self.assertEqual(ok.status_code, 200)
        self.c.cookies.clear()
        self.c.cookies.set("maja_wa", wa_cookie, domain="testserver.local", path="/api/webauthn")
        again = self.c.post("/api/webauthn/login/verify", json=self.auth.get(opts))
        self.assertEqual(again.status_code, 400)
        self.assertFalse(self.c.get("/api/me").json()["authed"])

    def test_login_rate_limited(self) -> None:
        self.login_password()
        self.register()
        self.c.cookies.clear()
        for _ in range(5):
            self.assertEqual(self.passkey_login(SoftAuthenticator()).status_code, 401)
        self.assertEqual(self.c.post("/api/webauthn/login/options").status_code, 429)
        self.assertEqual(self.c.post("/api/webauthn/login/verify", json={}).status_code, 429)
        # sdílený limiter s heslem
        self.assertEqual(self.c.post("/api/login", json={"password": PIN}).status_code, 429)

    def test_me_survives_db_error(self) -> None:
        async def boom() -> int:
            raise RuntimeError("db down")

        self.store.webauthn_count = boom  # type: ignore[method-assign]
        r = self.c.get("/api/me")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["passkey_count"])


class ChallengeStoreTest(unittest.TestCase):
    def test_ttl_kind_once(self) -> None:
        s = ChallengeStore(ttl_s=10)
        k = s.put(b"x", "login", now=0)
        self.assertIsNone(s.pop(k, "register", now=1))  # jiný druh -> zahozeno
        k = s.put(b"y", "login", now=0)
        self.assertIsNone(s.pop(k, "login", now=11))  # vypršel
        k = s.put(b"z", "login", now=0)
        self.assertEqual(s.pop(k, "login", now=5), b"z")
        self.assertIsNone(s.pop(k, "login", now=5))
        self.assertIsNone(s.pop(None, "login"))

    def test_bounded(self) -> None:
        s = ChallengeStore(ttl_s=100, max_items=3)
        keys = [s.put(bytes([i]), "login", now=i) for i in range(5)]
        self.assertIsNone(s.pop(keys[0], "login", now=6))
        self.assertEqual(s.pop(keys[4], "login", now=6), b"\x04")

    def test_device_name(self) -> None:
        self.assertEqual(device_name("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)"), "iPhone")
        self.assertEqual(device_name("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"), "Mac")
        self.assertEqual(device_name(None), "Zařízení")


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL není nastavené")
class PgPasskeyStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_crud_on_readonly_pool(self) -> None:
        from tractive_api.store import PgApiStore
        from tractive_ingestor.storage import PgStorage

        st = PgStorage(os.environ["TEST_DATABASE_URL"])
        await st.connect()
        await st.apply_schema()
        await st.pool.execute("DELETE FROM webauthn_credentials WHERE name = 'test-pk'")
        await st.close()
        s = PgApiStore(os.environ["TEST_DATABASE_URL"])
        cid = os.urandom(16)
        try:
            before = await s.webauthn_count()
            row = await s.webauthn_add({"credential_id": cid, "public_key": b"cose", "sign_count": 0,
                                        "transports": ["internal"], "name": "test-pk", "aaguid": "0" * 32,
                                        "backed_up": True})
            self.assertEqual(await s.webauthn_count(), before + 1)
            got = await s.webauthn_get(cid)
            assert got is not None
            self.assertEqual(bytes(got["public_key"]), b"cose")
            await s.webauthn_touch(cid, 7)
            got = await s.webauthn_get(cid)
            assert got is not None
            self.assertEqual(got["sign_count"], 7)
            self.assertIsNotNone(got["last_used_at"])
            self.assertTrue(any(c["id"] == row["id"] for c in await s.webauthn_list()))
            self.assertTrue(await s.webauthn_delete(row["id"]))
            self.assertFalse(await s.webauthn_delete(row["id"]))
        finally:
            await s.close()
