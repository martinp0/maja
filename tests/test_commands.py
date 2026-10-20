"""Fronta povelů: validace, rozhodnutí doma/venku, stavový automat workeru.

Bez Tractive účtu i bez DB (in-memory store + falešný klient). SQL v `PgCommandStore`
se navíc ověří proti skutečnému Postgresu, pokud je nastavené TEST_DATABASE_URL
(prázdná testovací DB s PostGIS; schema.sql se aplikuje) – jinak skip.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from aiotractive.exceptions import TractiveError

from tractive_ingestor.client import Command
from tractive_ingestor.commands import (
    REASON_EXPIRED,
    REASON_HOME,
    REASON_INTERRUPTED,
    REASON_MAX_ATTEMPTS,
    REASON_UNCONFIRMED,
    CommandRow,
    CommandWorker,
    Status,
    TrackerState,
    Verdict,
    decide,
    parse_command,
)

T0 = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)

HOME = TrackerState(hw_ts=T0, power_saving=True, hw_zone_id="zone-1",
                    pos_ts=T0, sensor_used="KNOWN_WIFI", pos_zone_id="zone-1")
OUTSIDE = TrackerState(hw_ts=T0 - timedelta(minutes=5), power_saving=True, hw_zone_id="zone-1",
                       pos_ts=T0, sensor_used="GPS", pos_zone_id=None)


class ParseCommandTest(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual(parse_command("live", "on"), (Command.LIVE, "on"))
        self.assertEqual(parse_command("Buzzer", "OFF"), (Command.BUZZER, "off"))
        self.assertEqual(parse_command("sound"), (Command.BUZZER, "on"))
        self.assertEqual(parse_command("led", "off"), (Command.LED, "off"))
        self.assertEqual(parse_command("light"), (Command.LED, "on"))
        self.assertEqual(parse_command("live_tracking", "off"), (Command.LIVE, "off"))

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_command("explode", "on")
        with self.assertRaises(ValueError):
            parse_command("live", "maybe")

    def test_cli_rejects_invalid(self) -> None:
        from tractive_ingestor.__main__ import main

        with self.assertRaises(SystemExit):
            main(["enqueue", "TRK00001", "explode"])
        with self.assertRaises(SystemExit):
            main(["enqueue", "TRK00001", "live", "maybe"])


class HomeZoneTest(unittest.TestCase):
    def test_unknown(self) -> None:
        self.assertIsNone(TrackerState().in_home_zone())

    def test_known_wifi_is_home(self) -> None:
        self.assertTrue(TrackerState(pos_ts=T0, sensor_used="KNOWN_WIFI").in_home_zone())

    def test_power_saving_is_home(self) -> None:
        self.assertTrue(TrackerState(hw_ts=T0, power_saving=True).in_home_zone())

    def test_newer_gps_position_wins_over_stale_power_saving(self) -> None:
        self.assertFalse(OUTSIDE.in_home_zone())

    def test_newer_hw_wins(self) -> None:
        st = TrackerState(hw_ts=T0, power_saving=False, pos_ts=T0 - timedelta(minutes=1), sensor_used="KNOWN_WIFI")
        self.assertFalse(st.in_home_zone())


class DecideTest(unittest.TestCase):
    def cmd(self, age_s: float = 0, attempts: int = 0, command: Command = Command.LIVE) -> CommandRow:
        return CommandRow(id="c1", tracker_id="TRK00001", command=command, action="on",
                          created_at=T0 - timedelta(seconds=age_s), attempts=attempts)

    def d(self, cmd: CommandRow, state: TrackerState) -> Any:
        return decide(cmd, state, now=T0, defer_timeout_s=600, max_attempts=3)

    def test_send_outside(self) -> None:
        self.assertEqual(self.d(self.cmd(), OUTSIDE).verdict, Verdict.SEND)

    def test_send_when_state_unknown(self) -> None:
        self.assertEqual(self.d(self.cmd(), TrackerState()).verdict, Verdict.SEND)

    def test_defer_at_home(self) -> None:
        res = self.d(self.cmd(age_s=60), HOME)
        self.assertEqual((res.verdict, res.reason), (Verdict.DEFER, REASON_HOME))

    def test_fail_at_home_after_timeout(self) -> None:
        res = self.d(self.cmd(age_s=601), HOME)
        self.assertEqual((res.verdict, res.reason), (Verdict.FAIL, REASON_HOME))

    def test_expired_outside(self) -> None:
        res = self.d(self.cmd(age_s=3600, command=Command.BUZZER), OUTSIDE)
        self.assertEqual((res.verdict, res.reason), (Verdict.FAIL, REASON_EXPIRED))

    def test_max_attempts(self) -> None:
        res = self.d(self.cmd(attempts=3), OUTSIDE)
        self.assertEqual((res.verdict, res.reason), (Verdict.FAIL, REASON_MAX_ATTEMPTS))


# ---------------------------------------------------------------------- worker s in-memory store


class MemStore:
    """In-memory obdoba PgCommandStore se stejnými přechody (guard na status = 'sending')."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.rows: dict[str, dict[str, Any]] = {}
        self.state = TrackerState()
        self._seq = 0

    async def enqueue(self, tracker_id: str, command: Command, action: str) -> str:
        self._seq += 1
        cid = f"c{self._seq}"
        self.rows[cid] = dict(id=cid, tracker_id=tracker_id, command=command.value, action=action,
                              status="queued", created_at=self.now, attempts=0, next_attempt_at=None,
                              sent_at=None, last_error=None, response=None)
        return cid

    async def requeue_interrupted(self) -> int:
        n = 0
        for r in self.rows.values():
            if r["status"] == "sending":
                r.update(status="deferred", last_error=REASON_INTERRUPTED, next_attempt_at=self.now)
                n += 1
        return n

    async def claim(self, limit: int) -> list[CommandRow]:
        due = [r for r in self.rows.values() if r["status"] in ("queued", "deferred")
               and (r["next_attempt_at"] is None or r["next_attempt_at"] <= self.now)]
        out = []
        for r in sorted(due, key=lambda r: r["created_at"])[:limit]:
            r["status"] = "sending"
            out.append(CommandRow(id=r["id"], tracker_id=r["tracker_id"], command=Command(r["command"]),
                                  action=r["action"], created_at=r["created_at"], attempts=r["attempts"]))
        return out

    async def tracker_state(self, tracker_id: str) -> TrackerState:
        return self.state

    def _guard(self, cid: str) -> dict[str, Any] | None:
        r = self.rows[cid]
        return r if r["status"] == "sending" else None

    async def mark_sent(self, cid: str, response: Any) -> None:
        if r := self._guard(cid):
            r.update(status="sent", attempts=r["attempts"] + 1, sent_at=self.now, response=response, last_error=None)

    async def mark_deferred(self, cid: str, reason: str, retry_in_s: float, *, count_attempt: bool = False) -> None:
        if r := self._guard(cid):
            r.update(status="deferred", last_error=reason, attempts=r["attempts"] + int(count_attempt),
                     next_attempt_at=self.now + timedelta(seconds=retry_in_s))

    async def mark_failed(self, cid: str, reason: str, *, count_attempt: bool = False) -> None:
        if r := self._guard(cid):
            r.update(status="failed", last_error=reason, attempts=r["attempts"] + int(count_attempt))

    async def confirm(self, tracker_id: str, switch: str, active: bool) -> int:
        n = 0
        for r in self.rows.values():
            if (r["tracker_id"], r["command"], r["action"], r["status"]) == (
                tracker_id, switch, "on" if active else "off", "sent"):
                r["status"] = "done"
                n += 1
        return n

    async def finish_unconfirmed(self, older_than_s: float) -> int:
        n = 0
        for r in self.rows.values():
            if r["status"] == "sent" and r["sent_at"] < self.now - timedelta(seconds=older_than_s):
                r.update(status="done", last_error=REASON_UNCONFIRMED)
                n += 1
        return n


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Command, bool]] = []
        self.fail_with: Exception | None = None

    async def send_command(self, tracker_id: str, command: Command, on: bool) -> dict[str, Any]:
        self.calls.append((tracker_id, command, on))
        if self.fail_with is not None:
            raise self.fail_with
        return {"pending": True}


class WorkerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = T0
        self.store = MemStore(self.now)
        self.client = FakeClient()
        self.worker = CommandWorker(self.store, self.client, defer_timeout_s=600, defer_retry_s=30,
                                    max_attempts=3, confirm_timeout_s=120, now=lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.store.now = self.now

    def status(self, cid: str) -> str:
        return self.store.rows[cid]["status"]

    async def test_send_then_confirm(self) -> None:
        self.store.state = OUTSIDE
        cid = await self.store.enqueue("TRK00001", Command.LIVE, "on")
        self.assertEqual(await self.worker.tick(), 1)
        self.assertEqual(self.status(cid), Status.SENT.value)
        self.assertEqual(self.client.calls, [("TRK00001", Command.LIVE, True)])
        self.assertEqual(self.store.rows[cid]["attempts"], 1)
        # opakovaný tick už odeslaný povel znovu neposílá
        self.assertEqual(await self.worker.tick(), 0)
        self.assertEqual(len(self.client.calls), 1)
        # potvrzení ze streamu: jiný stav switche nepotvrzuje
        self.assertEqual(await self.store.confirm("TRK00001", "live_tracking", False), 0)
        self.assertEqual(await self.store.confirm("TRK00001", "live_tracking", True), 1)
        self.assertEqual(self.status(cid), Status.DONE.value)

    async def test_unconfirmed_closes_as_done(self) -> None:
        self.store.state = OUTSIDE
        cid = await self.store.enqueue("TRK00001", Command.BUZZER, "on")
        await self.worker.tick()
        self.advance(121)
        await self.worker.tick()
        self.assertEqual(self.status(cid), Status.DONE.value)
        self.assertEqual(self.store.rows[cid]["last_error"], REASON_UNCONFIRMED)
        self.assertEqual(len(self.client.calls), 1)

    async def test_home_defer_then_send_after_walk_starts(self) -> None:
        self.store.state = HOME
        cid = await self.store.enqueue("TRK00001", Command.LIVE, "on")
        await self.worker.tick()
        self.assertEqual(self.status(cid), Status.DEFERRED.value)
        self.assertEqual(self.store.rows[cid]["last_error"], REASON_HOME)
        self.assertEqual(self.store.rows[cid]["attempts"], 0)  # odklad není pokus
        self.assertEqual(self.client.calls, [])
        # před uplynutím retry intervalu se nebere
        self.advance(10)
        self.assertEqual(await self.worker.tick(), 0)
        # pes vyrazil ven
        self.store.state = OUTSIDE
        self.advance(25)
        await self.worker.tick()
        self.assertEqual(self.status(cid), Status.SENT.value)
        self.assertEqual(len(self.client.calls), 1)

    async def test_home_timeout_fails(self) -> None:
        self.store.state = HOME
        cid = await self.store.enqueue("TRK00001", Command.LIVE, "on")
        for _ in range(25):  # 25 × 30 s > 600 s
            await self.worker.tick()
            self.advance(30)
        self.assertEqual(self.status(cid), Status.FAILED.value)
        self.assertEqual(self.store.rows[cid]["last_error"], REASON_HOME)
        self.assertEqual(self.client.calls, [])

    async def test_api_error_retries_then_fails(self) -> None:
        self.store.state = OUTSIDE
        self.client.fail_with = TractiveError("boom")
        cid = await self.store.enqueue("TRK00001", Command.LED, "on")
        await self.worker.tick()
        self.assertEqual((self.status(cid), self.store.rows[cid]["attempts"]), (Status.DEFERRED.value, 1))
        self.advance(30)
        await self.worker.tick()
        self.assertEqual((self.status(cid), self.store.rows[cid]["attempts"]), (Status.DEFERRED.value, 2))
        self.advance(60)  # lineární backoff 30 × 2
        await self.worker.tick()
        self.assertEqual(self.status(cid), Status.FAILED.value)
        self.assertTrue(self.store.rows[cid]["last_error"].startswith(REASON_MAX_ATTEMPTS))
        self.assertEqual(len(self.client.calls), 3)

    async def test_interrupted_sending_is_requeued(self) -> None:
        cid = await self.store.enqueue("TRK00001", Command.BUZZER, "off")
        self.store.rows[cid]["status"] = "sending"  # pád uprostřed odesílání
        self.assertEqual(await self.store.requeue_interrupted(), 1)
        self.assertEqual(self.status(cid), Status.DEFERRED.value)


# ---------------------------------------------------------------------- volitelně proti Postgresu


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL není nastavené")
class PgCommandStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from tractive_ingestor.commands import PgCommandStore
        from tractive_ingestor.storage import PgStorage

        self.storage = PgStorage(os.environ["TEST_DATABASE_URL"])
        await self.storage.connect()
        await self.storage.apply_schema()
        await self.storage.pool.execute("DELETE FROM commands WHERE tracker_id = 'TEST-TRK'")
        self.store = PgCommandStore(self.storage.pool)

    async def asyncTearDown(self) -> None:
        await self.storage.pool.execute("DELETE FROM commands WHERE tracker_id = 'TEST-TRK'")
        await self.storage.close()

    async def status(self, cid: str) -> Any:
        return await self.storage.pool.fetchrow("SELECT status, attempts, last_error FROM commands WHERE id = $1", cid)

    async def test_lifecycle(self) -> None:
        cid = await self.store.enqueue("TEST-TRK", Command.LIVE, "on")
        claimed = await self.store.claim(10)
        self.assertIn(cid, [c.id for c in claimed])
        self.assertEqual((await self.status(cid))["status"], "sending")
        self.assertNotIn(cid, [c.id for c in await self.store.claim(10)])  # žádné dvojí převzetí
        await self.store.mark_deferred(cid, REASON_HOME, 3600)
        self.assertEqual((await self.status(cid))["status"], "deferred")
        self.assertNotIn(cid, [c.id for c in await self.store.claim(10)])  # ještě není splatný
        await self.storage.pool.execute("UPDATE commands SET next_attempt_at = now() WHERE id = $1", cid)
        self.assertIn(cid, [c.id for c in await self.store.claim(10)])
        await self.store.mark_sent(cid, {"pending": True})
        row = await self.status(cid)
        self.assertEqual((row["status"], row["attempts"], row["last_error"]), ("sent", 1, None))
        await self.store.mark_failed(cid, "x")  # guard: sent se už nepřepíše
        self.assertEqual((await self.status(cid))["status"], "sent")
        self.assertEqual(await self.store.confirm("TEST-TRK", "live_tracking", True), 1)
        self.assertEqual((await self.status(cid))["status"], "done")

    async def test_tracker_state_empty(self) -> None:
        self.assertIsNone((await self.store.tracker_state("TEST-TRK-NONE")).in_home_zone())


if __name__ == "__main__":
    unittest.main()
