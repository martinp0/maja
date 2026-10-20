"""Ukázková fena pro UI bez účtu Tractive.

Žádná reálná poloha domova a žádný payload z Tractive. Trasa je krátká procházka
po cestách v Central Parku v New Yorku kolem Bethesda Fountain (veřejný park).
Souřadnice jsou v kódu schválně: jsou to body v parku, ne adresa.

    python -m tractive_ingestor seed-demo
    python -m tractive_ingestor seed-demo --clear
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .home_geofence import haversine_m

DEMO_TRACKER_ID = "DEMO-LUNA"
DEMO_PET_ID = "demo-luna"
DEMO_PET_NAME = "Luna"
PARK_LABEL = "Central Park, New York (Bethesda Fountain)"

# Veřejný orientační bod uprostřed parku. Není to dům.
ZONE_LAT = 40.77605
ZONE_LON = -73.97122

# Hranice Central Parku (jih, sever, západ, východ). Testy hlídají, že trasa nevyjede ven.
PARK_SOUTH = 40.7644
PARK_NORTH = 40.8006
PARK_WEST = -73.9819
PARK_EAST = -73.9492

# Lomové body po promenádě The Mall a zpátky k terase. Všechno je uvnitř parku.
_VERTICES: tuple[tuple[float, float], ...] = (
    (40.77618, -73.97118),
    (40.77555, -73.97148),
    (40.77485, -73.97162),
    (40.77415, -73.97170),
    (40.77345, -73.97148),
    (40.77295, -73.97085),
    (40.77340, -73.97020),
    (40.77405, -73.97055),
    (40.77455, -73.97115),
)

_PRAGUE = ZoneInfo("Europe/Prague")
_POINT_COUNT = 18


def in_central_park(lat: float, lon: float) -> bool:
    return PARK_SOUTH < lat < PARK_NORTH and PARK_WEST < lon < PARK_EAST


def walk_points(now: datetime) -> list[tuple[datetime, float, float]]:
    """Chronologická trasa za poslední dvě hodiny, poslední bod pár minut zpátky.

    První body jsou v okruhu ukázkové zóny, konec je venku, ale pod 300 m
    (UI ukáže „Venku“, ne „Utekla“).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    coords = _interpolate(_VERTICES, _POINT_COUNT)
    start = now - timedelta(hours=2, minutes=5)
    end = now - timedelta(minutes=4)
    step = (end - start) / (len(coords) - 1)
    return [(start + step * i, lat, lon) for i, (lat, lon) in enumerate(coords)]


def history_events(
    now: datetime, points: list[tuple[datetime, float, float]]
) -> list[tuple[str, datetime, float]]:
    """Pár odchodů a návratů. Dnešní odchod sedí na první bod mimo zónu."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local = now.astimezone(_PRAGUE)
    yesterday = (local - timedelta(days=1)).replace(hour=17, minute=5, second=0, microsecond=0)
    back = yesterday + timedelta(hours=1, minutes=25)
    left_today = next(
        ((ts, lat, lon) for ts, lat, lon in points if haversine_m(lat, lon, ZONE_LAT, ZONE_LON) > 80),
        points[-1],
    )
    return [
        ("left_home", yesterday.astimezone(UTC), 210.0),
        ("returned_home", back.astimezone(UTC), 35.0),
        ("left_home", left_today[0], haversine_m(left_today[1], left_today[2], ZONE_LAT, ZONE_LON)),
    ]


def _interpolate(vertices: tuple[tuple[float, float], ...], count: int) -> list[tuple[float, float]]:
    lengths: list[float] = []
    total = 0.0
    for a, b in zip(vertices, vertices[1:]):
        d = max(haversine_m(a[0], a[1], b[0], b[1]), 0.5)
        lengths.append(d)
        total += d
    out: list[tuple[float, float]] = []
    for i in range(count):
        target = total * (i / (count - 1))
        acc = 0.0
        placed = False
        for (a, b), d in zip(zip(vertices, vertices[1:]), lengths):
            if acc + d >= target or math.isclose(acc + d, total):
                t = 0.0 if d == 0 else min(1.0, max(0.0, (target - acc) / d))
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                placed = True
                break
            acc += d
        if not placed:
            out.append(vertices[-1])
    return out


async def seed_demo(dsn: str, *, clear: bool = False, now: datetime | None = None) -> str:
    """Aplikuje schema.sql a vloží (nebo jen smaže) ukázkovou fenu. Vrátí text na stdout."""
    from .storage import PgStorage

    now = now or datetime.now(UTC)
    storage = PgStorage(dsn, min_size=1, max_size=1)
    await storage.connect()
    try:
        await storage.apply_schema()
        async with storage.pool.acquire() as conn, conn.transaction():
            await _delete_demo(conn)
            if clear:
                return (
                    "Smazaná jen ukázková fena Luna (tracker DEMO-LUNA). "
                    "Ostatní trackery v databázi zůstaly.\n"
                )
            await _insert_demo(conn, now)
    finally:
        await storage.close()
    return _banner()


def _banner() -> str:
    return (
        f"Ukázková fena {DEMO_PET_NAME}, obojek {DEMO_TRACKER_ID}.\n"
        f"Trasa je v {PARK_LABEL} — veřejný park, ne adresa domova a ne data z Tractive.\n"
        "\n"
        "Čárkovaný kruh a seznam odchodů se zapnou těmito řádky v .env\n"
        "(pak znovu `docker compose up -d api`):\n"
        f"HOME_LAT={ZONE_LAT:.5f}\n"
        f"HOME_LON={ZONE_LON:.5f}\n"
        "Jsou to souřadnice fontány v tom parku. S opravdovým obojkem sem dej jen vlastní\n"
        "domov a ten řádek necommituj. Prázdné HOME_LAT/HOME_LON zónu vypne.\n"
    )


async def _delete_demo(conn) -> None:
    tid, pid = DEMO_TRACKER_ID, DEMO_PET_ID
    await conn.execute("DELETE FROM geofence_events WHERE tracker_id = $1", tid)
    await conn.execute("DELETE FROM geofence_state WHERE tracker_id = $1", tid)
    await conn.execute("DELETE FROM notify_state WHERE tracker_id = $1", tid)
    await conn.execute("DELETE FROM commands WHERE tracker_id = $1", tid)
    await conn.execute("DELETE FROM hw_reports WHERE tracker_id = $1", tid)
    await conn.execute("DELETE FROM positions WHERE tracker_id = $1", tid)
    await conn.execute("DELETE FROM health_daily WHERE pet_id = $1", pid)
    await conn.execute("DELETE FROM pets WHERE pet_id = $1 OR tracker_id = $2", pid, tid)
    await conn.execute("DELETE FROM trackers WHERE tracker_id = $1", tid)


async def _insert_demo(conn, now: datetime) -> None:
    points = walk_points(now)
    last_ts, last_lat, last_lon = points[-1]
    left_ts = next(ts for ts, lat, lon in points if haversine_m(lat, lon, ZONE_LAT, ZONE_LON) > 80)
    distance = haversine_m(last_lat, last_lon, ZONE_LAT, ZONE_LON)

    await conn.execute(
        """
        INSERT INTO trackers (
            tracker_id, model_number, state, state_reason, charging_state, battery_state,
            capabilities, updated_at
        ) VALUES ($1, 'DEMO', 'OPERATIONAL', NULL, 'NOT_CHARGING', 'REGULAR',
                  ARRAY['LT','BUZZER','LED'], $2)
        """,
        DEMO_TRACKER_ID,
        now,
    )
    await conn.execute(
        """
        INSERT INTO pets (
            pet_id, tracker_id, name, pet_type, gender, birthday, weight_g, neutered,
            daily_active_minutes_goal
        ) VALUES ($1, $2, $3, 'DOG', 'F', DATE '2019-04-12', 18200, true, 90)
        """,
        DEMO_PET_ID,
        DEMO_TRACKER_ID,
        DEMO_PET_NAME,
    )
    await conn.executemany(
        """
        INSERT INTO positions (
            tracker_id, ts, geom, accuracy_m, speed, altitude_m, sensor_used, origin
        ) VALUES (
            $1, $2, ST_SetSRID(ST_MakePoint($4, $3), 4326), $5, 1.2, 30, 'GPS', 'demo'
        )
        """,
        [
            (DEMO_TRACKER_ID, ts, lat, lon, 12.0)
            for ts, lat, lon in points
        ],
    )
    await conn.execute(
        """
        INSERT INTO hw_reports (
            tracker_id, ts, battery_pct, charging, charging_state, battery_state,
            tracker_state, power_saving, origin
        ) VALUES ($1, $2, 76, false, 'NOT_CHARGING', 'REGULAR', 'OPERATIONAL', false, 'demo')
        """,
        DEMO_TRACKER_ID,
        last_ts,
    )
    day = now.astimezone(_PRAGUE).date()
    await conn.execute(
        """
        INSERT INTO health_daily (
            pet_id, day, synced_at, minutes_active, minutes_goal,
            sleep_day_min, sleep_night_min, sleep_calm_min,
            resting_hr_status, resting_rr_status, bark_status, scratch_status
        ) VALUES ($1, $2, $3, 74, 90, 40, 430, 25, 'NORMAL', 'NORMAL', 'NORMAL', 'INFREQUENT')
        """,
        DEMO_PET_ID,
        day,
        last_ts,
    )
    await conn.execute(
        """
        INSERT INTO commands (tracker_id, command, action, status, attempts, finished_at, updated_at)
        VALUES ($1, 'live_tracking', 'on', 'done', 1, $2, $2)
        """,
        DEMO_TRACKER_ID,
        last_ts - timedelta(minutes=30),
    )
    for event_type, happened_at, meters in history_events(now, points):
        await conn.execute(
            """
            INSERT INTO geofence_events (tracker_id, event_type, happened_at, distance_m, notified_at)
            VALUES ($1, $2, $3, $4, $3)
            """,
            DEMO_TRACKER_ID,
            event_type,
            happened_at,
            meters,
        )
    await conn.execute(
        """
        INSERT INTO geofence_state (
            tracker_id, state, since, last_ts, last_distance_m, evaluated_at
        ) VALUES ($1, 'away', $2, $3, $4, $5)
        """,
        DEMO_TRACKER_ID,
        left_ts,
        last_ts,
        distance,
        now,
    )
