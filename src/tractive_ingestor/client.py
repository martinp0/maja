"""Tenký wrapper nad `aiotractive`.

Proč wrapper a ne přímo aiotractive:
- jedno místo pro re-login (refresh token neexistuje),
- vrací "raw" dicty, které ukládáme 1:1 do `events` (API se může změnit),
- izoluje zbytek kódu od interních detailů knihovny.

Co dělá aiotractive sama (v1.3.0, ověřeno ve zdrojácích):
- `POST https://graph.tractive.com/4/auth/token` (platform_email/platform_token),
  token drží v paměti a před každým requestem se znovu přihlásí, pokud do
  `expires_at` zbývá < 1 h,
- HTTP 429 -> retry s náhodným exponenciálním zpožděním (retry_count=3),
- realtime kanál `POST https://channel.tractive.com/3/channel` (chunked stream),
  zahazuje handshake/keep-alive a má vlastní 60s keep-alive watchdog.

Co přidáváme my: vynucený re-login při 401 a jednotné API pro ingestor.
"""

from __future__ import annotations

import enum
import time
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import aiohttp
import structlog
from aiotractive import Tractive
from aiotractive.channel import Channel
from aiotractive.exceptions import TractiveError, UnauthorizedError

log = structlog.get_logger(__name__)

T = TypeVar("T")

# Formát historie pozic pro `tracker/{id}/positions`. aiotractive vyžaduje parametr
# `fmt`, ale nedokumentuje hodnoty. "json_segments" používá webová appka / komunita.
# TODO(fáze 0): odpověď zatím NEZACHYCENA (první `check` běžel, když byl pes doma
# v power-saving zóně). Zachytit při procházce a podle toho upravit parsing.parse_history().
HISTORY_FORMAT = "json_segments"


class Command(str, enum.Enum):
    """Přepínače trackeru. Hodnoty = segment URL `tracker/{id}/command/{value}/on|off`."""

    LIVE = "live_tracking"  # vyprší samo po ~300 s
    BUZZER = "buzzer_control"
    LED = "led_control"


@dataclass(slots=True)
class AuthInfo:
    user_id: str
    expires_at: float | None  # unix ts; None pokud ho odpověď neobsahuje

    @property
    def expires_in_s(self) -> float | None:
        return None if self.expires_at is None else self.expires_at - time.time()


@dataclass(slots=True)
class TrackableInfo:
    """Pes (trackable_object) + jeho tracker. Raw payloady necháváme celé."""

    pet_id: str
    tracker_id: str
    name: str | None
    pet_raw: dict[str, Any]
    tracker_raw: dict[str, Any]


class TractiveClient:
    """Jediný klient Tractive v celém systému. Vytvářet uvnitř běžící event loop."""

    def __init__(self, email: str, password: str, *, timeout_s: int = 15) -> None:
        self._email = email
        self._password = password
        self._timeout_s = timeout_s
        self._session: aiohttp.ClientSession | None = None
        self._tractive: Tractive | None = None
        self.auth: AuthInfo | None = None
        self._channel: Channel | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Vytvoří HTTP session a instanci aiotractive (lazy – nic se nevolá v __init__)."""
        if self._tractive is not None:
            return
        self._session = aiohttp.ClientSession()
        self._tractive = Tractive(
            self._email,
            self._password,
            session=self._session,
            timeout=self._timeout_s,
        )

    async def close(self) -> None:
        if self._tractive is not None:
            await self._tractive.close()  # session je naše, aiotractive ji nezavře
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._tractive = None
        self._session = None

    async def __aenter__(self) -> TractiveClient:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def tractive(self) -> Tractive:
        if self._tractive is None:
            raise RuntimeError("TractiveClient.start() nebylo zavoláno")
        return self._tractive

    # ------------------------------------------------------------------ auth

    async def login(self) -> AuthInfo:
        """Přihlásí se (nebo vrátí platný token). Vyhazuje UnauthorizedError při špatných údajích."""
        await self.start()
        creds = await self.tractive.authenticate()
        if not creds:
            raise TractiveError("Auth odpověď neobsahuje JSON (neočekávaný Content-Type)")
        self.auth = AuthInfo(user_id=str(creds["user_id"]), expires_at=creds.get("expires_at"))
        log.info("tractive.login_ok", user_id=self.auth.user_id, expires_in_s=self.auth.expires_in_s)
        return self.auth

    async def force_relogin(self) -> AuthInfo:
        """Zahodí token a přihlásí se znovu (pro 401 uprostřed platnosti tokenu).

        TODO: aiotractive nemá veřejné API pro invalidaci tokenu; sahá se na
        privátní atributy `_api._user_credentials/_auth_headers`. Při upgradu
        aiotractive ověřit, že stále existují.
        """
        api = getattr(self.tractive, "_api", None)
        if api is not None:
            api._user_credentials = None  # noqa: SLF001
            api._auth_headers = None  # noqa: SLF001
        return await self.login()

    async def _call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Zavolá API; při 401 jednou zkusí re-login a request zopakuje."""
        try:
            return await fn()
        except UnauthorizedError:
            log.warning("tractive.unauthorized_relogin")
            await self.force_relogin()
            return await fn()

    # ------------------------------------------------------------------ data

    async def list_trackables(self) -> list[TrackableInfo]:
        """Psi s přiřazeným trackerem (trackable_objects + tracker details).

        Ověřeno: tracker details nesou `model_number` (TG6C), `fw_version`, `capabilities`,
        `state`/`state_reason`, `charging_state`, `battery_state`; pet nese `details`
        (name, pet_type, breed_ids, gender, birthday, weight v g…) a `home_location`.

        Sdílené trackery bez `details` aiotractive přeskakuje (viz otevřená otázka
        v konceptu: sdílení psa mezi účty v rodině).
        """
        items = await self._call(self.tractive.async_fetch_trackables)
        out: list[TrackableInfo] = []
        for t in items:
            try:
                name = t.name
            except (KeyError, TypeError):
                name = None
            out.append(
                TrackableInfo(
                    pet_id=t.pet_id,
                    tracker_id=t.tracker_id,
                    name=name,
                    pet_raw=t.pet_details,
                    tracker_raw=t.tracker_details,
                )
            )
        return out

    async def get_tracker_details(self, tracker_id: str) -> dict[str, Any]:
        return await self._call(lambda: self.tractive.tracker(tracker_id).details())

    async def get_pos_report(self, tracker_id: str) -> dict[str, Any]:
        """`GET device_pos_report/{id}` – poslední poloha.

        Ověřeno na reálném payloadu (TG6C): `time`, `time_rcvd` (unix **sekundy**),
        `latlong` [lat, lon], `pos_uncertainty` (m), `speed` (může být null),
        `altitude` (m, int), `sensor_used` (volný string – viděno "KNOWN_WIFI"; dál
        GPS, BLE… neomezovat na výčet), `pos_status`, `nearby_user_id`,
        `power_saving_zone_id`, `report_id`.
        """
        return await self._call(lambda: self.tractive.tracker(tracker_id).pos_report())

    async def get_hw_report(self, tracker_id: str) -> dict[str, Any]:
        """`GET device_hw_report/{id}/` – baterie, power-saving zóna.

        Ověřeno: `time` (unix sekundy), `battery_level` (%), `power_saving_zone_id`,
        `report_id`, `clip_mounted_state`, `temperature_state`, `hw_status` (vše null-able).
        Firmware ani model tady NEJSOU – jsou v tracker details (`fw_version`,
        `model_number`), stejně jako `charging_state`/`battery_state`/`state`.
        """
        return await self._call(lambda: self.tractive.tracker(tracker_id).hw_info())

    async def get_positions(
        self, tracker_id: str, time_from: float, time_to: float, fmt: str = HISTORY_FORMAT
    ) -> Any:
        """`GET tracker/{id}/positions?time_from&time_to&format` – historie (unix ts v sekundách).

        Návratový typ záměrně `Any`: tvar odpovědi závisí na `format`
        (TODO(fáze 0): tvar zatím nezachycen – zafixovat do parsing.parse_history()
        po capture během procházky).
        """
        return await self._call(
            lambda: self.tractive.tracker(tracker_id).positions(time_from, time_to, fmt)
        )

    async def get_health_overview(self, pet_id: str) -> dict[str, Any]:
        """`GET aps-api.tractive.com/api/1/pet/{id}/health/overview`.

        aiotractive vrací `{}` při 400 (nepodporováno) a 403 (neaktivní Premium).
        Tvar ověřen (camelCase): petId, activityDataSyncedAt (ISO UTC), activity{minutesActive,
        minutesGoal, hourlyDistribution[24]}, sleep{minutesDaySleep, minutesNightSleep,
        minutesCalm}, bark/restingHeartRate/restingRespiratoryRate/scratch{status, dayOffset},
        healthAlerts, associatedData, separationPhaseStatus, stateTimeline.
        """
        return await self._call(lambda: self.tractive.trackable_object(pet_id).health_overview())

    # ------------------------------------------------------------------ commands

    async def send_command(self, tracker_id: str, command: Command, on: bool) -> dict[str, Any]:
        """Pošle příkaz LIVE/BUZZER/LED. Odpověď typicky obsahuje `pending: true`.

        - Příkaz je ve frontě u Tractive; potvrzení přijde streamem (switch state
          `live_tracking` / `buzzer_control` / `led_control` s `active`, `remaining`).
        - V domácí / power-saving zóně cloudové příkazy neprojdou (oficiální appka jde přes BT).
        - LIVE vyprší po ~300 s, pro delší sledování je nutné obnovovat (pozor na 429).
        TODO(fáze 0): zachytit odpověď pro on/off doma i venku.
        """
        tracker = self.tractive.tracker(tracker_id)
        method = {
            Command.LIVE: tracker.set_live_tracking_active,
            Command.BUZZER: tracker.set_buzzer_active,
            Command.LED: tracker.set_led_active,
        }[command]
        return await self._call(lambda: method(on))

    # ------------------------------------------------------------------ realtime

    async def events(self, on_connected: Callable[[], None] | None = None) -> AsyncIterator[dict[str, Any]]:
        """Async generátor eventů z channel.tractive.com (bez handshake/keep-alive).

        Ekvivalent `Tractive.events()`, jen si držíme referenci na `Channel`, abychom
        (a) dostali callback při handshake (`on_connected`) a (b) mohli pro health
        číst čas posledního keep-alive (`last_keepalive_at`).

        Končí výjimkou (`DisconnectedError` po 60 s bez keep-alive, `TractiveError`,
        `UnauthorizedError`); reconnect řeší `channel.ChannelConsumer`.

        TODO: sahá na privátní `Tractive._api` (stejně jako `force_relogin`).
        Při upgradu aiotractive ověřit.
        """
        api = getattr(self.tractive, "_api", None)
        if api is None:  # fallback: bez handshake/keep-alive informací
            async for event in self.tractive.events():
                yield event
            return
        channel = Channel(api, on_connected=on_connected)
        self._channel = channel
        try:
            async with contextlib.aclosing(channel.listen()) as stream:
                async for event in stream:
                    yield event
        finally:
            if self._channel is channel:
                self._channel = None

    def last_keepalive_at(self) -> float | None:
        """Unix ts posledního keep-alive aktuálního spojení (None = žádné spojení / ještě nepřišel)."""
        channel = self._channel
        return None if channel is None else getattr(channel, "_last_keep_alive", None)
