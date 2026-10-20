"""Smoke testy health serveru – bez Tractive účtu a bez DB.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from typing import Any

from tractive_ingestor.channel import ChannelConsumer
from tractive_ingestor.health import HealthServer, HealthState


async def http(port: int, path: str, method: str = "GET") -> tuple[int, dict[str, Any] | None]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split()[1])
    return status, (json.loads(body) if body else None)


class HealthServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.state = HealthState()
        self.server = HealthServer(self.state, "127.0.0.1", 0, ready_max_age_s=90)
        await self.server.start()
        self.port = self.server.bound_port
        assert self.port

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    async def test_healthz_ok(self) -> None:
        status, body = await http(self.port, "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    async def test_readyz_not_ready_initially(self) -> None:
        status, body = await http(self.port, "/readyz")
        self.assertEqual(status, 503)
        self.assertFalse(body["authenticated"])
        self.assertFalse(body["channel_connected"])
        self.assertIsNone(body["last_event_age_s"])
        self.assertIn("not_authenticated", body["reasons"])

    async def test_readyz_ready_then_stale(self) -> None:
        self.state.mark_authenticated("u1")
        self.state.mark_connected()
        self.state.mark_event()
        status, body = await http(self.port, "/readyz")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["reasons"], [])
        # simulace: poslední aktivita před 120 s
        old = time.time() - 120
        self.state.last_event_at = self.state.channel_connected_at = old
        status, body = await http(self.port, "/readyz")
        self.assertEqual(status, 503)
        self.assertIn("channel_stale", body["reasons"])
        # čerstvý keep-alive (z aiotractive Channel) to zase spraví
        self.state.keepalive_source = lambda: time.time() - 5
        status, body = await http(self.port, "/readyz")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["last_keepalive_age_s"], 5.0)

    async def test_readyz_unauthenticated_after_error(self) -> None:
        self.state.mark_authenticated("u1")
        self.state.mark_connected()
        self.state.mark_unauthenticated("401")
        status, body = await http(self.port, "/readyz")
        self.assertEqual(status, 503)
        self.assertEqual(body["last_error"], "401")

    async def test_misc_routes(self) -> None:
        self.assertEqual((await http(self.port, "/nope"))[0], 404)
        self.assertEqual((await http(self.port, "/healthz", "POST"))[0], 405)
        status, body = await http(self.port, "/healthz", "HEAD")
        self.assertEqual((status, body), (200, None))
        # garbage request nesmí shodit server
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b"\x00\x01garbage\r\n\r\n")
        await w.drain()
        self.assertIn(b"400", await r.read())
        w.close()
        self.assertEqual((await http(self.port, "/healthz"))[0], 200)


class FakeClient:
    """Napodobuje TractiveClient.events(): handshake -> 1 event -> čeká -> odpojení."""

    def __init__(self) -> None:
        self.disconnect = asyncio.Event()

    async def events(self, on_connected=None):  # noqa: ANN001
        if on_connected:
            on_connected()
        yield {"message": "tracker_status", "tracker_id": "T1"}
        await self.disconnect.wait()
        raise TimeoutError("simulated disconnect")

    async def force_relogin(self) -> None:  # pragma: no cover
        raise AssertionError


class ChannelWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_consumer_updates_health(self) -> None:
        state = HealthState()
        state.mark_authenticated("u1")
        got: list[dict] = []

        async def handler(ev: dict) -> None:
            got.append(ev)

        client = FakeClient()
        consumer = ChannelConsumer(client, handler, backoff_base_s=0.01, backoff_max_s=0.01, health=state)  # type: ignore[arg-type]
        stop = asyncio.Event()
        task = asyncio.create_task(consumer.run(stop))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if got:
                break
        ready, body = state.readiness()
        self.assertTrue(ready, body)
        self.assertEqual(body["events_received"], 1)

        client.disconnect.set()  # spojení spadne -> not ready, chyba zapsaná, reconnect
        for _ in range(100):
            await asyncio.sleep(0.01)
            if state.reconnects:
                break
        self.assertGreaterEqual(state.reconnects, 1)
        self.assertIn("simulated disconnect", state.last_error or "")
        stop.set()
        await asyncio.wait_for(task, 2)
        self.assertFalse(state.channel_connected)


if __name__ == "__main__":
    unittest.main()
