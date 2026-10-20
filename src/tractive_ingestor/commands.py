"""Fronta povelů (LIVE / buzzer / LED) nad tabulkou `commands`.

Proč fronta v DB a ne přímé volání:
- povel může přijít odkudkoli (CLI `enqueue`, později appka/HomeKit) a posílá ho
  jen jediný Tractive klient v ingestoru (jeden token, žádné 429 z paralelních loginů),
- doma (power-saving / KNOWN_WIFI zóna) cloudové povely neprojdou – místo chyby se
  povel odloží (`deferred`) a zkouší se znovu, dokud pes neodejde, max. COMMAND_DEFER_TIMEOUT_S.

Stavy: queued -> sending -> sent -> done
       queued -> sending -> deferred -> sending … -> sent | failed
Rozhodovací logika (`decide`) je čistá funkce bez DB i sítě -> testuje se zvlášť.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from aiotractive.exceptions import TractiveError

from .client import Command

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)

# CLI název -> Command (hodnota = segment URL i název switche ve streamu)
COMMAND_ALIASES: dict[str, Command] = {
    "live": Command.LIVE,
    "buzzer": Command.BUZZER,
    "sound": Command.BUZZER,
    "led": Command.LED,
    "light": Command.LED,
}
ACTIONS = ("on", "off")

HOME_SENSORS = frozenset({"KNOWN_WIFI"})  # sensor_used = pes je v domácí WiFi zóně

REASON_HOME = "tracker_in_home_zone"
REASON_EXPIRED = "expired"
REASON_MAX_ATTEMPTS = "max_attempts"
REASON_INTERRUPTED = "interrupted"  # proces spadl uprostřed odesílání
REASON_UNCONFIRMED = "unconfirmed"  # API přijalo, stream potvrzení nepřinesl


class Status(str, enum.Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DONE = "done"
    DEFERRED = "deferred"
    FAILED = "failed"


def parse_command(command: str, action: str = "on") -> tuple[Command, str]:
    """`live|buzzer|led` + `on|off` -> (Command, action). ValueError při neplatném vstupu."""
    cmd = COMMAND_ALIASES.get(command.strip().lower())
    if cmd is None:
        # povolit i přímo hodnoty enumu (live_tracking, …)
        try:
            cmd = Command(command.strip().lower())
        except ValueError:
            raise ValueError(f"Neznámý povel {command!r} (povolené: live, buzzer, led)") from None
    act = action.strip().lower()
    if act not in ACTIONS:
        raise ValueError(f"Neznámá akce {action!r} (povolené: on, off)")
    return cmd, act


@dataclass(slots=True)
class CommandRow:
    id: str
    tracker_id: str
    command: Command
    action: str
    created_at: datetime
    attempts: int = 0

    @property
    def on(self) -> bool:
        return self.action == "on"


@dataclass(slots=True)
class TrackerState:
    """Poslední známý stav trackeru z DB (hw_reports + positions). Vše null-able."""

    hw_ts: datetime | None = None
    power_saving: bool | None = None
    hw_zone_id: str | None = None
    pos_ts: datetime | None = None
    sensor_used: str | None = None
    pos_zone_id: str | None = None

    def in_home_zone(self) -> bool | None:
        """True = doma (power-saving / KNOWN_WIFI), False = venku, None = nevíme.

        Rozhoduje NOVĚJŠÍ ze dvou zdrojů: hw_report ze snapshotu může nést starý
        POWER_SAVING, zatímco stream už posílá GPS polohu z procházky.
        """
        pos_known = self.pos_ts is not None
        hw_known = self.hw_ts is not None and (self.power_saving is not None or self.hw_zone_id is not None)
        if not pos_known and not hw_known:
            return None
        use_pos = pos_known and (not hw_known or self.pos_ts >= self.hw_ts)  # type: ignore[operator]
        if use_pos:
            return (self.sensor_used or "").upper() in HOME_SENSORS or self.pos_zone_id is not None
        return bool(self.power_saving) or self.hw_zone_id is not None


class Verdict(str, enum.Enum):
    SEND = "send"
    DEFER = "defer"
    FAIL = "fail"


@dataclass(slots=True, frozen=True)
class Decision:
    verdict: Verdict
    reason: str | None = None


def decide(
    cmd: CommandRow, state: TrackerState, *, now: datetime, defer_timeout_s: float, max_attempts: int
) -> Decision:
    """Poslat / odložit / vzdát. Bez vedlejších efektů.

    - doma: odložit, po `defer_timeout_s` od zařazení `failed: tracker_in_home_zone`
      (u LIVE on je to běžný případ – pes leží doma),
    - starý povel se neposílá ani venku (buzzer ze včerejška by byl překvapení): `expired`,
    - neznámý stav (prázdná DB) = zkusit poslat, API případně odmítne samo.
    """
    if cmd.attempts >= max_attempts:
        return Decision(Verdict.FAIL, REASON_MAX_ATTEMPTS)
    expired = (now - cmd.created_at).total_seconds() >= defer_timeout_s
    if state.in_home_zone():
        return Decision(Verdict.FAIL, REASON_HOME) if expired else Decision(Verdict.DEFER, REASON_HOME)
    if expired:
        return Decision(Verdict.FAIL, REASON_EXPIRED)
    return Decision(Verdict.SEND)


# ============================================================================ úložiště


class CommandStore(Protocol):
    async def enqueue(self, tracker_id: str, command: Command, action: str) -> str: ...
    async def requeue_interrupted(self) -> int: ...
    async def claim(self, limit: int) -> list[CommandRow]: ...
    async def tracker_state(self, tracker_id: str) -> TrackerState: ...
    async def mark_sent(self, cmd_id: str, response: Any) -> None: ...
    async def mark_deferred(
        self, cmd_id: str, reason: str, retry_in_s: float, *, count_attempt: bool = False
    ) -> None: ...
    async def mark_failed(self, cmd_id: str, reason: str, *, count_attempt: bool = False) -> None: ...
    async def confirm(self, tracker_id: str, switch: str, active: bool) -> int: ...
    async def finish_unconfirmed(self, older_than_s: float) -> int: ...


def _row(r: Any) -> CommandRow:
    return CommandRow(
        id=str(r["id"]),
        tracker_id=r["tracker_id"],
        command=Command(r["command"]),
        action=r["action"],
        created_at=r["created_at"],
        attempts=r["attempts"],
    )


class PgCommandStore:
    """SQL nad asyncpg poolem (sdílí pool s PgStorage)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def enqueue(self, tracker_id: str, command: Command, action: str) -> str:
        return str(
            await self.pool.fetchval(
                "INSERT INTO commands (tracker_id, command, action) VALUES ($1, $2, $3) RETURNING id",
                tracker_id,
                command.value,
                action,
            )
        )

    async def requeue_interrupted(self) -> int:
        """Při startu: `sending` z minulého běhu (pád uprostřed volání) -> deferred.

        Ingestor běží v jediné instanci, takže `sending` při startu nikdo jiný nedrží.
        """
        res = await self.pool.execute(
            """
            UPDATE commands SET status = 'deferred', last_error = $1, next_attempt_at = now(), updated_at = now()
            WHERE status = 'sending'
            """,
            REASON_INTERRUPTED,
        )
        return int(res.split()[-1])

    async def claim(self, limit: int) -> list[CommandRow]:
        """Atomicky převezme splatné povely (queued/deferred) -> sending. SKIP LOCKED = žádné dvojí odeslání."""
        rows = await self.pool.fetch(
            """
            UPDATE commands c SET status = 'sending', updated_at = now()
            FROM (
                SELECT id FROM commands
                WHERE status IN ('queued', 'deferred') AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY created_at
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            ) due
            WHERE c.id = due.id
            RETURNING c.id, c.tracker_id, c.command, c.action, c.created_at, c.attempts
            """,
            limit,
        )
        return sorted((_row(r) for r in rows), key=lambda c: c.created_at)

    async def tracker_state(self, tracker_id: str) -> TrackerState:
        row = await self.pool.fetchrow(
            """
            SELECT h.ts AS hw_ts, h.power_saving, h.power_saving_zone_id AS hw_zone_id,
                   p.ts AS pos_ts, p.sensor_used, p.power_saving_zone_id AS pos_zone_id
            FROM (SELECT 1) one
            LEFT JOIN LATERAL (SELECT ts, power_saving, power_saving_zone_id FROM hw_reports
                               WHERE tracker_id = $1 ORDER BY ts DESC LIMIT 1) h ON true
            LEFT JOIN LATERAL (SELECT ts, sensor_used, power_saving_zone_id FROM positions
                               WHERE tracker_id = $1 ORDER BY ts DESC LIMIT 1) p ON true
            """,
            tracker_id,
        )
        return TrackerState(**dict(row)) if row else TrackerState()

    async def mark_sent(self, cmd_id: str, response: Any) -> None:
        await self.pool.execute(
            """
            UPDATE commands SET status = 'sent', attempts = attempts + 1, sent_at = now(), response = $2::jsonb,
                                last_error = NULL, updated_at = now()
            WHERE id = $1 AND status = 'sending'
            """,
            cmd_id,
            response,
        )

    async def mark_deferred(self, cmd_id: str, reason: str, retry_in_s: float, *, count_attempt: bool = False) -> None:
        await self.pool.execute(
            """
            UPDATE commands SET status = 'deferred', last_error = $2,
                                attempts = attempts + CASE WHEN $4 THEN 1 ELSE 0 END,
                                next_attempt_at = now() + make_interval(secs => $3), updated_at = now()
            WHERE id = $1 AND status = 'sending'
            """,
            cmd_id,
            reason,
            float(retry_in_s),
            count_attempt,
        )

    async def mark_failed(self, cmd_id: str, reason: str, *, count_attempt: bool = False) -> None:
        await self.pool.execute(
            """
            UPDATE commands SET status = 'failed', last_error = $2,
                                attempts = attempts + CASE WHEN $3 THEN 1 ELSE 0 END,
                                finished_at = now(), updated_at = now()
            WHERE id = $1 AND status = 'sending'
            """,
            cmd_id,
            reason,
            count_attempt,
        )

    async def confirm(self, tracker_id: str, switch: str, active: bool) -> int:
        """Stav switche ze streamu odpovídá odeslanému povelu -> done."""
        res = await self.pool.execute(
            """
            UPDATE commands SET status = 'done', finished_at = now(), updated_at = now()
            WHERE tracker_id = $1 AND command = $2 AND action = $3 AND status = 'sent'
            """,
            tracker_id,
            switch,
            "on" if active else "off",
        )
        return int(res.split()[-1])

    async def finish_unconfirmed(self, older_than_s: float) -> int:
        """`sent` bez potvrzení ze streamu -> done s poznámkou (API povel přijalo; re-send nedává smysl)."""
        res = await self.pool.execute(
            """
            UPDATE commands SET status = 'done', last_error = $2, finished_at = now(), updated_at = now()
            WHERE status = 'sent' AND sent_at < now() - make_interval(secs => $1)
            """,
            float(older_than_s),
            REASON_UNCONFIRMED,
        )
        return int(res.split()[-1])


# ============================================================================ worker


class CommandSender(Protocol):
    async def send_command(self, tracker_id: str, command: Command, on: bool) -> dict[str, Any]: ...


class CommandWorker:
    """Polluje frontu a posílá povely přes jediný TractiveClient."""

    def __init__(
        self,
        store: CommandStore,
        client: CommandSender,
        *,
        poll_interval_s: float = 4.0,
        defer_timeout_s: float = 600.0,
        defer_retry_s: float = 30.0,
        max_attempts: int = 3,
        confirm_timeout_s: float = 120.0,
        batch_size: int = 5,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.client = client
        self.poll_interval_s = poll_interval_s
        self.defer_timeout_s = defer_timeout_s
        self.defer_retry_s = defer_retry_s
        self.max_attempts = max_attempts
        self.confirm_timeout_s = confirm_timeout_s
        self.batch_size = batch_size
        self._now = now

    async def run(self, stop: asyncio.Event) -> None:
        n = await self.store.requeue_interrupted()
        if n:
            log.warning("commands.requeued_interrupted", count=n)
        log.info("commands.worker_started", poll_interval_s=self.poll_interval_s)
        while not stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # DB výpadek apod. – worker nesmí shodit ingestor
                log.warning("commands.tick_failed", error=repr(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_s)

    async def tick(self) -> int:
        """Jedno kolo: dokončit nepotvrzené, převzít splatné a zpracovat. Vrací počet převzatých."""
        if done := await self.store.finish_unconfirmed(self.confirm_timeout_s):
            log.info("commands.unconfirmed_done", count=done)
        claimed = await self.store.claim(self.batch_size)
        for cmd in claimed:
            await self.process(cmd)
        return len(claimed)

    async def process(self, cmd: CommandRow) -> None:
        state = await self.store.tracker_state(cmd.tracker_id)
        d = decide(cmd, state, now=self._now(), defer_timeout_s=self.defer_timeout_s, max_attempts=self.max_attempts)
        ctx = {"command_id": cmd.id, "tracker_id": cmd.tracker_id, "command": cmd.command.value, "action": cmd.action}
        if d.verdict is Verdict.FAIL:
            log.warning("commands.failed", reason=d.reason, attempts=cmd.attempts, **ctx)
            await self.store.mark_failed(cmd.id, d.reason or "failed")
            return
        if d.verdict is Verdict.DEFER:
            log.info("commands.deferred", reason=d.reason, retry_in_s=self.defer_retry_s, **ctx)
            await self.store.mark_deferred(cmd.id, d.reason or "deferred", self.defer_retry_s)
            return
        try:
            response = await self.client.send_command(cmd.tracker_id, cmd.command, cmd.on)
        except TractiveError as exc:  # 429 už opakuje aiotractive, sem dojde až po vyčerpání
            attempts = cmd.attempts + 1
            if attempts >= self.max_attempts:
                log.warning("commands.failed", reason=repr(exc), attempts=attempts, **ctx)
                await self.store.mark_failed(cmd.id, f"{REASON_MAX_ATTEMPTS}: {exc!r}", count_attempt=True)
            else:
                retry_in = self.defer_retry_s * attempts  # lineární backoff, šetrně k rate-limitu
                log.warning("commands.send_error", error=repr(exc), attempts=attempts, retry_in_s=retry_in, **ctx)
                await self.store.mark_deferred(cmd.id, repr(exc), retry_in, count_attempt=True)
            return
        log.info("commands.sent", response=response, **ctx)
        await self.store.mark_sent(cmd.id, response)

