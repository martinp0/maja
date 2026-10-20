"""Přístup API do DB (asyncpg). Vše read-only, výjimkou je zařazení povelu a geofence checkpoint.

- Pool má `default_transaction_read_only=on` -> omylem napsaný UPDATE/DELETE v API selže
  už v Postgresu. Zařazení povelu si zápis povolí explicitně (`SET TRANSACTION READ WRITE`)
  a vkládá jen řádek `queued` do `commands` – Tractive volá výhradně ingestor (CommandWorker).
- `ApiStore` je Protocol -> testy podstrčí in-memory implementaci (bez DB).
- Výstupy jsou obyčejné dicty (JSON-friendly); geometrie se rozbaluje v SQL na lat/lng.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from tractive_ingestor.client import Command
from tractive_ingestor.commands import COMMAND_ALIASES, PgCommandStore
from tractive_ingestor.home_geofence import HomeConfig, evaluate_pool
from tractive_ingestor.push import Sender, delete_subscription, send_to_all, upsert_subscription

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)

# Command.value -> krátký název pro UI/API (live_tracking -> live …)
COMMAND_SHORT: dict[str, str] = {}
for _alias, _cmd in COMMAND_ALIASES.items():
    COMMAND_SHORT.setdefault(_cmd.value, _alias)


class StoreUnavailable(RuntimeError):
    """DB není nakonfigurovaná nebo se k ní nelze připojit -> 503."""


class ApiStore(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...
    async def tracker_exists(self, tracker_id: str) -> bool: ...
    async def list_pets(self) -> list[dict[str, Any]]: ...
    async def list_trackers(self) -> list[dict[str, Any]]: ...
    async def latest(self, tracker_id: str) -> dict[str, Any]: ...
    async def positions(self, tracker_id: str, since: datetime, limit: int) -> list[dict[str, Any]]: ...
    async def health(self, tracker_id: str, days: int) -> dict[str, Any]: ...
    async def list_commands(self, tracker_id: str, limit: int) -> list[dict[str, Any]]: ...
    async def enqueue_command(self, tracker_id: str, command: Command, action: str) -> dict[str, Any]: ...
    async def in_home_zone(self, tracker_id: str) -> bool | None: ...
    async def default_tracker(self) -> str | None: ...
    async def latest_position(self, tracker_id: str) -> dict[str, Any] | None: ...
    async def geofence_evaluate(self, tracker_id: str, cfg: HomeConfig) -> dict[str, Any] | None: ...
    async def geofence_events(self, tracker_id: str, limit: int) -> list[dict[str, Any]]: ...
    async def push_subscribe(self, endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> int: ...
    async def push_unsubscribe(self, endpoint: str) -> bool: ...
    async def push_count(self) -> int: ...
    async def push_send_all(self, sender: Sender, payload: str) -> dict[str, int]: ...
    async def webauthn_list(self) -> list[dict[str, Any]]: ...
    async def webauthn_get(self, credential_id: bytes) -> dict[str, Any] | None: ...
    async def webauthn_add(self, cred: dict[str, Any]) -> dict[str, Any]: ...
    async def webauthn_touch(self, credential_id: bytes, sign_count: int) -> None: ...
    async def webauthn_delete(self, id_: int) -> bool: ...
    async def webauthn_count(self) -> int: ...


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=lambda v: json.dumps(v, default=str), decoder=json.loads, schema="pg_catalog")


def _command_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["id"] = str(d["id"])
    d["command_type"] = d["command"]  # live_tracking / buzzer_control / led_control
    d["command"] = COMMAND_SHORT.get(d["command"], d["command"])  # live / buzzer / led
    return d


_COMMAND_COLS = """id, tracker_id, command, action, status, attempts, last_error,
                   created_at, updated_at, next_attempt_at, sent_at, finished_at"""


class PgApiStore:
    def __init__(self, dsn: str | None, *, min_size: int = 1, max_size: int = 3) -> None:
        self._dsn = dsn
        self._min = min_size
        self._max = max_size
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Líné připojení – API naběhne i když DB ještě startuje; další request to zkusí znovu."""
        if self._pool is not None:
            return
        if not self._dsn:
            raise StoreUnavailable("DATABASE_URL není nastavené")
        async with self._lock:
            if self._pool is not None:
                return
            import asyncpg  # lazy

            try:
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._min,
                    max_size=self._max,
                    init=_init_conn,
                    statement_cache_size=0 if ":6543" in self._dsn else 100,
                    command_timeout=15,
                    server_settings={"default_transaction_read_only": "on", "application_name": "tractive-api"},
                )
            except (OSError, asyncpg.PostgresError) as exc:
                log.warning("api.db_connect_failed", error=repr(exc))
                raise StoreUnavailable("Databáze je nedostupná") from exc
            log.info("api.db_connected", pool_max=self._max)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _p(self) -> asyncpg.Pool:
        await self.connect()
        assert self._pool is not None
        return self._pool

    # ------------------------------------------------------------------ read

    async def ping(self) -> bool:
        pool = await self._p()
        return await pool.fetchval("SELECT 1") == 1

    async def tracker_exists(self, tracker_id: str) -> bool:
        pool = await self._p()
        return bool(await pool.fetchval("SELECT EXISTS (SELECT 1 FROM trackers WHERE tracker_id = $1)", tracker_id))

    async def list_pets(self) -> list[dict[str, Any]]:
        # home_location záměrně NEvracíme (adresa domova)
        pool = await self._p()
        rows = await pool.fetch(
            """
            SELECT pet_id, tracker_id, name, pet_type, breed_ids, gender, birthday, weight_g, neutered,
                   daily_active_minutes_goal
            FROM pets ORDER BY name NULLS LAST, pet_id
            """
        )
        return [dict(r) for r in rows]

    async def list_trackers(self) -> list[dict[str, Any]]:
        pool = await self._p()
        rows = await pool.fetch(
            """
            SELECT t.tracker_id, t.model_number, t.fw_version, t.capabilities, t.state, t.state_reason,
                   t.charging_state, t.battery_state, t.power_saving_zone_id, t.updated_at,
                   pet.pet_id, pet.name AS pet_name,
                   h.ts AS hw_ts, h.battery_pct, h.power_saving,
                   p.ts AS last_position_ts, p.sensor_used
            FROM trackers t
            LEFT JOIN LATERAL (SELECT pet_id, name FROM pets WHERE pets.tracker_id = t.tracker_id
                               ORDER BY pet_id LIMIT 1) pet ON true
            LEFT JOIN LATERAL (SELECT ts, battery_pct, power_saving FROM hw_reports
                               WHERE tracker_id = t.tracker_id ORDER BY ts DESC LIMIT 1) h ON true
            LEFT JOIN LATERAL (SELECT ts, sensor_used FROM positions
                               WHERE tracker_id = t.tracker_id ORDER BY ts DESC LIMIT 1) p ON true
            ORDER BY t.tracker_id
            """
        )
        return [dict(r) for r in rows]

    async def latest(self, tracker_id: str) -> dict[str, Any]:
        pool = await self._p()
        pos = await pool.fetchrow(
            """
            SELECT ts, ST_Y(geom) AS lat, ST_X(geom) AS lng, accuracy_m, speed, altitude_m,
                   sensor_used, power_saving_zone_id, origin
            FROM positions WHERE tracker_id = $1 ORDER BY ts DESC LIMIT 1
            """,
            tracker_id,
        )
        hw = await pool.fetchrow(
            """
            SELECT ts, battery_pct, charging, charging_state, battery_state, tracker_state, power_saving,
                   power_saving_zone_id
            FROM hw_reports WHERE tracker_id = $1 ORDER BY ts DESC LIMIT 1
            """,
            tracker_id,
        )
        tr = await pool.fetchrow(
            """
            SELECT state, state_reason, charging_state, battery_state, power_saving_zone_id, last_switches, updated_at
            FROM trackers WHERE tracker_id = $1
            """,
            tracker_id,
        )
        return {
            "tracker_id": tracker_id,
            "position": dict(pos) if pos else None,
            "hw": dict(hw) if hw else None,
            "tracker": dict(tr) if tr else None,
        }

    async def positions(self, tracker_id: str, since: datetime, limit: int) -> list[dict[str, Any]]:
        # Nejnovějších N bodů od `since`, vrácené chronologicky (pro polyline).
        pool = await self._p()
        rows = await pool.fetch(
            """
            SELECT ts, lat, lng, accuracy_m, sensor_used FROM (
                SELECT ts, ST_Y(geom) AS lat, ST_X(geom) AS lng, accuracy_m, sensor_used
                FROM positions WHERE tracker_id = $1 AND ts >= $2
                ORDER BY ts DESC LIMIT $3
            ) recent ORDER BY ts
            """,
            tracker_id,
            since,
            limit,
        )
        return [dict(r) for r in rows]

    async def health(self, tracker_id: str, days: int) -> dict[str, Any]:
        pool = await self._p()
        pet = await pool.fetchrow(
            "SELECT pet_id, name, daily_active_minutes_goal FROM pets WHERE tracker_id = $1 ORDER BY pet_id LIMIT 1",
            tracker_id,
        )
        if pet is None:
            return {"tracker_id": tracker_id, "pet_id": None, "latest": None, "days": []}
        rows = await pool.fetch(
            """
            SELECT day, synced_at, minutes_active, minutes_goal, hourly_distribution,
                   sleep_day_min, sleep_night_min, sleep_calm_min,
                   resting_hr_status, resting_rr_status, bark_status, scratch_status, updated_at
            FROM health_daily WHERE pet_id = $1 ORDER BY day DESC LIMIT $2
            """,
            pet["pet_id"],
            days,
        )
        out = [dict(r) for r in rows]
        for d in out:
            if d["minutes_goal"] is None:
                d["minutes_goal"] = pet["daily_active_minutes_goal"]
        return {
            "tracker_id": tracker_id,
            "pet_id": pet["pet_id"],
            "pet_name": pet["name"],
            "daily_active_minutes_goal": pet["daily_active_minutes_goal"],
            "latest": out[0] if out else None,
            "days": out,
        }

    async def list_commands(self, tracker_id: str, limit: int) -> list[dict[str, Any]]:
        pool = await self._p()
        rows = await pool.fetch(
            f"SELECT {_COMMAND_COLS} FROM commands WHERE tracker_id = $1 ORDER BY created_at DESC LIMIT $2",
            tracker_id,
            limit,
        )
        return [_command_row(r) for r in rows]

    async def in_home_zone(self, tracker_id: str) -> bool | None:
        pool = await self._p()
        state = await PgCommandStore(pool).tracker_state(tracker_id)
        return state.in_home_zone()

    async def default_tracker(self) -> str | None:
        pool = await self._p()
        return await pool.fetchval("SELECT tracker_id FROM trackers ORDER BY tracker_id LIMIT 1")

    async def latest_position(self, tracker_id: str) -> dict[str, Any] | None:
        pool = await self._p()
        row = await pool.fetchrow(
            """SELECT ts, ST_Y(geom) AS lat, ST_X(geom) AS lng, accuracy_m, sensor_used, power_saving_zone_id
               FROM positions WHERE tracker_id = $1 ORDER BY ts DESC LIMIT 1""",
            tracker_id,
        )
        return dict(row) if row else None

    async def geofence_events(self, tracker_id: str, limit: int) -> list[dict[str, Any]]:
        import asyncpg

        pool = await self._p()
        try:
            rows = await pool.fetch(
                """SELECT id, tracker_id, event_type, happened_at, distance_m, created_at
                   FROM geofence_events WHERE tracker_id = $1 ORDER BY happened_at DESC, id DESC LIMIT $2""",
                tracker_id,
                limit,
            )
        except asyncpg.UndefinedTableError:
            log.warning("api.geofence_schema_missing", hint="docker compose run --rm ingestor init-db")
            return []
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ write (jediné zápisy API)

    async def geofence_evaluate(self, tracker_id: str, cfg: HomeConfig) -> dict[str, Any] | None:
        """Inkrementálně dopočítá stav (idempotentní, advisory lock) a vrátí checkpoint.

        Zapisuje jen geofence_state/geofence_events. None = chybí tabulky (neproběhl init-db).
        """
        import asyncpg

        pool = await self._p()
        try:
            cp, _ = await evaluate_pool(pool, tracker_id, cfg, force_read_write=True)
        except asyncpg.UndefinedTableError:
            log.warning("api.geofence_schema_missing", hint="docker compose run --rm ingestor init-db")
            return None
        return {
            "state": cp.state,
            "since": cp.since,
            "last_ts": cp.last_ts,
            "pending_state": cp.pending_state,
            "pending_since": cp.pending_since,
        }


    async def enqueue_command(self, tracker_id: str, command: Command, action: str) -> dict[str, Any]:
        pool = await self._p()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION READ WRITE")
            row = await conn.fetchrow(
                f"INSERT INTO commands (tracker_id, command, action) VALUES ($1, $2, $3) RETURNING {_COMMAND_COLS}",
                tracker_id,
                command.value,
                action,
            )
        assert row is not None
        return _command_row(row)

    # ------------------------------------------------------------------ push (zápis jen do push_subscriptions)

    async def push_subscribe(self, endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> int:
        pool = await self._p()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION READ WRITE")
            return await upsert_subscription(conn, endpoint, p256dh, auth, user_agent)

    async def push_unsubscribe(self, endpoint: str) -> bool:
        pool = await self._p()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION READ WRITE")
            return await delete_subscription(conn, endpoint)

    async def push_count(self) -> int:
        pool = await self._p()
        return int(await pool.fetchval("SELECT count(*) FROM push_subscriptions"))

    async def push_send_all(self, sender: Sender, payload: str) -> dict[str, int]:
        pool = await self._p()
        res = await send_to_all(pool, sender, payload, ttl=600, urgency="high", read_write=True)
        return res.as_dict()

    # ------------------------------------------------------------------ passkeys (zápis jen do webauthn_credentials)

    _WA_COLS = "id, credential_id, sign_count, transports, name, aaguid, backed_up, created_at, last_used_at"

    async def _rw(self, sql: str, *args: Any) -> Any:
        pool = await self._p()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION READ WRITE")
            return await conn.fetchrow(sql, *args)

    async def webauthn_list(self) -> list[dict[str, Any]]:
        pool = await self._p()
        rows = await pool.fetch(f"SELECT {self._WA_COLS} FROM webauthn_credentials ORDER BY created_at")
        return [dict(r) for r in rows]

    async def webauthn_get(self, credential_id: bytes) -> dict[str, Any] | None:
        pool = await self._p()
        r = await pool.fetchrow(f"SELECT {self._WA_COLS}, public_key FROM webauthn_credentials WHERE credential_id = $1",
                                credential_id)
        return dict(r) if r else None

    async def webauthn_add(self, cred: dict[str, Any]) -> dict[str, Any]:
        r = await self._rw(
            f"""INSERT INTO webauthn_credentials (credential_id, public_key, sign_count, transports, name, aaguid, backed_up)
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING {self._WA_COLS}""",
            cred["credential_id"], cred["public_key"], cred["sign_count"], cred.get("transports"), cred.get("name"),
            cred.get("aaguid"), cred.get("backed_up"))
        return dict(r)

    async def webauthn_touch(self, credential_id: bytes, sign_count: int) -> None:
        await self._rw("UPDATE webauthn_credentials SET sign_count = $2, last_used_at = now() WHERE credential_id = $1",
                       credential_id, sign_count)

    async def webauthn_delete(self, id_: int) -> bool:
        return await self._rw("DELETE FROM webauthn_credentials WHERE id = $1 RETURNING id", id_) is not None

    async def webauthn_count(self) -> int:
        pool = await self._p()
        return int(await pool.fetchval("SELECT count(*) FROM webauthn_credentials"))
