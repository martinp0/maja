"""Vestavěný HTTP health server (liveness + readiness) – čistě stdlib asyncio.

Běží ve stejné event loop jako ingestor, takže:
  * `GET /healthz` (liveness) – 200, pokud event loop odpovídá. Samotná odpověď
    je důkaz, že proces žije a loop není zaseknutý (zablokovaná loop = timeout).
  * `GET /readyz` (readiness) – 200 jen pokud
      - máme platnou Tractive session (`authenticated`),
      - realtime kanál je připojený (`channel_connected`, nastaví se při handshake),
      - z kanálu přišel event / keep-alive / handshake za posledních
        `ready_max_age_s` sekund (default 90 s; Tractive posílá keep-alive
        zhruba každých pár desítek sekund, aiotractive po 60 s ticha spojení shodí).
    Jinak 503 + JSON s důvody (`reasons`).

Žádná nová závislost: minimální HTTP/1.1 parser nad `asyncio.start_server`,
každá odpověď `Connection: close`. Stav (`HealthState`) se mění jen z event loop
(channel/ingestor) a server ho jen čte ve stejné loop → není potřeba zámek.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)

DEFAULT_READY_MAX_AGE_S = 90.0
_MAX_REQUEST_BYTES = 8192
_READ_TIMEOUT_S = 5.0


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).isoformat()


def _age(ts: float | None, now: float) -> float | None:
    return None if ts is None else round(max(0.0, now - ts), 1)


@dataclass
class HealthState:
    """Sdílený runtime stav, který čte health server.

    Zapisují: `Ingestor` (fáze, přihlášení), `ChannelConsumer` (spojení, eventy,
    reconnecty, chyby). Časy jsou unix ts (`time.time()`), stejně jako v aiotractive.
    """

    started_at: float = field(default_factory=time.time)
    phase: str = "starting"  # starting | login | discovery | backfill | streaming | stopping

    authenticated: bool = False
    user_id: str | None = None
    last_login_at: float | None = None

    channel_connected: bool = False
    channel_connected_at: float | None = None  # poslední handshake
    last_event_at: float | None = None  # poslední skutečný event (doručený handleru)
    last_reconnect_at: float | None = None
    reconnects: int = 0
    events_received: int = 0

    last_error: str | None = None
    last_error_at: float | None = None

    # Volitelný zdroj času posledního keep-alive (aiotractive je zahazuje,
    # ale drží si timestamp v Channel._last_keep_alive → TractiveClient ho vystaví).
    keepalive_source: Callable[[], float | None] | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ zápis

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def mark_authenticated(self, user_id: str | None = None) -> None:
        self.authenticated = True
        self.user_id = user_id or self.user_id
        self.last_login_at = time.time()

    def mark_unauthenticated(self, error: str | None = None) -> None:
        self.authenticated = False
        if error:
            self.record_error(error)

    def mark_connected(self) -> None:
        now = time.time()
        self.channel_connected = True
        self.channel_connected_at = now

    def mark_disconnected(self, error: str | None = None) -> None:
        self.channel_connected = False
        if error:
            self.record_error(error)

    def mark_reconnect(self) -> None:
        self.reconnects += 1
        self.last_reconnect_at = time.time()

    def mark_event(self) -> None:
        self.events_received += 1
        self.last_event_at = time.time()
        # Event = spojení evidentně žije, i kdyby handshake callback nepřišel.
        if not self.channel_connected:
            self.channel_connected = True
            self.channel_connected_at = self.channel_connected_at or self.last_event_at

    def record_error(self, error: str) -> None:
        self.last_error = error[:500]
        self.last_error_at = time.time()

    # ------------------------------------------------------------------ čtení

    def last_keepalive_at(self) -> float | None:
        if self.keepalive_source is None:
            return None
        try:
            return self.keepalive_source()
        except Exception:  # health nikdy nesmí shodit službu
            return None

    def last_activity_at(self) -> float | None:
        """Nejnovější známka života kanálu: event, keep-alive nebo handshake."""
        candidates = [t for t in (self.last_event_at, self.last_keepalive_at(), self.channel_connected_at) if t]
        return max(candidates) if candidates else None

    def readiness(self, max_age_s: float = DEFAULT_READY_MAX_AGE_S) -> tuple[bool, dict[str, Any]]:
        now = time.time()
        keepalive = self.last_keepalive_at()
        # keep-alive čteme z živého Channel objektu → jeho existence = spojení běží
        connected = self.channel_connected or keepalive is not None
        activity = self.last_activity_at()
        age = _age(activity, now)
        reasons: list[str] = []
        if not self.authenticated:
            reasons.append("not_authenticated")
        if not connected:
            reasons.append("channel_not_connected")
        if age is None:
            reasons.append("no_channel_activity_yet")
        elif age > max_age_s:
            reasons.append("channel_stale")
        ready = not reasons
        body: dict[str, Any] = {
            "status": "ready" if ready else "not_ready",
            "reasons": reasons,
            "phase": self.phase,
            "authenticated": self.authenticated,
            "channel_connected": connected,
            "last_event_age_s": _age(self.last_event_at, now),
            "last_keepalive_age_s": _age(keepalive, now),
            "last_activity_age_s": age,
            "max_age_s": max_age_s,
            "events_received": self.events_received,
            "reconnects": self.reconnects,
            "last_reconnect_at": _iso(self.last_reconnect_at),
            "last_login_at": _iso(self.last_login_at),
            "last_error": self.last_error,
            "last_error_at": _iso(self.last_error_at),
            "uptime_s": round(now - self.started_at, 1),
        }
        return ready, body

    def liveness(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "phase": self.phase,
            "pid": os.getpid(),
            "uptime_s": round(time.time() - self.started_at, 1),
        }


class HealthServer:
    """Minimální HTTP server: GET/HEAD /healthz, /readyz. Nic jiného."""

    def __init__(
        self,
        state: HealthState,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        ready_max_age_s: float = DEFAULT_READY_MAX_AGE_S,
    ) -> None:
        self.state = state
        self.host = host
        self.port = port
        self.ready_max_age_s = ready_max_age_s
        self._server: asyncio.base_events.Server | None = None

    @property
    def bound_port(self) -> int | None:
        """Skutečný port (užitečné při port=0 v testech)."""
        if self._server is None or not self._server.sockets:
            return None
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        log.info("health.listening", host=self.host, port=self.bound_port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._server.wait_closed(), timeout=2)
        self._server = None
        log.info("health.stopped")

    async def __aenter__(self) -> HealthServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------ HTTP

    def route(self, method: str, path: str) -> tuple[int, dict[str, Any]]:
        path = path.split("?", 1)[0].rstrip("/") or "/"
        if method not in ("GET", "HEAD"):
            return 405, {"error": "method_not_allowed"}
        if path == "/healthz":
            return 200, self.state.liveness()
        if path == "/readyz":
            ready, body = self.state.readiness(self.ready_max_age_s)
            return (200 if ready else 503), body
        return 404, {"error": "not_found", "endpoints": ["/healthz", "/readyz"]}

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, body, method = 400, {"error": "bad_request"}, "GET"
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_READ_TIMEOUT_S)
            if len(head) > _MAX_REQUEST_BYTES:
                raise ValueError("request too large")
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = request_line.split()
            if len(parts) >= 2:
                method = parts[0].upper()
                status, body = self.route(method, parts[1])
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ValueError):
            pass
        except Exception:
            log.exception("health.handler_failed")
            status, body = 500, {"error": "internal"}
        try:
            payload = json.dumps(body, ensure_ascii=False).encode()
            reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
                      500: "Internal Server Error", 503: "Service Unavailable"}.get(status, "")
            headers = (
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Cache-Control: no-store\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            writer.write(headers if method == "HEAD" else headers + payload)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
