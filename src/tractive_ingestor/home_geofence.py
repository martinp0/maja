"""Geofence „doma / venku (utekla)“ – jedna kruhová domácí zóna z prostředí.

Domov (HOME_LAT/HOME_LON) je citlivý údaj: bere se VÝHRADNĚ z env/.env, nikdy z kódu.
Bez HOME_LAT/HOME_LON je funkce vypnutá (state = "unknown", enabled = false).

Signály (na jeden bod z `positions`):
- Tractive říká „doma“: sensor_used = KNOWN_WIFI nebo vyplněné power_saving_zone_id,
- nebo je bod do HOME_RADIUS_M od domova (haversine).
GPS bod s horší přesností než GEOFENCE_MAX_ACCURACY_M se ignoruje (pokud ho Tractive
neoznačil jako doma) – nepřesný fix nesmí vyvolat útěk.

Debounce/hystereze (čistě podle časů bodů, ne hodin serveru):
- stav se přepne na „away“, až když je pes NEPŘETRŽITĚ venku aspoň GEOFENCE_DEBOUNCE_LEAVE_S
  (první bod venku v čase t0, a pozdější bod – pořád venku – má ts >= t0 + LEAVE_S),
- zpět na „home“ po GEOFENCE_DEBOUNCE_RETURN_S nepřetržitě uvnitř,
- jediný bod na „druhé straně“ běh přeruší (pending se zahodí),
- bod, kde Tractive sám hlásí domácí zónu (WiFi), návrat potvrdí hned
  (GEOFENCE_TRUST_TRACTIVE_HOME, default zapnuto) – WiFi zóna netrpí GPS driftem a tracker
  v power-saving pak už body skoro neposílá, takže by se návrat jinak nemusel potvrdit.
- Přechod se datuje začátkem běhu (`happened_at` = ts prvního bodu na nové straně).

Perzistence: `geofence_state` (checkpoint na tracker: stav, od kdy, poslední zpracovaný bod,
rozběhnutý pending) + `geofence_events` (potvrzené přechody). Vyhodnocení běží v transakci
s advisory lockem na tracker a zpracovává jen body novější než checkpoint, takže ho můžou
klidně volat ingestor (periodicky) i API (na request) – přechod se nikdy nezapíše dvakrát
(navíc UNIQUE (tracker_id, event_type, happened_at) + ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)

EARTH_RADIUS_M = 6_371_000.0
HOME_SENSORS = frozenset({"KNOWN_WIFI"})

HOME = "home"
AWAY = "away"
UNKNOWN = "unknown"
LEFT_HOME = "left_home"
RETURNED_HOME = "returned_home"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


@dataclass(slots=True, frozen=True)
class HomeConfig:
    # repr=False: souřadnice domova se nesmí dostat do logu omylem přes repr()
    lat: float = field(repr=False)
    lon: float = field(repr=False)
    radius_m: float = 80.0
    leave_s: float = 120.0
    return_s: float = 60.0
    max_accuracy_m: float = 150.0
    escape_m: float = 300.0
    trust_tractive_home: bool = True
    bootstrap_hours: float = 24.0

    @classmethod
    def build(cls, lat: float | None, lon: float | None, **kw: Any) -> HomeConfig | None:
        """None = funkce vypnutá (chybí souřadnice / nesmysl)."""
        if lat is None or lon is None:
            return None
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
            log.warning("geofence.invalid_home", hint="HOME_LAT/HOME_LON mimo rozsah – geofence vypnutý")
            return None
        return cls(lat=lat, lon=lon, **{k: v for k, v in kw.items() if v is not None})

    def distance_m(self, lat: float, lon: float) -> float:
        return haversine_m(lat, lon, self.lat, self.lon)


@dataclass(slots=True, frozen=True)
class Sample:
    ts: datetime
    lat: float
    lon: float
    accuracy_m: float | None = None
    sensor_used: str | None = None
    zone_id: str | None = None


@dataclass(slots=True, frozen=True)
class Classified:
    inside: bool | None  # None = bod se nepočítá (nepřesný)
    distance_m: float
    by_tractive: bool


def classify(s: Sample, cfg: HomeConfig) -> Classified:
    dist = cfg.distance_m(s.lat, s.lon)
    by_tractive = (s.sensor_used or "").upper() in HOME_SENSORS or bool(s.zone_id)
    if by_tractive:
        return Classified(True, dist, True)
    if s.accuracy_m is not None and s.accuracy_m > cfg.max_accuracy_m:
        return Classified(None, dist, False)
    return Classified(dist <= cfg.radius_m, dist, False)


@dataclass(slots=True, frozen=True)
class Transition:
    event_type: str  # left_home | returned_home
    happened_at: datetime
    distance_m: float


@dataclass(slots=True)
class Checkpoint:
    state: str = UNKNOWN
    since: datetime | None = None
    last_ts: datetime | None = None  # poslední zpracovaný bod (i ignorovaný)
    pending_state: str | None = None
    pending_since: datetime | None = None
    pending_distance_m: float | None = None
    last_distance_m: float | None = None


def step(cp: Checkpoint, samples: list[Sample], cfg: HomeConfig) -> tuple[Checkpoint, list[Transition]]:
    """Čistá funkce: projede body (chronologicky) a vrátí nový checkpoint + potvrzené přechody.

    Body se ts <= cp.last_ts se přeskočí (opakované volání = bez efektu).
    """
    cp = replace(cp)
    out: list[Transition] = []
    for s in sorted(samples, key=lambda x: x.ts):
        if cp.last_ts is not None and s.ts <= cp.last_ts:
            continue
        cp.last_ts = s.ts
        c = classify(s, cfg)
        if c.inside is None:
            continue
        cp.last_distance_m = c.distance_m
        side = HOME if c.inside else AWAY
        if cp.state == UNKNOWN:
            # první použitelný bod jen nastaví výchozí stav – žádná událost
            cp.state, cp.since = side, s.ts
            cp.pending_state = cp.pending_since = cp.pending_distance_m = None
            continue
        if side == cp.state:
            cp.pending_state = cp.pending_since = cp.pending_distance_m = None  # běh přerušen
            continue
        if cp.pending_state != side:
            cp.pending_state, cp.pending_since, cp.pending_distance_m = side, s.ts, c.distance_m
        need = cfg.leave_s if side == AWAY else cfg.return_s
        if side == HOME and c.by_tractive and cfg.trust_tractive_home:
            need = 0.0
        assert cp.pending_since is not None
        if (s.ts - cp.pending_since).total_seconds() >= need:
            out.append(Transition(LEFT_HOME if side == AWAY else RETURNED_HOME, cp.pending_since,
                                  float(cp.pending_distance_m or 0.0)))
            cp.state, cp.since = side, cp.pending_since
            cp.pending_state = cp.pending_since = cp.pending_distance_m = None
    return cp, out


# ============================================================================ Postgres

_CHUNK = 5000


def _lock_key(tracker_id: str) -> str:
    return f"geofence:{tracker_id}"


async def _load_checkpoint(conn: asyncpg.Connection, tracker_id: str, cfg: HomeConfig, now: datetime) -> Checkpoint:
    row = await conn.fetchrow(
        """SELECT state, since, last_ts, pending_state, pending_since, pending_distance_m, last_distance_m
           FROM geofence_state WHERE tracker_id = $1""",
        tracker_id,
    )
    if row is not None:
        return Checkpoint(**dict(row))
    # bootstrap: od poslední události (pokud je), jinak posledních N hodin
    ev = await conn.fetchrow(
        "SELECT event_type, happened_at FROM geofence_events WHERE tracker_id = $1 ORDER BY happened_at DESC LIMIT 1",
        tracker_id,
    )
    if ev is not None:
        st = AWAY if ev["event_type"] == LEFT_HOME else HOME
        return Checkpoint(state=st, since=ev["happened_at"], last_ts=ev["happened_at"])
    return Checkpoint(last_ts=now - timedelta(hours=cfg.bootstrap_hours))


async def evaluate(conn: asyncpg.Connection, tracker_id: str, cfg: HomeConfig,
                   now: datetime | None = None) -> tuple[Checkpoint, list[Transition]]:
    """Inkrementální vyhodnocení jednoho trackeru. MUSÍ běžet uvnitř (zapisovatelné) transakce.

    Vrací (checkpoint, NOVĚ vložené přechody).
    """
    now = now or datetime.now(UTC)
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _lock_key(tracker_id))
    cp = await _load_checkpoint(conn, tracker_id, cfg, now)
    inserted: list[Transition] = []
    while True:
        rows = await conn.fetch(
            """SELECT ts, ST_Y(geom) AS lat, ST_X(geom) AS lon, accuracy_m, sensor_used, power_saving_zone_id
               FROM positions WHERE tracker_id = $1 AND ($2::timestamptz IS NULL OR ts > $2)
               ORDER BY ts LIMIT $3""",
            tracker_id, cp.last_ts, _CHUNK,
        )
        samples = [Sample(r["ts"], r["lat"], r["lon"], r["accuracy_m"], r["sensor_used"], r["power_saving_zone_id"])
                   for r in rows]
        cp, transitions = step(cp, samples, cfg)
        for t in transitions:
            got = await conn.fetchval(
                """INSERT INTO geofence_events (tracker_id, event_type, happened_at, distance_m)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (tracker_id, event_type, happened_at) DO NOTHING RETURNING id""",
                tracker_id, t.event_type, t.happened_at, t.distance_m,
            )
            if got is not None:
                inserted.append(t)
        if len(rows) < _CHUNK:
            break
    await conn.execute(
        """INSERT INTO geofence_state (tracker_id, state, since, last_ts, pending_state, pending_since,
                                       pending_distance_m, last_distance_m, evaluated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
           ON CONFLICT (tracker_id) DO UPDATE SET
               state = EXCLUDED.state, since = EXCLUDED.since, last_ts = EXCLUDED.last_ts,
               pending_state = EXCLUDED.pending_state, pending_since = EXCLUDED.pending_since,
               pending_distance_m = EXCLUDED.pending_distance_m, last_distance_m = EXCLUDED.last_distance_m,
               evaluated_at = now()""",
        tracker_id, cp.state, cp.since, cp.last_ts, cp.pending_state, cp.pending_since,
        cp.pending_distance_m, cp.last_distance_m,
    )
    for t in inserted:
        log.info("geofence.transition", tracker_id=tracker_id, event_type=t.event_type, happened_at=t.happened_at,
                 distance_m=round(t.distance_m))
    return cp, inserted


async def evaluate_pool(pool: asyncpg.Pool, tracker_id: str, cfg: HomeConfig,
                        *, force_read_write: bool = False) -> tuple[Checkpoint, list[Transition]]:
    async with pool.acquire() as conn, conn.transaction():
        if force_read_write:  # API pool je default read-only
            await conn.execute("SET TRANSACTION READ WRITE")
        return await evaluate(conn, tracker_id, cfg)


@dataclass
class GeofenceWorker:
    """Periodické vyhodnocení v ingestoru (pro budoucí notifikace i když appka není otevřená)."""

    pool: asyncpg.Pool
    cfg: HomeConfig
    interval_s: float = 30.0
    _warned: set[str] = field(default_factory=set)

    async def run_once(self) -> int:
        n = 0
        trackers = await self.pool.fetch("SELECT tracker_id FROM trackers ORDER BY tracker_id")
        for r in trackers:
            _, ins = await evaluate_pool(self.pool, r["tracker_id"], self.cfg)
            n += len(ins)
        return n

    async def run(self, stop: asyncio.Event) -> None:
        log.info("geofence.worker_started", radius_m=self.cfg.radius_m, leave_s=self.cfg.leave_s,
                 return_s=self.cfg.return_s)
        while not stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # geofence nesmí shodit ingestor
                key = type(exc).__name__
                if key not in self._warned:
                    self._warned.add(key)
                    log.warning("geofence.eval_failed", error=repr(exc),
                                hint="chybí tabulky? spusť `docker compose run --rm ingestor init-db`")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_s)
            except TimeoutError:
                pass
