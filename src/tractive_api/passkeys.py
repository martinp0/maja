"""Passkeys (WebAuthn) – přihlášení přes Face ID / Touch ID místo hesla.

- Registrace jen s platnou session (nejdřív heslo), přihlášení passkey vydá stejnou cookie.
- Challenge se drží na serveru (in-memory, TTL `CHALLENGE_TTL_S`) pod náhodným id v krátké
  HttpOnly cookie `maja_wa`; každý challenge jde použít jen jednou.
- Jediný „uživatel“ (rodinná appka): user.id je pevný, rozlišují se jen zařízení/credentials.
- userVerification = required (Face ID/Touch ID/kód zařízení), residentKey = preferred, platform.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

COOKIE_NAME = "maja_wa"
CHALLENGE_TTL_S = 180.0
USER_ID = b"maja-owner-v1"
USER_NAME = "maja"
USER_DISPLAY = "Mája"


@dataclass(slots=True, frozen=True)
class PendingChallenge:
    challenge: bytes
    kind: str  # "register" | "login"
    expires: float


class ChallengeStore:
    def __init__(self, ttl_s: float = CHALLENGE_TTL_S, max_items: int = 1000) -> None:
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._items: dict[str, PendingChallenge] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        for k in [k for k, v in self._items.items() if v.expires <= now]:
            del self._items[k]

    def put(self, challenge: bytes, kind: str, now: float | None = None) -> str:
        now = now if now is not None else time.monotonic()
        key = secrets.token_urlsafe(24)
        with self._lock:
            self._prune(now)
            if len(self._items) >= self.max_items:  # ochrana paměti (anonymní /login/options)
                oldest = min(self._items, key=lambda k: self._items[k].expires)
                del self._items[oldest]
            self._items[key] = PendingChallenge(challenge, kind, now + self.ttl_s)
        return key

    def pop(self, key: str | None, kind: str, now: float | None = None) -> bytes | None:
        """Jednorázově vyzvedne challenge (None = neexistuje / vypršel / jiný druh)."""
        if not key:
            return None
        now = now if now is not None else time.monotonic()
        with self._lock:
            item = self._items.pop(key, None)
        if item is None or item.expires <= now or item.kind != kind:
            return None
        return item.challenge


def device_name(user_agent: str | None) -> str:
    ua = user_agent or ""
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    if "Android" in ua:
        return "Android"
    if "Macintosh" in ua:
        return "Mac"
    if "Windows" in ua:
        return "Windows"
    return "Zařízení"
