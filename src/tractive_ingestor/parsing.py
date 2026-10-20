"""Normalizace raw Tractive payloadů na naše záznamy.

Tolerantní parsování: chybějící pole => None, nikdy výjimka kvůli tvaru dat.
Raw JSON se vždy ukládá do `events`, takže chybu v mapování jde zpětně
opravit a data přepočítat (reprocess z `events`).

Stav ověření (Fáze 0, fixtures z reálného účtu – tracker TG6C, fw 011.092-TRV6):
- OVĚŘENO: `device_pos_report`, `device_hw_report`, tracker details, pet
  (trackable_object) a health overview (aps-api, camelCase).
  Časy v REST payloadech jsou unix **sekundy**; health používá ISO 8601 UTC string.
- NEOVĚŘENO: odpověď historie `tracker/{id}/positions` a eventy realtime streamu
  (bloky position/hardware/switch převzaté z aiotractive). Viz README → TODO.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from typing import Any

# --------------------------------------------------------------------- records


@dataclass(slots=True)
class PositionRecord:
    tracker_id: str
    ts: datetime  # čas fixu (`time`), ne čas přijetí (`time_rcvd`)
    lat: float
    lon: float
    accuracy_m: float | None  # `pos_uncertainty` (m)
    speed: float | None  # bývá null (doma/WiFi). TODO: jednotka – ověřit při procházce
    altitude_m: float | None  # `altitude` (int, m n. m.), může být null
    # `sensor_used`: volný string. Viděno "KNOWN_WIFI" (doma v power-saving zóně);
    # další známé hodnoty např. GPS, BLE/BLUETOOTH… – NEomezovat na výčet.
    sensor_used: str | None
    power_saving_zone_id: str | None
    origin: str  # "channel" | "pos_report" | "history"
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class HwRecord:
    """`device_hw_report` + stav z tracker details.

    Firmware a model v hw_reportu NEJSOU – jsou v tracker objektu (`fw_version`,
    `model_number`) a ukládají se do `trackers` (viz `tracker_from_raw`).
    """

    tracker_id: str
    ts: datetime
    battery_pct: int | None  # `battery_level`
    charging: bool | None  # z tracker.charging_state (NOT_CHARGING / CHARGING…)
    charging_state: str | None
    battery_state: str | None  # tracker.battery_state (REGULAR…)
    tracker_state: str | None  # tracker.state (OPERATIONAL…)
    power_saving: bool | None  # tracker.state_reason == POWER_SAVING
    power_saving_zone_id: str | None
    clip_mounted_state: str | None
    temperature_state: str | None
    hw_status: str | None
    report_id: str | None
    origin: str
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class TrackerRecord:
    tracker_id: str
    model_number: str | None  # např. "TG6C"
    fw_version: str | None  # např. "011.092-TRV6-009-e7ce01"
    hw_edition: str | None
    capabilities: list[str] = field(default_factory=list)
    state: str | None = None  # OPERATIONAL
    state_reason: str | None = None  # POWER_SAVING
    charging_state: str | None = None  # NOT_CHARGING
    battery_state: str | None = None  # REGULAR
    power_saving_zone_id: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class PetRecord:
    pet_id: str
    tracker_id: str | None
    name: str | None
    pet_type: str | None  # DOG
    breed_ids: list[str] = field(default_factory=list)
    gender: str | None = None  # F / M
    birthday: date | None = None  # z unix ts (UTC)
    weight_g: int | None = None  # `details.weight` je v gramech
    neutered: bool | None = None
    home_lat: float | None = None
    home_lon: float | None = None
    daily_active_minutes_goal: int | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class HealthRecord:
    pet_id: str
    day: date
    synced_at: datetime | None  # `activityDataSyncedAt`
    minutes_active: int | None
    minutes_goal: int | None
    hourly_distribution: list[int] | None  # 24 hodnot (minuty aktivity po hodinách)
    sleep_day_min: int | None
    sleep_night_min: int | None
    sleep_calm_min: int | None
    resting_hr_status: str | None
    resting_rr_status: str | None
    bark_status: str | None
    scratch_status: str | None


@dataclass(slots=True)
class SwitchState:
    tracker_id: str
    switch: str  # live_tracking | buzzer_control | led_control
    active: bool | None
    remaining_s: int | None


# --------------------------------------------------------------------- helpers


def payload_hash(payload: Any) -> str:
    """Stabilní hash raw payloadu – klíč pro idempotentní insert do `events`."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def to_datetime(value: Any) -> datetime | None:
    """Unix ts / ISO string -> aware UTC datetime.

    REST payloady posílají sekundy (ověřeno); heuristika pro ms zůstává pro jistotu
    (stream/historie zatím neověřené). ISO string může mít nanosekundy
    (`2026-...T00:11:31.681516621Z`) – zkrátí se na mikrosekundy.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        ts = float(value)
        if ts > 1e12:  # milisekundy
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip().replace("Z", "+00:00")
        # Python < 3.11 neumí > 6 desetinných míst; zkrátíme ručně
        if "." in s:
            head, _, rest = s.partition(".")
            digits = "".join(ch for ch in rest if ch.isdigit())
            tail = rest[len(digits):]
            s = f"{head}.{digits[:6]}{tail}" if digits else head + tail
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def _latlon(obj: dict[str, Any], key: str = "latlong") -> tuple[float, float] | None:
    ll = obj.get(key)  # ověřeno: [lat, lon]
    if isinstance(ll, list | tuple) and len(ll) >= 2:
        try:
            return float(ll[0]), float(ll[1])
        except (TypeError, ValueError):
            return None
    return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    f = _num(v)
    return None if f is None else int(round(f))


def _str(v: Any) -> str | None:
    return None if v is None or v == "" else str(v)


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _str_list(v: Any) -> list[str]:
    return [str(x) for x in v if x is not None] if isinstance(v, list | tuple) else []


def _bool_or_none(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


# --------------------------------------------------------------------- trackable (pet + tracker)


def tracker_from_raw(tracker_id: str, raw: dict[str, Any] | None) -> TrackerRecord:
    """Tracker details (ověřeno na TG6C): model_number, fw_version, capabilities, state…"""
    raw = raw or {}
    return TrackerRecord(
        tracker_id=str(raw.get("_id") or raw.get("hw_id") or tracker_id),
        model_number=_str(raw.get("model_number")),
        fw_version=_str(raw.get("fw_version")),
        hw_edition=_str(raw.get("hw_edition")),
        capabilities=_str_list(raw.get("capabilities")),
        state=_str(raw.get("state")),
        state_reason=_str(raw.get("state_reason")),
        charging_state=_str(raw.get("charging_state")),
        battery_state=_str(raw.get("battery_state")),
        power_saving_zone_id=_str(raw.get("power_saving_zone_id")),
        raw=raw,
    )


def pet_from_raw(pet_id: str, raw: dict[str, Any] | None, tracker_id: str | None = None) -> PetRecord:
    """Trackable object `pet` (ověřeno): details{name, pet_type, breed_ids, gender,
    birthday (unix s), weight (g), neutered, activity_settings{...}}, home_location [lat, lon]."""
    raw = raw or {}
    details = _dict(raw.get("details"))
    activity = _dict(details.get("activity_settings"))
    home = _latlon(raw, "home_location")
    bday = to_datetime(details.get("birthday"))
    return PetRecord(
        pet_id=str(raw.get("_id") or pet_id),
        tracker_id=_str(raw.get("device_id")) or tracker_id,
        name=_str(details.get("name")),
        pet_type=_str(details.get("pet_type")),
        breed_ids=_str_list(details.get("breed_ids")),
        gender=_str(details.get("gender")),
        birthday=bday.date() if bday else None,
        weight_g=_int(details.get("weight")),
        neutered=_bool_or_none(details.get("neutered")),
        home_lat=home[0] if home else None,
        home_lon=home[1] if home else None,
        daily_active_minutes_goal=_int(activity.get("daily_active_minutes_goal")),
        raw=raw,
    )


# --------------------------------------------------------------------- channel events
# TODO(fáze 0): tvary eventů ze streamu jsou zatím jen podle aiotractive – reálné
# fixtures chybí (Mája byla doma v power-saving zóně). Zachytit při procházce.

SWITCH_KEYS = ("live_tracking", "buzzer_control", "led_control")  # aiotractive


def event_type(event: dict[str, Any]) -> str:
    """Typ eventu ze streamu (`message`): tracker_status, health_overview, ..."""
    return str(event.get("message") or "unknown")


def position_from_event(event: dict[str, Any]) -> PositionRecord | None:
    """Blok `position` v tracker eventu (aiotractive: latlong, accuracy, sensor_used, time).

    Pro jistotu bere i názvy z `device_pos_report` (pos_uncertainty, power_saving_zone_id).
    """
    tracker_id = event.get("tracker_id")
    pos = event.get("position")
    if not tracker_id or not isinstance(pos, dict):
        return None
    ll = _latlon(pos)
    ts = to_datetime(pos.get("time"))
    if ll is None or ts is None:
        return None
    return PositionRecord(
        tracker_id=str(tracker_id),
        ts=ts,
        lat=ll[0],
        lon=ll[1],
        accuracy_m=_num(pos.get("accuracy", pos.get("pos_uncertainty"))),
        speed=_num(pos.get("speed")),
        altitude_m=_num(pos.get("altitude")),
        sensor_used=_str(pos.get("sensor_used")),
        power_saving_zone_id=_str(pos.get("power_saving_zone_id")),
        origin="channel",
        raw=pos,
    )


def hw_from_event(event: dict[str, Any]) -> HwRecord | None:
    """Blok `hardware` + stav trackeru (aiotractive: hardware.battery_level, hardware.time,
    hardware.power_saving_zone_id, charging_state, tracker_state, tracker_state_reason)."""
    tracker_id = event.get("tracker_id")
    hw = event.get("hardware")
    if not tracker_id or not isinstance(hw, dict):
        return None
    ts = to_datetime(hw.get("time")) or datetime.now(UTC)
    charging_state = _str(event.get("charging_state"))
    reason = event.get("tracker_state_reason")
    return HwRecord(
        tracker_id=str(tracker_id),
        ts=ts,
        battery_pct=_int(hw.get("battery_level")),
        charging=None if charging_state is None else charging_state == "CHARGING",
        charging_state=charging_state,
        battery_state=_str(event.get("battery_state")),
        tracker_state=_str(event.get("tracker_state")),
        power_saving=None if reason is None and "tracker_state" not in event else reason == "POWER_SAVING",
        power_saving_zone_id=_str(hw.get("power_saving_zone_id")),
        clip_mounted_state=_str(hw.get("clip_mounted_state")),
        temperature_state=_str(hw.get("temperature_state")),
        hw_status=_str(hw.get("hw_status")),
        report_id=_str(hw.get("report_id")),
        origin="channel",
        raw=hw,
    )


def switches_from_event(event: dict[str, Any]) -> list[SwitchState]:
    tracker_id = event.get("tracker_id")
    if not tracker_id:
        return []
    out = []
    for key in SWITCH_KEYS:
        data = event.get(key)
        if isinstance(data, dict):
            out.append(
                SwitchState(
                    tracker_id=str(tracker_id),
                    switch=key,
                    active=data.get("active"),
                    remaining_s=data.get("remaining"),
                )
            )
    return out


# --------------------------------------------------------------------- health


def _status(obj: Any) -> str | None:
    return _str(_dict(obj).get("status"))


def health_from_payload(
    data: dict[str, Any], *, fallback_day: date, pet_id: str | None = None, tz: tzinfo | None = None
) -> HealthRecord | None:
    """Health overview (aps-api, ověřeno; stream event `health_overview` má obsah asi v `content`).

    Tvar: petId, activityDataSyncedAt (ISO UTC), activity{minutesActive, minutesGoal,
    hourlyDistribution[24]}, sleep{minutesDaySleep, minutesNightSleep, minutesCalm},
    bark/restingHeartRate/restingRespiratoryRate/scratch {status, dayOffset},
    healthAlerts{unseenCount}, rest (null), associatedData, separationPhaseStatus, stateTimeline.

    `day` = lokální datum (`tz`, default UTC) z `activityDataSyncedAt`; bez něj `fallback_day`.
    TODO(fáze 0): potvrdit přiřazení ke dni – overview je „dnešní“ souhrn, ale po půlnoci
    může `activityDataSyncedAt` ještě ukazovat na včerejší sync; `dayOffset` u bark/HR/scratch
    (-1/0) naznačuje, že jednotlivé metriky se mohou vztahovat k různým dnům.
    """
    data = data.get("content", data) if isinstance(data, dict) else {}
    if not isinstance(data, dict):
        return None
    pid = data.get("petId") or pet_id
    if not pid:
        return None
    activity = _dict(data.get("activity"))
    sleep = _dict(data.get("sleep"))
    synced_at = to_datetime(data.get("activityDataSyncedAt"))
    day = synced_at.astimezone(tz or UTC).date() if synced_at else fallback_day
    hourly = activity.get("hourlyDistribution")
    hourly_list = [_int(x) or 0 for x in hourly] if isinstance(hourly, list) else None
    return HealthRecord(
        pet_id=str(pid),
        day=day,
        synced_at=synced_at,
        minutes_active=_int(activity.get("minutesActive")),
        minutes_goal=_int(activity.get("minutesGoal")),
        hourly_distribution=hourly_list,
        sleep_day_min=_int(sleep.get("minutesDaySleep")),
        sleep_night_min=_int(sleep.get("minutesNightSleep")),
        sleep_calm_min=_int(sleep.get("minutesCalm")),
        resting_hr_status=_status(data.get("restingHeartRate")),
        resting_rr_status=_status(data.get("restingRespiratoryRate")),
        bark_status=_status(data.get("bark")),
        scratch_status=_status(data.get("scratch")),
    )


# --------------------------------------------------------------------- REST


def position_from_pos_report(tracker_id: str, report: dict[str, Any]) -> PositionRecord | None:
    """`device_pos_report` (ověřeno): time, time_rcvd (s), latlong [lat, lon], pos_uncertainty (m),
    speed (null), altitude (m), sensor_used ("KNOWN_WIFI"…), power_saving_zone_id, report_id."""
    if not isinstance(report, dict):
        return None
    ll = _latlon(report)
    ts = to_datetime(report.get("time")) or to_datetime(report.get("time_rcvd"))
    if ll is None or ts is None:
        return None
    return PositionRecord(
        tracker_id=tracker_id,
        ts=ts,
        lat=ll[0],
        lon=ll[1],
        accuracy_m=_num(report.get("pos_uncertainty")),
        speed=_num(report.get("speed")),
        altitude_m=_num(report.get("altitude")),
        sensor_used=_str(report.get("sensor_used")),
        power_saving_zone_id=_str(report.get("power_saving_zone_id")),
        origin="pos_report",
        raw=report,
    )


def hw_from_report(tracker_id: str, report: dict[str, Any], details: dict[str, Any] | None = None) -> HwRecord | None:
    """`device_hw_report` (ověřeno): time (s), battery_level (%), clip_mounted_state,
    temperature_state, hw_status, report_id, power_saving_zone_id.

    Nabíjení/stav jsou v tracker details (charging_state, battery_state, state, state_reason),
    proto se volitelně předávají. Firmware v hw_reportu není (je v `trackers.fw_version`).
    """
    if not report or not isinstance(report, dict):
        return None
    details = details or {}
    charging_state = _str(details.get("charging_state"))
    reason = details.get("state_reason")
    return HwRecord(
        tracker_id=tracker_id,
        ts=to_datetime(report.get("time")) or datetime.now(UTC),
        battery_pct=_int(report.get("battery_level")),
        charging=None if charging_state is None else charging_state == "CHARGING",
        charging_state=charging_state,
        battery_state=_str(details.get("battery_state")),
        tracker_state=_str(details.get("state")),
        power_saving=None if reason is None else reason == "POWER_SAVING",
        power_saving_zone_id=_str(report.get("power_saving_zone_id")),
        clip_mounted_state=_str(report.get("clip_mounted_state")),
        temperature_state=_str(report.get("temperature_state")),
        hw_status=_str(report.get("hw_status")),
        report_id=_str(report.get("report_id")),
        origin="hw_report",
        raw=report,
    )


def parse_history(tracker_id: str, payload: Any) -> list[PositionRecord]:
    """Odpověď `tracker/{id}/positions`.

    TODO(fáze 0): tvar odpovědi pro format=json_segments stále NEMÁME zachycený
    (check běžel, když byla Mája doma v power-saving zóně). Parser je tolerantní:
    přijme seznam bodů nebo seznam segmentů (seznamů bodů), bod je dict s `time`
    + `latlong` (+ volitelně speed, pos_uncertainty/accuracy, sensor_used, alt/altitude).
    Po zachycení fixtures z procházky zúžit na reálný tvar.
    """
    points: list[dict[str, Any]] = []

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            if "latlong" in node:
                points.append(node)
            else:
                for v in node.values():
                    if isinstance(v, list | dict):
                        collect(v)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(payload)
    out: list[PositionRecord] = []
    for p in points:
        ll = _latlon(p)
        ts = to_datetime(p.get("time"))
        if ll is None or ts is None:
            continue
        out.append(
            PositionRecord(
                tracker_id=tracker_id,
                ts=ts,
                lat=ll[0],
                lon=ll[1],
                accuracy_m=_num(p.get("pos_uncertainty", p.get("accuracy"))),
                speed=_num(p.get("speed")),
                altitude_m=_num(p.get("alt", p.get("altitude"))),
                sensor_used=_str(p.get("sensor_used")),
                power_saving_zone_id=_str(p.get("power_saving_zone_id")),
                origin="history",
                raw=p,
            )
        )
    return out
