"""Jednoduchý PIN login: podepsaná session cookie + in-memory rate limit.

Cookie `maja_session` = `<issued_unix_ts>.<hex HMAC-SHA256(APP_SESSION_SECRET, issued_unix_ts)>`,
platnost 90 dní. Když APP_PIN není nastavený, autentizace je vypnutá (lokální vývoj).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import threading
import time
from collections import deque
from collections.abc import Mapping

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


# Jméno tokenu v logu a v API_READ_TOKENS (name=token). Samotný secret se neloguje.
_TOKEN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")

# Read-only token vidí jen posledních 48 h trasy a nanejvýš tolik bodů
# (cookie/PIN session má plné API_POSITIONS_* okno).
READ_TOKEN_POSITION_MAX_AGE_S = 48 * 3600
READ_TOKEN_POSITION_MAX_POINTS = 2000


def parse_read_tokens(raw: str | None) -> list[tuple[str, str]]:
    """`name=token` páry, nebo starý seznam tokenů oddělených čárkou.

    Krátké (< 32 znaků) se ignorují. Nepojmenované tokeny dostanou stabilní jméno
    `token-1`, `token-2` podle pořadí, ať log neobsahuje secret. Když jméno
    nesedí na `name=token` (třeba base64 s `=`), použije se celý záznam.
    """
    if not raw:
        return []
    entries: list[tuple[str, str]] = []
    used: set[str] = set()
    anon = 0
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name: str | None = None
        secret = part
        if "=" in part:
            raw_name, _, value = part.partition("=")
            raw_name, value = raw_name.strip(), value.strip()
            if len(value) >= 32 and _TOKEN_NAME.match(raw_name):
                name, secret = raw_name, value
        if name is None:
            if len(secret) < 32:
                continue
            anon += 1
            name = f"token-{anon}"
        base = name
        n = 2
        while name in used:
            name = f"{base}-{n}"
            n += 1
        used.add(name)
        entries.append((name, secret))
    return entries


def match_read_token(entries: list[tuple[str, str]], given: str | None) -> str | None:
    """Jméno sedícího tokenu. Stejně jako `check_read_token` porovná všechny, bez předčasného returnu."""
    if not given or not entries:
        return None
    matched: str | None = None
    for name, token in entries:
        if hmac.compare_digest(token.encode("utf-8"), given.encode("utf-8")) and matched is None:
            matched = name
    return matched


# CF-Connecting-IP / X-Forwarded-For bereme jen od lokálního proxy.
# Loopback: cloudflared nebo uvicorn na hostu. 172.16/12: docker-proxy, když compose
# publikuje port (v kontejneru je peer brána bridge, ne 127.0.0.1).
_TRUSTED_PROXY_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("172.16.0.0/12"),
)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = value.strip().strip('"')
    if not text:
        return None
    if text.startswith("[") and "]" in text:
        text = text[1:text.index("]")]
    if "%" in text:
        text = text.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_trusted_proxy(peer: str | None) -> bool:
    ip = _parse_ip(peer or "")
    if ip is None:
        return False
    return any(ip in net for net in _TRUSTED_PROXY_NETWORKS)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    get = getattr(headers, "get", None)
    if get is not None:
        value = get(name)
        if value:
            return str(value)
        if name.lower() != name:
            value = get(name.lower())
            if value:
                return str(value)
    lowered = name.lower()
    for key, item in headers.items():
        if str(key).lower() == lowered and item:
            return str(item)
    return None


def _forwarded_client_ip(headers: Mapping[str, str]) -> str | None:
    """CF-Connecting-IP má přednost (Cloudflare ho přepisuje). Jinak první adresa z X-Forwarded-For."""
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        raw = _header(headers, header)
        if not raw:
            continue
        ip = _parse_ip(raw.split(",")[0])
        if ip is not None:
            return str(ip)
    return None


def client_address(peer: str | None, headers: Mapping[str, str] | None = None) -> str:
    """IP pro rate limit. Forwardované hlavičky jen když peer je důvěryhodný lokální proxy."""
    if _is_trusted_proxy(peer):
        forwarded = _forwarded_client_ip(headers or {})
        if forwarded:
            return forwarded[:64]
    if peer and peer.strip():
        return peer.strip()[:64]
    return "unknown"


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

    Žádný globální strop: neúspěchy cizích klientů nezamknou platný token ani PIN majitele.
    PIN/passkey a read-only token mají každý vlastní instanci.
    """

    def __init__(self, max_failures: int = 5, window: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window = window
        self._fails: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, q: deque[float], now: float) -> None:
        while q and now - q[0] >= self.window:
            q.popleft()

    def blocked(self, ip: str, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        with self._lock:
            q = self._fails.get(ip)
            if q is None:
                return False
            self._prune(q, now)
            if not q:
                del self._fails[ip]
                return False
            return len(q) >= self.max_failures

    def fail(self, ip: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            q = self._fails.get(ip)
            if q is None:
                if len(self._fails) >= 10_000:  # ochrana paměti; nejstarší cizí IP, ne tenhle záznam
                    self._fails.pop(next(iter(self._fails)), None)
                q = deque()
                self._fails[ip] = q
            q.append(now)

    def success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
