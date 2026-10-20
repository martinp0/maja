"""Ukládání do Postgresu/Supabase přes asyncpg.

- Pool se vytváří až v `connect()` (import nic nepřipojuje).
- Všechny zápisy jsou idempotentní (ON CONFLICT), takže backfill i reconnect
  můžou stejná data poslat vícekrát bez duplicit.
- `NullStorage` = běh bez DB (Fáze 0: jen logovat / zachytávat fixtures).
"""

from __future__ import annotations

import json
from datetime import datetime
from importlib import resources
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from .parsing import HealthRecord, HwRecord, PositionRecord, payload_hash, pet_from_raw, tracker_from_raw

if TYPE_CHECKING:
    import asyncpg

    from .client import TrackableInfo

log = structlog.get_logger(__name__)


class Storage(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def insert_event(
        self, source: str, event_type: str, payload: Any, *, tracker_id: str | None = None, pet_id: str | None = None
    ) -> bool: ...
    async def upsert_trackable(self, info: TrackableInfo) -> None: ...
    async def upsert_positions(self, records: list[PositionRecord]) -> int: ...
    async def upsert_hw(self, rec: HwRecord) -> None: ...
    async def upsert_health(self, rec: HealthRecord, raw: dict[str, Any]) -> None: ...
    async def update_switches(self, tracker_id: str, switches: dict[str, Any]) -> None: ...
    async def last_position_ts(self, tracker_id: str) -> datetime | None: ...


# ============================================================================ Postgres


async def _init_conn(conn: asyncpg.Connection) -> None:
    # jsonb <-> Python dict
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=lambda v: json.dumps(v, default=str), decoder=json.loads, schema="pg_catalog")


class PgStorage:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 5) -> None:
        self._dsn = dsn
        self._min = min_size
        self._max = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        import asyncpg  # lazy

        # Supabase transaction pooler (port 6543, PgBouncer) nepodporuje prepared statements.
        statement_cache_size = 0 if ":6543" in self._dsn else 100
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min,
            max_size=self._max,
            init=_init_conn,
            statement_cache_size=statement_cache_size,
            command_timeout=30,
        )
        log.info("db.connected", pool_max=self._max)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PgStorage.connect() nebylo zavoláno")
        return self._pool

    async def apply_schema(self) -> None:
        sql = resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")
        async with self.pool.acquire() as conn:
            await conn.execute(sql)
        log.info("db.schema_applied")

    # ------------------------------------------------------------------ raw

    async def insert_event(
        self, source: str, event_type: str, payload: Any, *, tracker_id: str | None = None, pet_id: str | None = None
    ) -> bool:
        """Vloží raw payload. Vrací False, pokud identický payload už v DB je."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO events (source, event_type, tracker_id, pet_id, payload, payload_hash)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            ON CONFLICT (payload_hash) DO NOTHING
            RETURNING id
            """,
            source,
            event_type,
            tracker_id,
            pet_id,
            payload,
            payload_hash(payload),
        )
        return row is not None

    # ------------------------------------------------------------------ entities

    async def upsert_trackable(self, info: TrackableInfo) -> None:
        tr = tracker_from_raw(info.tracker_id, info.tracker_raw)
        pet = pet_from_raw(info.pet_id, info.pet_raw, tracker_id=info.tracker_id)
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO trackers (tracker_id, model_number, fw_version, hw_edition, capabilities,
                                      state, state_reason, charging_state, battery_state,
                                      power_saving_zone_id, raw)
                VALUES ($1, $2, $3, $4, $5::text[], $6, $7, $8, $9, $10, $11::jsonb)
                ON CONFLICT (tracker_id) DO UPDATE SET
                    model_number = COALESCE(EXCLUDED.model_number, trackers.model_number),
                    fw_version = COALESCE(EXCLUDED.fw_version, trackers.fw_version),
                    hw_edition = COALESCE(EXCLUDED.hw_edition, trackers.hw_edition),
                    capabilities = COALESCE(EXCLUDED.capabilities, trackers.capabilities),
                    state = EXCLUDED.state,
                    state_reason = EXCLUDED.state_reason,
                    charging_state = EXCLUDED.charging_state,
                    battery_state = EXCLUDED.battery_state,
                    power_saving_zone_id = EXCLUDED.power_saving_zone_id,
                    raw = EXCLUDED.raw, updated_at = now()
                """,
                info.tracker_id,
                tr.model_number,
                tr.fw_version,
                tr.hw_edition,
                tr.capabilities or None,
                tr.state,
                tr.state_reason,
                tr.charging_state,
                tr.battery_state,
                tr.power_saving_zone_id,
                info.tracker_raw,
            )
            await conn.execute(
                """
                INSERT INTO pets (pet_id, tracker_id, name, pet_type, breed_ids, gender, birthday,
                                  weight_g, neutered, home_location, daily_active_minutes_goal, raw)
                VALUES ($1, $2, $3, $4, $5::text[], $6, $7, $8, $9,
                        CASE WHEN $10::float8 IS NULL OR $11::float8 IS NULL THEN NULL
                             ELSE ST_SetSRID(ST_MakePoint($11::float8, $10::float8), 4326) END,
                        $12, $13::jsonb)
                ON CONFLICT (pet_id) DO UPDATE SET
                    tracker_id = EXCLUDED.tracker_id,
                    name = COALESCE(EXCLUDED.name, pets.name),
                    pet_type = COALESCE(EXCLUDED.pet_type, pets.pet_type),
                    breed_ids = COALESCE(EXCLUDED.breed_ids, pets.breed_ids),
                    gender = COALESCE(EXCLUDED.gender, pets.gender),
                    birthday = COALESCE(EXCLUDED.birthday, pets.birthday),
                    weight_g = COALESCE(EXCLUDED.weight_g, pets.weight_g),
                    neutered = COALESCE(EXCLUDED.neutered, pets.neutered),
                    home_location = COALESCE(EXCLUDED.home_location, pets.home_location),
                    daily_active_minutes_goal = COALESCE(EXCLUDED.daily_active_minutes_goal,
                                                         pets.daily_active_minutes_goal),
                    raw = EXCLUDED.raw, updated_at = now()
                """,
                info.pet_id,
                info.tracker_id,
                info.name or pet.name,
                pet.pet_type,
                pet.breed_ids or None,
                pet.gender,
                pet.birthday,
                pet.weight_g,
                pet.neutered,
                pet.home_lat,
                pet.home_lon,
                pet.daily_active_minutes_goal,
                info.pet_raw,
            )

    # ------------------------------------------------------------------ normalized

    async def upsert_positions(self, records: list[PositionRecord]) -> int:
        if not records:
            return 0
        await self.pool.executemany(
            """
            INSERT INTO positions (tracker_id, ts, geom, accuracy_m, speed, altitude_m, sensor_used,
                                   power_saving_zone_id, origin, raw)
            VALUES ($1, $2, ST_SetSRID(ST_MakePoint($4, $3), 4326), $5, $6, $7, $8, $9, $10, $11::jsonb)
            ON CONFLICT (tracker_id, ts) DO UPDATE SET
                accuracy_m  = COALESCE(EXCLUDED.accuracy_m, positions.accuracy_m),
                speed       = COALESCE(EXCLUDED.speed, positions.speed),
                altitude_m  = COALESCE(EXCLUDED.altitude_m, positions.altitude_m),
                sensor_used = COALESCE(EXCLUDED.sensor_used, positions.sensor_used),
                power_saving_zone_id = COALESCE(EXCLUDED.power_saving_zone_id, positions.power_saving_zone_id),
                raw         = COALESCE(EXCLUDED.raw, positions.raw)
            """,
            [
                (
                    r.tracker_id,
                    r.ts,
                    r.lat,
                    r.lon,
                    r.accuracy_m,
                    r.speed,
                    r.altitude_m,
                    r.sensor_used,
                    r.power_saving_zone_id,
                    r.origin,
                    r.raw,
                )
                for r in records
            ],
        )
        return len(records)

    async def upsert_hw(self, rec: HwRecord) -> None:
        await self.pool.execute(
            """
            INSERT INTO hw_reports (tracker_id, ts, battery_pct, charging, charging_state, battery_state,
                                    tracker_state, power_saving, power_saving_zone_id, clip_mounted_state,
                                    temperature_state, hw_status, report_id, origin, raw)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb)
            ON CONFLICT (tracker_id, ts) DO UPDATE SET
                battery_pct = COALESCE(EXCLUDED.battery_pct, hw_reports.battery_pct),
                charging = COALESCE(EXCLUDED.charging, hw_reports.charging),
                charging_state = COALESCE(EXCLUDED.charging_state, hw_reports.charging_state),
                battery_state = COALESCE(EXCLUDED.battery_state, hw_reports.battery_state),
                tracker_state = COALESCE(EXCLUDED.tracker_state, hw_reports.tracker_state),
                power_saving = COALESCE(EXCLUDED.power_saving, hw_reports.power_saving),
                power_saving_zone_id = COALESCE(EXCLUDED.power_saving_zone_id, hw_reports.power_saving_zone_id),
                clip_mounted_state = COALESCE(EXCLUDED.clip_mounted_state, hw_reports.clip_mounted_state),
                temperature_state = COALESCE(EXCLUDED.temperature_state, hw_reports.temperature_state),
                hw_status = COALESCE(EXCLUDED.hw_status, hw_reports.hw_status),
                report_id = COALESCE(EXCLUDED.report_id, hw_reports.report_id),
                raw = COALESCE(EXCLUDED.raw, hw_reports.raw)
            """,
            rec.tracker_id,
            rec.ts,
            rec.battery_pct,
            rec.charging,
            rec.charging_state,
            rec.battery_state,
            rec.tracker_state,
            rec.power_saving,
            rec.power_saving_zone_id,
            rec.clip_mounted_state,
            rec.temperature_state,
            rec.hw_status,
            rec.report_id,
            rec.origin,
            rec.raw,
        )

    async def upsert_health(self, rec: HealthRecord, raw: dict[str, Any]) -> None:
        await self.pool.execute(
            """
            INSERT INTO health_daily (pet_id, day, synced_at, minutes_active, minutes_goal, hourly_distribution,
                                      sleep_day_min, sleep_night_min, sleep_calm_min, resting_hr_status,
                                      resting_rr_status, bark_status, scratch_status, raw)
            VALUES ($1, $2, $3, $4, $5, $6::integer[], $7, $8, $9, $10, $11, $12, $13, $14::jsonb)
            ON CONFLICT (pet_id, day) DO UPDATE SET
                synced_at = COALESCE(EXCLUDED.synced_at, health_daily.synced_at),
                minutes_active = EXCLUDED.minutes_active,
                minutes_goal = EXCLUDED.minutes_goal,
                hourly_distribution = COALESCE(EXCLUDED.hourly_distribution, health_daily.hourly_distribution),
                sleep_day_min = EXCLUDED.sleep_day_min,
                sleep_night_min = EXCLUDED.sleep_night_min,
                sleep_calm_min = EXCLUDED.sleep_calm_min,
                resting_hr_status = COALESCE(EXCLUDED.resting_hr_status, health_daily.resting_hr_status),
                resting_rr_status = COALESCE(EXCLUDED.resting_rr_status, health_daily.resting_rr_status),
                bark_status = COALESCE(EXCLUDED.bark_status, health_daily.bark_status),
                scratch_status = COALESCE(EXCLUDED.scratch_status, health_daily.scratch_status),
                raw = EXCLUDED.raw,
                updated_at = now()
            """,
            rec.pet_id,
            rec.day,
            rec.synced_at,
            rec.minutes_active,
            rec.minutes_goal,
            rec.hourly_distribution,
            rec.sleep_day_min,
            rec.sleep_night_min,
            rec.sleep_calm_min,
            rec.resting_hr_status,
            rec.resting_rr_status,
            rec.bark_status,
            rec.scratch_status,
            raw,
        )

    async def update_switches(self, tracker_id: str, switches: dict[str, Any]) -> None:
        await self.pool.execute(
            """
            INSERT INTO trackers (tracker_id, last_switches) VALUES ($1, $2::jsonb)
            ON CONFLICT (tracker_id) DO UPDATE
                SET last_switches = trackers.last_switches || EXCLUDED.last_switches, updated_at = now()
            """,
            tracker_id,
            switches,
        )
        # párování s `commands` (sent -> done) dělá ingestor přes commands.PgCommandStore.confirm

    async def last_position_ts(self, tracker_id: str) -> datetime | None:
        return await self.pool.fetchval("SELECT max(ts) FROM positions WHERE tracker_id = $1", tracker_id)


# ============================================================================ bez DB


class NullStorage:
    """Nic neukládá, jen loguje. Pro Fázi 0 / ladění bez databáze."""

    async def connect(self) -> None:
        log.warning("db.disabled", hint="DATABASE_URL není nastavené – data se neukládají")

    async def close(self) -> None:
        return None

    async def insert_event(self, source: str, event_type: str, payload: Any, **_: Any) -> bool:
        log.debug("event", source=source, event_type=event_type)
        return True

    async def upsert_trackable(self, info: TrackableInfo) -> None:
        tr = tracker_from_raw(info.tracker_id, info.tracker_raw)
        log.info(
            "trackable",
            pet_id=info.pet_id,
            tracker_id=info.tracker_id,
            name=info.name,
            model=tr.model_number,
            fw=tr.fw_version,
            state=tr.state,
            state_reason=tr.state_reason,
        )

    async def upsert_positions(self, records: list[PositionRecord]) -> int:
        if records:
            last = records[-1]
            log.info("positions", count=len(records), last_ts=last.ts.isoformat(), origin=last.origin,
                     sensor=last.sensor_used, accuracy_m=last.accuracy_m)
        return len(records)

    async def upsert_hw(self, rec: HwRecord) -> None:
        log.info("hw", tracker_id=rec.tracker_id, battery_pct=rec.battery_pct, charging=rec.charging,
                 power_saving=rec.power_saving)

    async def upsert_health(self, rec: HealthRecord, raw: dict[str, Any]) -> None:
        log.info("health", pet_id=rec.pet_id, day=str(rec.day), minutes_active=rec.minutes_active,
                 minutes_goal=rec.minutes_goal, sleep_night_min=rec.sleep_night_min)

    async def update_switches(self, tracker_id: str, switches: dict[str, Any]) -> None:
        log.info("switches", tracker_id=tracker_id, switches=switches)

    async def last_position_ts(self, tracker_id: str) -> datetime | None:
        return None


def make_storage(dsn: str | None, *, min_size: int = 1, max_size: int = 5) -> Storage:
    return PgStorage(dsn, min_size=min_size, max_size=max_size) if dsn else NullStorage()
