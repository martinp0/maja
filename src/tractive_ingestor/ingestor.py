"""Orchestrace: start -> login -> discovery -> snapshot -> backfill -> stream -> shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from aiotractive.exceptions import TractiveError, UnauthorizedError

from . import parsing
from .channel import ChannelConsumer, FatalChannelError
from .client import TrackableInfo, TractiveClient
from .commands import CommandStore, CommandWorker, PgCommandStore, parse_command
from .config import Settings
from .health import HealthServer, HealthState
from .home_geofence import GeofenceWorker
from .push import Notifier, NotifyWorker, WebPushSender
from .rules import GeofenceEngine
from .storage import PgStorage, Storage, make_storage

log = structlog.get_logger(__name__)


class ConfigError(RuntimeError):
    pass


class AuthError(RuntimeError):
    pass


class FixtureRecorder:
    """Fáze 0: ukládá raw payloady jako JSONL (fixtures/<source>.jsonl).

    POZOR: obsahuje přesnou polohu psa = domova. Necommitovat (je v .gitignore).
    """

    def __init__(self, directory: Path | None) -> None:
        self._dir = directory
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    def record(self, source: str, payload: Any, **meta: Any) -> None:
        if self._dir is None:
            return
        line = json.dumps(
            {"captured_at": datetime.now(UTC).isoformat(), "source": source, **meta, "payload": payload},
            default=str,
            ensure_ascii=False,
        )
        with (self._dir / f"{source}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class Ingestor:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.tz = ZoneInfo(settings.local_tz)
        self.client: TractiveClient | None = None
        self.storage: Storage = make_storage(settings.dsn, min_size=settings.db_pool_min, max_size=settings.db_pool_max)
        self.fixtures = FixtureRecorder(settings.fixtures_dir)
        self.geofence = GeofenceEngine()  # TODO(Fáze 1): načíst zóny z DB
        self.trackables: list[TrackableInfo] = []
        self._stop = asyncio.Event()
        self.health = HealthState()
        self.health_server: HealthServer | None = None
        self.command_store: CommandStore | None = None  # jen s DB (PgStorage)

    # ------------------------------------------------------------------ lifecycle

    def request_stop(self) -> None:
        log.info("ingestor.stop_requested")
        self._stop.set()

    async def _login(self) -> TractiveClient:
        missing = self.s.missing_credentials()
        if missing:
            raise ConfigError(f"Chybí proměnné prostředí: {', '.join(missing)} (viz .env.example)")
        client = TractiveClient(self.s.tractive_email, self.s.tractive_password.get_secret_value())
        await client.start()
        try:
            await client.login()
        except UnauthorizedError as exc:
            await client.close()
            raise AuthError(
                "Tractive odmítl přihlášení. Zkontroluj e-mail/heslo; účty přihlášené přes Apple/Google nefungují."
            ) from exc
        except TractiveError:
            await client.close()
            raise
        return client

    async def _start_health_server(self) -> None:
        if self.s.health_port <= 0:
            log.info("health.disabled")
            return
        server = HealthServer(
            self.health, self.s.health_host, self.s.health_port, ready_max_age_s=self.s.health_ready_max_age_s
        )
        await server.start()  # obsazený port -> OSError -> služba nenastartuje (radši hlasitě)
        self.health_server = server

    async def run(self) -> None:
        """Hlavní běh služby (blokuje do SIGINT/SIGTERM)."""
        # Health server startuje jako první: /healthz odpovídá i během loginu a
        # backfillu (může trvat minuty), /readyz je do připojení kanálu 503.
        await self._start_health_server()
        try:
            if self.s.demo_mode:
                # Bez účtu Tractive: proces zůstane běžet, ať Docker nerecykluje kontejner
                # kvůli odmítnutému loginu. Polohy ber jen z DB (seed-demo).
                log.warning(
                    "ingestor.demo_mode",
                    hint="DEMO_MODE=1: přihlášení k Tractive se přeskakuje. V mapě jsou jen data z databáze.",
                )
                self.health.set_phase("streaming")
                self.health.mark_unauthenticated("demo mode: no Tractive session")
                await self._stop.wait()
                return
            self.health.set_phase("login")
            try:
                self.client = await self._login()
            except Exception as exc:
                self.health.mark_unauthenticated(f"login failed: {exc}")
                raise
            assert self.client.auth is not None
            self.health.mark_authenticated(self.client.auth.user_id)
            self.health.keepalive_source = self.client.last_keepalive_at
            await self._run_logged_in()
        finally:
            self.health.set_phase("stopping")
            if self.health_server is not None:
                await self.health_server.stop()
                self.health_server = None

    async def _run_logged_in(self) -> None:
        assert self.client is not None
        try:
            await self.storage.connect()
            if isinstance(self.storage, PgStorage):
                self.command_store = PgCommandStore(self.storage.pool)
            self.health.set_phase("discovery")
            await self._discover()
            await self._snapshot()
            self.health.set_phase("backfill")
            await self.backfill_all()

            consumer = ChannelConsumer(
                self.client,
                self.handle_event,
                backoff_base_s=self.s.reconnect_backoff_base_s,
                backoff_max_s=self.s.reconnect_backoff_max_s,
                max_silence_s=self.s.channel_max_silence_s,
                on_reconnect=self.backfill_all,
                health=self.health,
            )
            self.health.set_phase("streaming")
            tasks = [
                asyncio.create_task(consumer.run(self._stop), name="channel"),
                asyncio.create_task(self._health_loop(), name="health_poll")  # Tractive health data, ne /healthz,
            ]
            if self.command_store is not None:
                worker = CommandWorker(
                    self.command_store,
                    self.client,
                    poll_interval_s=self.s.command_poll_interval_s,
                    defer_timeout_s=self.s.command_defer_timeout_s,
                    defer_retry_s=self.s.command_defer_retry_s,
                    max_attempts=self.s.command_max_attempts,
                    confirm_timeout_s=self.s.command_confirm_timeout_s,
                )
                tasks.append(asyncio.create_task(worker.run(self._stop), name="commands"))
            else:
                log.info("commands.disabled", hint="bez DATABASE_URL není fronta povelů")
            home_cfg = self.s.home_config()
            if isinstance(self.storage, PgStorage) and home_cfg is not None and self.s.geofence_eval_interval_s > 0:
                gw = GeofenceWorker(self.storage.pool, home_cfg, interval_s=self.s.geofence_eval_interval_s)
                tasks.append(asyncio.create_task(gw.run(self._stop), name="geofence"))
            else:
                log.info("geofence.disabled", hint="HOME_LAT/HOME_LON nenastavené nebo bez DB")
            push_cfg = self.s.push_config()
            if isinstance(self.storage, PgStorage) and push_cfg is not None and self.s.push_eval_interval_s > 0:
                notifier = Notifier(self.storage.pool, WebPushSender(push_cfg), home_cfg,
                                    max_event_age_s=self.s.push_max_event_age_s)
                nw = NotifyWorker(notifier, interval_s=self.s.push_eval_interval_s)
                tasks.append(asyncio.create_task(nw.run(self._stop), name="push"))
            else:
                log.info("push.disabled", hint="VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY nenastavené nebo bez DB")
            # TODO(Fáze 1): heartbeat do DB (pro monitoring bez přístupu k HTTP portu)
            stop_task = asyncio.create_task(self._stop.wait())
            done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
            self._stop.set()
            for t in tasks:
                t.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, FatalChannelError):
                    raise AuthError(str(r)) from r
                if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                    raise r
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        log.info("ingestor.shutdown")
        if self.client is not None:
            await self.client.close()
        await self.storage.close()

    # ------------------------------------------------------------------ startup steps

    async def _discover(self) -> None:
        assert self.client is not None
        self.trackables = await self.client.list_trackables()
        if not self.trackables:
            log.warning("ingestor.no_trackables", hint="Účet nemá psa s přiřazeným trackerem (nebo je jen sdílený)")
        for t in self.trackables:
            self.fixtures.record("trackable", {"pet": t.pet_raw, "tracker": t.tracker_raw})
            await self.storage.upsert_trackable(t)
        log.info("ingestor.discovered", count=len(self.trackables), names=[t.name for t in self.trackables])

    async def _snapshot(self) -> None:
        """Aktuální stav přes REST (poslední poloha + baterie), než se rozjede stream."""
        assert self.client is not None
        for t in self.trackables:
            try:
                pos = await self.client.get_pos_report(t.tracker_id)
                hw = await self.client.get_hw_report(t.tracker_id)
            except TractiveError as exc:
                log.warning("ingestor.snapshot_failed", tracker_id=t.tracker_id, error=repr(exc))
                continue
            self.fixtures.record("pos_report", pos, tracker_id=t.tracker_id)
            self.fixtures.record("hw_report", hw, tracker_id=t.tracker_id)
            await self.storage.insert_event("pos_report", "device_pos_report", pos, tracker_id=t.tracker_id)
            await self.storage.insert_event("hw_report", "device_hw_report", hw, tracker_id=t.tracker_id)
            if rec := parsing.position_from_pos_report(t.tracker_id, pos):
                await self.storage.upsert_positions([rec])
            if hw_rec := parsing.hw_from_report(t.tracker_id, hw, t.tracker_raw):
                await self.storage.upsert_hw(hw_rec)

    # ------------------------------------------------------------------ backfill

    async def backfill_all(self) -> None:
        for t in self.trackables:
            try:
                await self.backfill_tracker(t.tracker_id)
            except TractiveError as exc:
                log.warning("backfill.failed", tracker_id=t.tracker_id, error=repr(exc))

    async def backfill_tracker(self, tracker_id: str) -> int:
        """Doplní historii pozic od poslední uložené (nebo BACKFILL_DAYS zpět) do teď."""
        assert self.client is not None
        now = datetime.now(UTC)
        floor = now - timedelta(days=self.s.backfill_days)
        last = await self.storage.last_position_ts(tracker_id)
        start = max(last, floor) if last else floor
        chunk = timedelta(hours=self.s.backfill_chunk_hours)
        total = 0
        cursor = start
        while cursor < now and not self._stop.is_set():
            end = min(cursor + chunk, now)
            payload = await self.client.get_positions(tracker_id, cursor.timestamp(), end.timestamp())
            self.fixtures.record("history", payload, tracker_id=tracker_id, time_from=cursor.isoformat(), time_to=end.isoformat())
            await self.storage.insert_event("history", "tracker_positions", payload, tracker_id=tracker_id)
            records = parsing.parse_history(tracker_id, payload)
            total += await self.storage.upsert_positions(records)
            cursor = end
            await asyncio.sleep(1.0)  # šetrně k rate-limitu
        log.info("backfill.done", tracker_id=tracker_id, since=start.isoformat(), positions=total)
        return total

    # ------------------------------------------------------------------ health

    async def _health_loop(self) -> None:
        while not self._stop.is_set():
            await self.poll_health()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.s.health_poll_interval_s)

    async def poll_health(self) -> None:
        assert self.client is not None
        today = datetime.now(self.tz).date()
        for t in self.trackables:
            try:
                data = await self.client.get_health_overview(t.pet_id)
            except TractiveError as exc:
                log.warning("health.failed", pet_id=t.pet_id, error=repr(exc))
                continue
            if not data:
                log.info("health.unavailable", pet_id=t.pet_id, hint="400/403 – neaktivní Premium nebo nepodporováno")
                continue
            self.fixtures.record("health", data, pet_id=t.pet_id)
            await self.storage.insert_event("health", "health_overview", data, pet_id=t.pet_id)
            if rec := parsing.health_from_payload(data, fallback_day=today, pet_id=t.pet_id, tz=self.tz):
                await self.storage.upsert_health(rec, data)

    # ------------------------------------------------------------------ realtime

    async def handle_event(self, event: dict[str, Any]) -> None:
        """Router eventů ze streamu. Nejdřív raw, pak normalizace."""
        etype = parsing.event_type(event)
        tracker_id = event.get("tracker_id")
        self.fixtures.record("channel", event, message=etype)
        await self.storage.insert_event("channel", etype, event, tracker_id=tracker_id)

        if etype == "health_overview":
            today = datetime.now(self.tz).date()
            if rec := parsing.health_from_payload(event, fallback_day=today, tz=self.tz):
                await self.storage.upsert_health(rec, event.get("content", event))
            return

        if not tracker_id:
            log.debug("channel.unrouted", message=etype)
            return

        # tracker_status (a příbuzné) nesou bloky position / hardware / switch states
        # TODO(fáze 0): ověřit všechny hodnoty `message` a jestli position/hardware
        # chodí i jako samostatné typy; router je zatím podle obsahu, ne podle typu.
        if pos := parsing.position_from_event(event):
            await self.storage.upsert_positions([pos])
            for zone, transition in self.geofence.evaluate(pos):
                log.info("geofence.transition", zone=zone.name, transition=transition.value, tracker_id=pos.tracker_id)
                # TODO(Fáze 1): zapsat zone_events + push
        if hw := parsing.hw_from_event(event):
            await self.storage.upsert_hw(hw)
        if switches := parsing.switches_from_event(event):
            await self.storage.update_switches(
                str(tracker_id),
                {s.switch: {"active": s.active, "remaining": s.remaining_s, "at": time.time()} for s in switches},
            )
            if self.command_store is not None:
                for sw in switches:
                    if sw.active is not None and await self.command_store.confirm(str(tracker_id), sw.switch, sw.active):
                        log.info("commands.confirmed", tracker_id=tracker_id, switch=sw.switch, active=sw.active)


# ---------------------------------------------------------------------- CLI helpers


async def check(settings: Settings) -> None:
    """`python -m tractive_ingestor check` – Fáze 0: login + výpis psů + REST snapshot, bez DB."""
    ing = Ingestor(settings)
    ing.storage = make_storage(None)
    ing.client = await ing._login()
    try:
        assert ing.client.auth is not None
        print(f"✔ Přihlášeno: user_id={ing.client.auth.user_id}, token vyprší za {ing.client.auth.expires_in_s or 0:.0f} s")
        await ing._discover()
        for t in ing.trackables:
            print(f"  • {t.name or '?'}  pet_id={t.pet_id}  tracker_id={t.tracker_id}")
        await ing._snapshot()
        await ing.poll_health()
        if settings.fixtures_dir:
            print(f"Raw payloady uloženy do {settings.fixtures_dir}/*.jsonl")
    finally:
        await ing._shutdown()


async def init_db(settings: Settings) -> None:
    """`python -m tractive_ingestor init-db` – aplikuje schema.sql."""
    from .storage import PgStorage

    if not settings.dsn:
        raise ConfigError("DATABASE_URL není nastavené")
    storage = PgStorage(settings.dsn)
    await storage.connect()
    try:
        await storage.apply_schema()
    finally:
        await storage.close()


async def enqueue(settings: Settings, tracker_id: str, command: str, action: str) -> str:
    """`python -m tractive_ingestor enqueue <tracker_id> <live|buzzer|led> [on|off]` – zařadí povel do DB.

    Neposílá nic do Tractive; odešle ho worker běžící služby (jediný klient = jeden token).
    """
    try:
        cmd, act = parse_command(command, action)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if not settings.dsn:
        raise ConfigError("DATABASE_URL není nastavené (fronta povelů je v DB)")
    storage = PgStorage(settings.dsn, min_size=1, max_size=1)
    await storage.connect()
    try:
        known = await storage.pool.fetchval("SELECT EXISTS (SELECT 1 FROM trackers WHERE tracker_id = $1)", tracker_id)
        if not known:
            log.warning("commands.unknown_tracker", tracker_id=tracker_id, hint="tracker zatím není v DB (překlep?)")
        cmd_id = await PgCommandStore(storage.pool).enqueue(tracker_id, cmd, act)
    finally:
        await storage.close()
    log.info("commands.enqueued", command_id=cmd_id, tracker_id=tracker_id, command=cmd.value, action=act)
    print(cmd_id)
    return cmd_id
