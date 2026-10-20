"""Konzument realtime kanálu Tractive (channel.tractive.com).

Není to WebSocket: aiotractive drží dlouhý chunked HTTP POST a vystavuje ho jako
async generátor `Tractive.events()`. Vrstvy odpovědnosti:

  aiotractive.Channel
    - zahazuje `handshake` a `keep-alive` (k nám se nedostanou),
    - 60s keep-alive watchdog -> ukončí stream `DisconnectedError`,
    - pár retry při 429/5xx uvnitř jednoho připojení.
  ChannelConsumer (tady)
    - nekonečný reconnect s exponenciálním backoffem + full jitter,
    - re-login při 401/403 (refresh token neexistuje),
    - volitelný "silence" watchdog (žádný event N s -> tvrdý reconnect),
    - po reconnectu zavolá `on_reconnect` (ingestor doplní mezeru backfillem),
    - chyba v handleru nezabije stream (zaloguje se a jede se dál),
    - průběžně zapisuje stav do `HealthState` (handshake, eventy, reconnecty,
      chyby) pro /readyz.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import structlog
from aiotractive.exceptions import TractiveError, UnauthorizedError

from .client import TractiveClient
from .health import HealthState

log = structlog.get_logger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class FatalChannelError(RuntimeError):
    """Neopravitelná chyba (špatné přihlašovací údaje, zrušené předplatné…)."""


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponenciální backoff s "full jitter" (AWS styl): U(0, min(cap, base*2^n))."""
    return random.uniform(0, min(cap, base * (2 ** max(0, attempt - 1))))  # noqa: S311


class ChannelConsumer:
    def __init__(
        self,
        client: TractiveClient,
        handler: EventHandler,
        *,
        backoff_base_s: float = 5.0,
        backoff_max_s: float = 300.0,
        max_silence_s: float = 0,
        on_reconnect: Callable[[], Awaitable[None]] | None = None,
        health: HealthState | None = None,
    ) -> None:
        self._client = client
        self._handler = handler
        self._base = backoff_base_s
        self._cap = backoff_max_s
        self._max_silence = max_silence_s or None
        self._on_reconnect = on_reconnect
        self._health = health or HealthState()
        self.events_received = 0
        self._conn_got_event = False

    async def run(self, stop: asyncio.Event) -> None:
        """Běží, dokud není nastaven `stop` (nebo nenastane FatalChannelError)."""
        attempt = 0
        connected_before = False
        while not stop.is_set():
            if connected_before:
                self._health.mark_reconnect()
            if connected_before and self._on_reconnect is not None:
                try:
                    await self._on_reconnect()
                except Exception:  # backfill nesmí zablokovat stream
                    log.exception("channel.on_reconnect_failed")

            self._conn_got_event = False
            try:
                log.info("channel.connecting", attempt=attempt)
                await self._consume_once(stop)
                connected_before = True
                if stop.is_set():
                    break
                log.warning("channel.stream_ended")
                self._health.record_error("channel stream ended")
            except UnauthorizedError:
                log.warning("channel.unauthorized")
                self._health.mark_unauthenticated("channel unauthorized (401/403)")
                try:
                    auth = await self._client.force_relogin()
                    self._health.mark_authenticated(auth.user_id)
                except UnauthorizedError as exc:
                    self._health.mark_unauthenticated("re-login rejected")
                    raise FatalChannelError(
                        "Tractive odmítl přihlášení (špatné údaje / Apple-Google účet / neaktivní Premium?)"
                    ) from exc
            except (TractiveError, aiohttp.ClientError, TimeoutError) as exc:
                connected_before = True
                log.warning("channel.disconnected", error=repr(exc))
                self._health.mark_disconnected(f"channel disconnected: {exc!r}")

            if self._conn_got_event:
                attempt = 0  # spojení fungovalo -> backoff začíná znovu od nuly
            attempt += 1
            delay = backoff_delay(attempt, self._base, self._cap)
            log.info("channel.reconnect_wait", delay_s=round(delay, 1), attempt=attempt)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)

    async def _consume_once(self, stop: asyncio.Event) -> bool:
        """Jedno připojení. Vrací True, pokud přišel aspoň jeden event."""
        got_event = False
        stream = self._client.events(on_connected=self._health.mark_connected)
        stop_task = asyncio.create_task(stop.wait())
        try:
            while True:
                next_task = asyncio.ensure_future(anext(stream))
                done, _ = await asyncio.wait(
                    {next_task, stop_task},
                    timeout=self._max_silence,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    next_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                        await next_task
                    return got_event
                if not done:  # silence watchdog
                    next_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                        await next_task
                    log.warning("channel.silence_timeout", seconds=self._max_silence)
                    self._health.record_error(f"channel silent for {self._max_silence}s")
                    return got_event
                try:
                    event = next_task.result()
                except StopAsyncIteration:
                    return got_event
                got_event = True
                self._conn_got_event = True
                self.events_received += 1
                self._health.mark_event()
                try:
                    await self._handler(event)
                except Exception:
                    # Event je typicky už uložen raw; chyba je v normalizaci -> nezabíjet stream.
                    log.exception("channel.handler_failed", message=event.get("message"))
                    self._health.record_error(f"handler failed for {event.get('message')!r}")
        finally:
            self._health.mark_disconnected()
            stop_task.cancel()
            with contextlib.suppress(Exception):
                await stream.aclose()  # type: ignore[attr-defined]
