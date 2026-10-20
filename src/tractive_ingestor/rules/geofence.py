"""Geofence – vyhodnocení vstupu/výstupu ze zón. STUB (Fáze 1).

Cílový návrh (koncept kap. 4A):
- zóny jsou v tabulce `zones` (PostGIS): kruh = Point + radius_m, nebo Polygon,
- hystereze (`hysteresis_m`) a minimální přesnost (`min_accuracy_m`) proti GPS driftu,
- výsledek = řádek v `zone_events` + push (APNs) – push zatím mimo scope.

Produkčně bude lepší vyhodnocovat v DB (ST_DWithin / ST_Contains nad geography),
tady je čistě pythonní verze pro kruhy, aby šla logika testovat bez DB.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from ..parsing import PositionRecord

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class Transition(str, Enum):
    ENTER = "enter"
    EXIT = "exit"


@dataclass(slots=True)
class Zone:
    id: str
    name: str
    kind: str  # home | park | forbidden | escape | custom
    lat: float
    lon: float
    radius_m: float
    hysteresis_m: float = 30.0
    min_accuracy_m: float = 100.0


@dataclass
class GeofenceEngine:
    zones: list[Zone] = field(default_factory=list)
    # (tracker_id, zone_id) -> True = uvnitř
    _state: dict[tuple[str, str], bool] = field(default_factory=dict)

    def evaluate(self, pos: PositionRecord) -> list[tuple[Zone, Transition]]:
        """Vrátí přechody vyvolané touto polohou. Stav drží v paměti.

        TODO(Fáze 1):
        - načíst zóny z DB (a reagovat na změny),
        - inicializovat `_state` z posledních `zone_events` po restartu,
        - polygonové zóny (shapely nebo dotaz do PostGIS),
        - zapisovat `zone_events` a spouštět notifikace,
        - zohlednit, že BT/WIFI polohy doma mají jinou přesnost než GPS.
        """
        transitions: list[tuple[Zone, Transition]] = []
        for zone in self.zones:
            if pos.accuracy_m is not None and pos.accuracy_m > zone.min_accuracy_m:
                continue  # nepřesný fix ignorujeme
            dist = haversine_m(pos.lat, pos.lon, zone.lat, zone.lon)
            key = (pos.tracker_id, zone.id)
            inside = self._state.get(key)
            if inside is None:
                self._state[key] = dist <= zone.radius_m  # první poloha jen nastaví stav
                continue
            if inside and dist > zone.radius_m + zone.hysteresis_m:
                self._state[key] = False
                transitions.append((zone, Transition.EXIT))
            elif not inside and dist < max(zone.radius_m - zone.hysteresis_m, 0):
                self._state[key] = True
                transitions.append((zone, Transition.ENTER))
        return transitions
