"""Jednoduchý PIN login: podepsaná session cookie + in-memory rate limit.

Cookie `maja_session` = `<issued_unix_ts>.<hex HMAC-SHA256(APP_SESSION_SECRET, issued_unix_ts)>`,
platnost 90 dní. Když APP_PIN není nastavený, autentizace je vypnutá (lokální vývoj).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from collections import deque

COOKIE_NAME = "maja_session"
SESSION_MAX_AGE = 90 * 24 * 3600  # 7776000 s

# Cesty pod /api, které jsou dostupné bez přihlášení
PUBLIC_API_PATHS = frozenset({"/api/login", "/api/logout", "/api/me", "/api/health", "/api/ready",
                              "/api/webauthn/login/options", "/api/webauthn/login/verify"})


# Read-only bearer token (nativní appky): jen GET/HEAD na tyhle cesty. `{id}` = tracker id.
READ_TOKEN_PATHS = (
    re.compile(r"^/api/(pets|trackers)$"),
    re.compile(r"^/api/trackers/[A-Za-z0-9_-]{1,64}/(latest|positions|health)$"),
    re.compile(r"^/api/geofence/(state|events)$"),
)


def read_token_allows(method: str, path: str) -> bool:
    return method in ("GET", "HEAD") and any(p.match(path) for p in READ_TOKEN_PATHS)


def bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def check_read_token(tokens: list[str], given: str | None) -> bool:
    """Konstantní čas přes všechny tokeny (nevyzradí, kolikátý sedí)."""
    if not given or not tokens:
        return False
    ok = False
    for t in tokens:
        ok |= hmac.compare_digest(t.encode("utf-8"), given.encode("utf-8"))
    return ok


def _sign(secret: bytes, issued: str) -> str:
    return hmac.new(secret, issued.encode("ascii"), hashlib.sha256).hexdigest()


def make_token(secret: bytes, now: float | None = None) -> str:
    issued = str(int(now if now is not None else time.time()))
    return f"{issued}.{_sign(secret, issued)}"


def verify_token(secret: bytes, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    issued, _, sig = token.partition(".")
    if not issued.isdigit() or len(issued) > 12:
        return False
    if not hmac.compare_digest(_sign(secret, issued), sig):
        return False
    age = (now if now is not None else time.time()) - int(issued)
    return -300 <= age < SESSION_MAX_AGE  # malá tolerance na posun hodin


def check_pin(expected: str, given: str | None) -> bool:
    if not isinstance(given, str):
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))


class LoginRateLimiter:
    """Po `max_failures` neúspěších z jedné IP během `window` s vrací blokaci (429).

    Navíc globální strop (`global_max`) pro případ, že IP klienta nejde spolehlivě zjistit
    (vše přes tunel z 127.0.0.1) nebo někdo střídá IP.
    """

    def __init__(self, max_failures: int = 5, window: float = 300.0, global_max: int = 30) -> None:
        self.max_failures = max_failures
        self.window = window
        self.global_max = global_max
        self._fails: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.window:
            q.popleft()

    def blocked(self, ip: str, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._prune(self._global, now)
            q = self._fails.get(ip)
            if q is not None:
                self._prune(q, now)
                if not q:
                    del self._fails[ip]
                    q = None
            return len(self._global) >= self.global_max or (q is not None and len(q) >= self.max_failures)

    def fail(self, ip: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._fails.setdefault(ip, deque()).append(now)
            self._global.append(now)
            if len(self._fails) > 10_000:  # ochrana paměti
                self._fails.clear()

    def success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
