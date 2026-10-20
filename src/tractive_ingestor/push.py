"""Web Push notifikace (VAPID) – odběry, odesílání a idempotentní upozornění.

Konfigurace (env/.env, NIKDY do gitu): VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT.
Bez nich je funkce vypnutá (API vrací enabled=false, ingestor notifier nespouští).
Klíče:  python -m tractive_ingestor gen-vapid   (vypíše řádky do .env)

Idempotence (restart ingestoru nikdy nepošle stejné upozornění znovu):
- geofence_events.notified_at – událost se nejdřív „zabere“ (UPDATE … WHERE notified_at IS NULL
  RETURNING) a teprve po commitu se odešle (at-most-once). Staré události (created_at starší
  než PUSH_MAX_EVENT_AGE_S, např. po delším výpadku) se jen označí, neposílají se.
- notify_state.escape_since – „utekla“ max. jednou za away epizodu (klíč = geofence_state.since).
- notify_state.battery_low_at – slabá baterie jednou pod BATTERY_LOW_PCT, znovu „nabije“ až nad
  BATTERY_RESET_PCT.
Odběry s odpovědí 404/410 (vypršelé/odhlášené) se mažou; po MAX_FAILURES chybách v řadě taky.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

import structlog

if TYPE_CHECKING:
    import asyncpg

    from .home_geofence import HomeConfig

log = structlog.get_logger(__name__)

BATTERY_LOW_PCT = 20
BATTERY_RESET_PCT = 30
MAX_FAILURES = 10
DEFAULT_PET_NAME = "Mája"
# Contact claim for Web Push. Real deployments set VAPID_SUBJECT in .env.
DEFAULT_VAPID_SUBJECT = "mailto:you@example.com"

# Push služby prohlížečů – jinam API odběr nepřijme (ochrana proti SSRF přes endpoint)
ALLOWED_PUSH_HOST_SUFFIXES = (
    "push.apple.com",  # Safari / iOS PWA (web.push.apple.com)
    "fcm.googleapis.com",  # Chrome, Edge (Chromium), Android
    "push.services.mozilla.com",  # Firefox
    "notify.windows.com",  # starý Edge / Windows
)


# ============================================================================ klíče


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def generate_vapid_keys() -> tuple[str, str]:
    """(public, private) – base64url; public = nekomprimovaný P-256 bod (applicationServerKey),
    private = raw 32 B skalár (formát, který bere py_vapid/pywebpush)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64u(pub), _b64u(priv)


def vapid_env_lines(subject: str = DEFAULT_VAPID_SUBJECT) -> str:
    pub, priv = generate_vapid_keys()
    return f"VAPID_PUBLIC_KEY={pub}\nVAPID_PRIVATE_KEY={priv}\nVAPID_SUBJECT={subject}\n"


@dataclass(slots=True, frozen=True)
class PushConfig:
    public_key: str
    private_key: str = field(repr=False)
    subject: str = DEFAULT_VAPID_SUBJECT

    @classmethod
    def build(cls, public_key: str | None, private_key: str | None, subject: str | None) -> PushConfig | None:
        pub, priv = (public_key or "").strip(), (private_key or "").strip()
        if not pub or not priv:
            return None
        subj = (subject or "").strip() or DEFAULT_VAPID_SUBJECT
        if not subj.startswith(("mailto:", "https://")):
            log.warning("push.invalid_subject", hint="VAPID_SUBJECT musí začínat mailto: nebo https://")
            return None
        return cls(pub, priv, subj)


# ============================================================================ odběr


class InvalidSubscription(ValueError):
    pass


def _is_b64u(s: str) -> bool:
    return bool(s) and all(c.isalnum() or c in "-_=" for c in s)


def validate_subscription(endpoint: str, p256dh: str, auth: str) -> None:
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise InvalidSubscription("Neplatný endpoint")
    u = urlparse(endpoint)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        raise InvalidSubscription("Endpoint musí být https")
    if not any(host == s or host.endswith("." + s) for s in ALLOWED_PUSH_HOST_SUFFIXES):
        raise InvalidSubscription("Neznámá push služba")
    if not (isinstance(p256dh, str) and _is_b64u(p256dh) and 40 <= len(p256dh) <= 200):
        raise InvalidSubscription("Neplatný klíč p256dh")
    if not (isinstance(auth, str) and _is_b64u(auth) and 8 <= len(auth) <= 64):
        raise InvalidSubscription("Neplatný klíč auth")


async def upsert_subscription(conn: asyncpg.Connection, endpoint: str, p256dh: str, auth: str,
                              user_agent: str | None) -> int:
    validate_subscription(endpoint, p256dh, auth)
    return await conn.fetchval(
        """INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (endpoint) DO UPDATE SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
               user_agent = EXCLUDED.user_agent, failures = 0
           RETURNING id""",
        endpoint, p256dh, auth, (user_agent or "")[:300] or None,
    )


async def delete_subscription(conn: asyncpg.Connection, endpoint: str) -> bool:
    return (await conn.execute("DELETE FROM push_subscriptions WHERE endpoint = $1", endpoint)) != "DELETE 0"


# ============================================================================ odesílání


class Sender(Protocol):
    async def send(self, sub: Mapping[str, Any], payload: str, *, ttl: int, urgency: str) -> int:
        """Vrací HTTP status push služby (0 = síťová chyba)."""
        ...


class WebPushSender:
    """pywebpush (čistý Python + cryptography wheel – funguje i na arm64 slim)."""

    def __init__(self, cfg: PushConfig, timeout_s: float = 10.0) -> None:
        from py_vapid import Vapid

        self.cfg = cfg
        self.timeout_s = timeout_s
        self._vapid = Vapid.from_string(cfg.private_key)

    def _send_sync(self, sub: Mapping[str, Any], payload: str, ttl: int, urgency: str) -> int:
        from pywebpush import WebPushException, webpush

        info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
        try:
            resp = webpush(info, data=payload, vapid_private_key=self._vapid,
                           vapid_claims={"sub": self.cfg.subject},  # pywebpush dict mutuje -> vždy nový
                           ttl=ttl, timeout=self.timeout_s, headers={"Urgency": urgency})
            return int(getattr(resp, "status_code", 201))
        except WebPushException as exc:
            resp = getattr(exc, "response", None)
            return int(resp.status_code) if resp is not None else 0
        except Exception as exc:  # síť, DNS…
            log.warning("push.send_error", error=repr(exc))
            return 0

    async def send(self, sub: Mapping[str, Any], payload: str, *, ttl: int = 3600, urgency: str = "normal") -> int:
        return await asyncio.to_thread(self._send_sync, sub, payload, ttl, urgency)


@dataclass(slots=True)
class SendResult:
    sent: int = 0
    removed: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"sent": self.sent, "removed": self.removed, "failed": self.failed}


def make_payload(title: str, body: str, *, tag: str, url: str = "/", **extra: Any) -> str:
    return json.dumps({"title": title, "body": body, "tag": tag, "url": url,
                       "ts": datetime.now(UTC).isoformat(), **extra}, ensure_ascii=False)


async def send_to_all(pool: asyncpg.Pool, sender: Sender, payload: str, *, ttl: int = 3600,
                      urgency: str = "normal", read_write: bool = False) -> SendResult:
    """Pošle payload všem odběrům a uklidí vypršelé (404/410). `read_write` pro read-only pool API."""
    res = SendResult()
    async with pool.acquire() as conn:
        subs = await conn.fetch("SELECT id, endpoint, p256dh, auth FROM push_subscriptions ORDER BY id")
    for s in subs:
        status = await sender.send(s, payload, ttl=ttl, urgency=urgency)
        async with pool.acquire() as conn, conn.transaction():
            if read_write:
                await conn.execute("SET TRANSACTION READ WRITE")
            if 200 <= status < 300:
                res.sent += 1
                await conn.execute("UPDATE push_subscriptions SET last_ok_at = now(), failures = 0 WHERE id = $1",
                                   s["id"])
            elif status in (404, 410):
                res.removed += 1
                await conn.execute("DELETE FROM push_subscriptions WHERE id = $1", s["id"])
                log.info("push.subscription_gone", id=s["id"], status=status)
            else:
                res.failed += 1
                n = await conn.fetchval(
                    "UPDATE push_subscriptions SET failures = failures + 1 WHERE id = $1 RETURNING failures", s["id"])
                if n is not None and n >= MAX_FAILURES:
                    await conn.execute("DELETE FROM push_subscriptions WHERE id = $1", s["id"])
                    res.removed += 1
                log.warning("push.send_failed", id=s["id"], status=status, failures=n)
    return res


# ============================================================================ texty


def fmt_duration(td: timedelta) -> str:
    mins = max(0, int(td.total_seconds() // 60))
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h} h {m} min"
    if h:
        return f"{h} h"
    return f"{m} min"


def text_left_home(name: str) -> tuple[str, str]:
    return f"{name} odešla z domova", "Je venku – klepni pro polohu na mapě."


def text_returned_home(name: str, away_for: timedelta | None) -> tuple[str, str]:
    body = f"Venku byla {fmt_duration(away_for)}." if away_for is not None else "Je zase v domácí zóně."
    return f"{name} je zpátky doma 🏠", body


def text_escaped(name: str, distance_m: float) -> tuple[str, str]:
    return f"🚨 {name} utekla! {round(distance_m)} m od domova", "Klepni pro živou polohu."


def text_battery_low(name: str, pct: int) -> tuple[str, str]:
    return f"🔋 Slabá baterie obojku ({pct} %)", f"Nabij obojek ({name}), ať o ni nepřijdeš."


# ============================================================================ notifier


@dataclass
class Notifier:
    """Projde DB a pošle nová upozornění. Volá ho ingestor periodicky (NotifyWorker)."""

    pool: asyncpg.Pool
    sender: Sender
    home_cfg: HomeConfig | None = None
    max_event_age_s: float = 900.0

    async def _push(self, title: str, body: str, *, tag: str, urgency: str = "normal", ttl: int = 3600,
                    **extra: Any) -> None:
        payload = make_payload(title, body, tag=tag, **extra)
        res = await send_to_all(self.pool, self.sender, payload, ttl=ttl, urgency=urgency)
        log.info("push.notified", tag=tag, **res.as_dict())

    async def _pet_name(self, tracker_id: str) -> str:
        name = await self.pool.fetchval(
            "SELECT name FROM pets WHERE tracker_id = $1 AND name IS NOT NULL ORDER BY pet_id LIMIT 1", tracker_id)
        return name or DEFAULT_PET_NAME

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        n = await self.geofence_events(now)
        trackers = await self.pool.fetch("SELECT tracker_id FROM trackers ORDER BY tracker_id")
        for r in trackers:
            tid = r["tracker_id"]
            if self.home_cfg is not None:
                n += await self.escape(tid)
            n += await self.battery(tid)
        return n

    async def geofence_events(self, now: datetime) -> int:
        """Zabere (označí) nové události a pak je pošle – restart = žádné duplikáty."""
        stale_before = now - timedelta(seconds=self.max_event_age_s)
        async with self.pool.acquire() as conn, conn.transaction():
            claimed = await conn.fetch(
                """UPDATE geofence_events SET notified_at = now()
                   WHERE id IN (SELECT id FROM geofence_events WHERE notified_at IS NULL
                                ORDER BY happened_at, id FOR UPDATE SKIP LOCKED LIMIT 20)
                   RETURNING id, tracker_id, event_type, happened_at, distance_m, created_at""")
        sent = 0
        for ev in sorted(claimed, key=lambda e: (e["happened_at"], e["id"])):
            if ev["created_at"] < stale_before:
                log.info("push.event_skipped_stale", id=ev["id"], event_type=ev["event_type"])
                continue
            name = await self._pet_name(ev["tracker_id"])
            if ev["event_type"] == "left_home":
                title, body = text_left_home(name)
                await self._push(title, body, tag="geofence", urgency="high", kind="left_home")
            elif ev["event_type"] == "returned_home":
                left = await self.pool.fetchval(
                    """SELECT happened_at FROM geofence_events WHERE tracker_id = $1 AND event_type = 'left_home'
                       AND happened_at < $2 ORDER BY happened_at DESC LIMIT 1""",
                    ev["tracker_id"], ev["happened_at"])
                title, body = text_returned_home(name, ev["happened_at"] - left if left else None)
                await self._push(title, body, tag="geofence", kind="returned_home")
            else:
                continue
            sent += 1
        return sent

    async def escape(self, tracker_id: str) -> int:
        """„Utekla“ jednou za away epizodu (notify_state.escape_since = geofence_state.since)."""
        assert self.home_cfg is not None
        async with self.pool.acquire() as conn, conn.transaction():
            st = await conn.fetchrow(
                "SELECT state, since, last_distance_m FROM geofence_state WHERE tracker_id = $1", tracker_id)
            if (st is None or st["state"] != "away" or st["since"] is None or st["last_distance_m"] is None
                    or st["last_distance_m"] <= self.home_cfg.escape_m):
                return 0
            got = await conn.fetchval(
                """INSERT INTO notify_state (tracker_id, escape_since) VALUES ($1, $2)
                   ON CONFLICT (tracker_id) DO UPDATE SET escape_since = EXCLUDED.escape_since, updated_at = now()
                   WHERE notify_state.escape_since IS DISTINCT FROM EXCLUDED.escape_since
                   RETURNING tracker_id""",
                tracker_id, st["since"])
        if got is None:
            return 0
        title, body = text_escaped(await self._pet_name(tracker_id), float(st["last_distance_m"]))
        await self._push(title, body, tag="escape", urgency="high", ttl=1800, kind="escaped",
                         requireInteraction=True)
        return 1

    async def battery(self, tracker_id: str) -> int:
        row = await self.pool.fetchrow(
            """SELECT battery_pct, charging FROM hw_reports WHERE tracker_id = $1 AND battery_pct IS NOT NULL
               ORDER BY ts DESC LIMIT 1""", tracker_id)
        if row is None:
            return 0
        pct = int(row["battery_pct"])
        if pct > BATTERY_RESET_PCT:
            await self.pool.execute(
                "UPDATE notify_state SET battery_low_at = NULL, updated_at = now() "
                "WHERE tracker_id = $1 AND battery_low_at IS NOT NULL", tracker_id)
            return 0
        if pct >= BATTERY_LOW_PCT or row["charging"]:
            return 0
        got = await self.pool.fetchval(
            """INSERT INTO notify_state (tracker_id, battery_low_at) VALUES ($1, now())
               ON CONFLICT (tracker_id) DO UPDATE SET battery_low_at = now(), updated_at = now()
               WHERE notify_state.battery_low_at IS NULL
               RETURNING tracker_id""", tracker_id)
        if got is None:
            return 0
        title, body = text_battery_low(await self._pet_name(tracker_id), pct)
        await self._push(title, body, tag="battery", kind="battery_low", ttl=6 * 3600)
        return 1


@dataclass
class NotifyWorker:
    notifier: Notifier
    interval_s: float = 30.0
    _warned: set[str] = field(default_factory=set)

    async def run(self, stop: asyncio.Event) -> None:
        log.info("push.worker_started", interval_s=self.interval_s, geofence=self.notifier.home_cfg is not None)
        while not stop.is_set():
            try:
                await self.notifier.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # notifikace nesmí shodit ingestor
                key = type(exc).__name__
                if key not in self._warned:
                    self._warned.add(key)
                    log.warning("push.notify_failed", error=repr(exc),
                                hint="chybí tabulky? spusť `docker compose run --rm ingestor init-db`")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_s)
            except TimeoutError:
                pass
