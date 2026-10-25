"""FastAPI aplikace: /api/* (čtení z PostGIS + zařazení povelu) a PWA z web/dist na /.

Jeden kontejner, jeden port: PWA volá same-origin `/api`, takže CORS je potřeba jen
pro vývoj (`npm run dev`). Autentizace: PIN login (APP_PIN) -> podepsaná cookie
`maja_session`; bez platné cookie vrací /api/* 401 (kromě login/logout/me/health/ready).
PWA shell na / je veřejný – data jsou za loginem. Bez APP_PIN je auth vypnutá.
Nativní appky (iPhone/widget/Watch) se místo cookie hlásí read-only tokenem
(`Authorization: Bearer`, API_READ_TOKENS) – jen GET na polohu, baterii a doma/venku.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import secrets

import structlog
from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.types import Scope
import webauthn
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from tractive_ingestor.commands import COMMAND_ALIASES, REASON_HOME, parse_command
from tractive_ingestor.push import InvalidSubscription, Sender, WebPushSender, make_payload

from .auth import (
    COOKIE_NAME,
    PUBLIC_API_PATHS,
    SESSION_MAX_AGE,
    LoginRateLimiter,
    bearer_token,
    check_pin,
    check_read_token,
    make_token,
    read_token_allows,
    verify_token,
)
from .config import ApiSettings, get_api_settings
from .passkeys import COOKIE_NAME as WA_COOKIE
from .passkeys import USER_DISPLAY as PK_USER_DISPLAY
from .passkeys import USER_ID as PK_USER_ID
from .passkeys import USER_NAME as PK_USER_NAME
from .passkeys import ChallengeStore, device_name
from .store import ApiStore, PgApiStore, StoreUnavailable

log = structlog.get_logger(__name__)

TrackerId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]{1,64}$", description="Tractive tracker id")]


class LoginRequest(BaseModel):
    """Heslo/PIN. Pole `pin` zůstává kvůli kompatibilitě, `password` je alias (má přednost)."""

    pin: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=256)

    @property
    def secret(self) -> str | None:
        return self.password if self.password is not None else self.pin


def client_ip(request: Request) -> str:
    # Za Cloudflare Tunnelem přichází vše z loopbacku; CF-Connecting-IP nastavuje (a přepisuje)
    # Cloudflare edge. Port API je publikovaný jen na loopback, takže hlavičku nejde obejít zvenku.
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()[:64]
    return request.client.host if request.client else "unknown"


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=64)


class PushSubscribeRequest(BaseModel):
    """Výstup `PushSubscription.toJSON()` z prohlížeče (expirationTime se ignoruje)."""

    endpoint: str = Field(max_length=2048)
    keys: PushKeys


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(max_length=2048)


class CommandRequest(BaseModel):
    command: str = Field(description="live | buzzer | led (aliasy: sound, light, live_tracking, …)", max_length=32)
    action: str = Field(default="on", description="on | off", max_length=8)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Očekávám JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Očekávám JSON objekt")
    return body


def _public_cred(c: dict[str, Any]) -> dict[str, Any]:
    """Passkey pro UI – bez veřejného klíče; credential_id jako base64url (k rozpoznání zařízení)."""
    import base64

    return {
        "id": c["id"],
        "credential_id": base64.urlsafe_b64encode(bytes(c["credential_id"])).rstrip(b"=").decode(),
        "name": c.get("name"),
        "backed_up": c.get("backed_up"),
        "created_at": c.get("created_at"),
        "last_used_at": c.get("last_used_at"),
    }


def get_store(request: Request) -> ApiStore:
    return request.app.state.store


# Aliasy musí být na úrovni modulu (from __future__ import annotations -> FastAPI je resolvuje přes globals)
StoreDep = Annotated[ApiStore, Depends(get_store)]


async def require_tracker(tracker_id: TrackerId, store: StoreDep) -> str:
    if not await store.tracker_exists(tracker_id):
        raise HTTPException(status_code=404, detail=f"Tracker {tracker_id} neexistuje")
    return tracker_id


KnownTracker = Annotated[str, Depends(require_tracker)]


def _db_errors() -> tuple[type[BaseException], ...]:
    errs: list[type[BaseException]] = [StoreUnavailable, OSError]
    try:
        import asyncpg

        errs += [asyncpg.PostgresError, asyncpg.InterfaceError]
    except ImportError:  # pragma: no cover
        pass
    return tuple(errs)


class WebStatic(StaticFiles):
    """StaticFiles s rozumnými cache hlavičkami pro PWA.

    Hashované `assets/*` = immutable; index.html, service worker a manifest = no-cache,
    jinak by si telefon držel starou verzi appky.
    """

    async def get_response(self, path: str, scope: Scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        if path.startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app(settings: ApiSettings | None = None, store: ApiStore | None = None,
               push_sender: Sender | None = None) -> FastAPI:
    settings = settings or get_api_settings()
    store = store or PgApiStore(settings.dsn, min_size=settings.api_db_pool_min, max_size=settings.api_db_pool_max)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await store.connect()
        except StoreUnavailable as exc:  # API naběhne i bez DB, další request zkusí znovu
            log.warning("api.db_unavailable_at_start", error=str(exc))
        yield
        await store.close()

    app = FastAPI(title="Tractive API (Mája)", version="0.1.0", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.state.store = store
    app.state.settings = settings

    pin = settings.pin
    secret = settings.session_secret
    if pin and not secret:
        secret = secrets.token_bytes(32)
        log.warning("api.auth_ephemeral_secret", hint="APP_SESSION_SECRET není nastavený – session po restartu neplatí")
    if pin:
        log.info("api.auth_enabled")
    else:
        log.warning("api.auth_disabled", hint="APP_PIN není nastavený – API je bez přihlášení")
    limiter = LoginRateLimiter()
    app.state.login_limiter = limiter
    read_tokens = settings.read_tokens
    if read_tokens:
        log.info("api.read_tokens_enabled", count=len(read_tokens))

    def is_authed(request: Request) -> bool:
        if not pin:
            return True
        return verify_token(secret, request.cookies.get(COOKIE_NAME))  # type: ignore[arg-type]

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if (
            pin
            and request.method != "OPTIONS"
            and (path == "/api" or path.startswith("/api/"))
            and path.rstrip("/") not in PUBLIC_API_PATHS
            and not is_authed(request)
        ):
            # Nativní appky: read-only Bearer token (API_READ_TOKENS), jen GET na čtecí endpointy.
            token = bearer_token(request.headers.get("authorization"))
            if token is not None and read_tokens:
                ip = client_ip(request)
                if limiter.blocked(ip):
                    return JSONResponse(status_code=429, content={"detail": "Moc pokusů"},
                                        headers={"Retry-After": str(int(limiter.window)), "Cache-Control": "no-store"})
                if not check_read_token(read_tokens, token):
                    limiter.fail(ip)
                    log.warning("api.read_token_failed", ip=ip)
                    return JSONResponse(status_code=401, content={"detail": "Neplatný token"},
                                        headers={"Cache-Control": "no-store"})
                if not read_token_allows(request.method, path.rstrip("/")):
                    return JSONResponse(status_code=403, content={"detail": "Token je jen pro čtení"},
                                        headers={"Cache-Control": "no-store"})
            else:
                return JSONResponse(status_code=401, content={"detail": "Nepřihlášeno"},
                                    headers={"Cache-Control": "no-store"})
        resp = await call_next(request)
        # Data z API se nikdy nesmí brát z cache (prohlížeč, service worker, Cloudflare) – vždy čerstvá
        if (path == "/api" or path.startswith("/api/")) and "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            allow_credentials=True,
        )

    for exc_type in _db_errors():

        @app.exception_handler(exc_type)
        async def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
            log.warning("api.db_error", path=request.url.path, error=repr(exc))
            return JSONResponse(status_code=503, content={"detail": "Databáze je nedostupná"})

    # ------------------------------------------------------------------ auth

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request, response: Response) -> Any:
        if not pin:
            return {"ok": True, "auth": "disabled"}
        ip = client_ip(request)
        if limiter.blocked(ip):
            log.warning("api.login_rate_limited", ip=ip)
            return JSONResponse(status_code=429, content={"ok": False, "detail": "Moc pokusů"},
                                headers={"Retry-After": str(int(limiter.window)), "Cache-Control": "no-store"})
        if not check_pin(pin, body.secret):
            limiter.fail(ip)
            log.warning("api.login_failed", ip=ip)
            return JSONResponse(status_code=401, content={"ok": False}, headers={"Cache-Control": "no-store"})
        limiter.success(ip)
        log.info("api.login_ok", ip=ip)
        response.headers["Cache-Control"] = "no-store"
        response.set_cookie(COOKIE_NAME, make_token(secret), max_age=SESSION_MAX_AGE, path="/",  # type: ignore[arg-type]
                            httponly=True, secure=True, samesite="lax")
        return {"ok": True}

    @app.post("/api/logout")
    async def logout(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=True, samesite="lax")
        return {"ok": True}

    @app.get("/api/me")
    async def me(request: Request, response: Response, store: StoreDep) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        try:
            passkeys: int | None = await store.webauthn_count()
        except Exception:  # bez DB / bez tabulky (neproběhl init-db) – přihlašovací obrazovka musí jet dál
            passkeys = None
        return {"authed": is_authed(request), "auth_enabled": bool(pin), "passkey_count": passkeys,
                "webauthn_rp_id": settings.webauthn_rp_id}

    # ------------------------------------------------------------------ passkeys (WebAuthn / Face ID)

    challenges = ChallengeStore()
    app.state.webauthn_challenges = challenges
    rp_id = settings.webauthn_rp_id
    origins = settings.webauthn_origins

    def _set_wa_cookie(response: Response, key: str) -> None:
        response.set_cookie(WA_COOKIE, key, max_age=int(challenges.ttl_s), path="/api/webauthn",
                            httponly=True, secure=True, samesite="strict")

    def _clear_wa_cookie(response: Response) -> None:
        response.delete_cookie(WA_COOKIE, path="/api/webauthn", httponly=True, secure=True, samesite="strict")

    def _wa_json(options: Any, key: str) -> Response:
        resp = Response(content=webauthn.options_to_json(options), media_type="application/json",
                        headers={"Cache-Control": "no-store"})
        _set_wa_cookie(resp, key)
        return resp

    def _rate_limited(ip: str) -> JSONResponse:
        log.warning("api.login_rate_limited", ip=ip, method="passkey")
        return JSONResponse(status_code=429, content={"ok": False, "detail": "Moc pokusů"},
                            headers={"Retry-After": str(int(limiter.window)), "Cache-Control": "no-store"})

    def _descriptor(c: dict[str, Any]) -> Any:
        transports = []
        for t in c.get("transports") or []:
            try:
                transports.append(AuthenticatorTransport(t))
            except ValueError:
                pass
        return PublicKeyCredentialDescriptor(id=bytes(c["credential_id"]), transports=transports or None)

    @app.post("/api/webauthn/register/options")
    async def wa_register_options(store: StoreDep) -> Response:
        """Jen přihlášený (gate middleware). Vyloučí už registrované passkeys."""
        existing = await store.webauthn_list()
        challenge = secrets.token_bytes(32)
        options = webauthn.generate_registration_options(
            rp_id=rp_id, rp_name=settings.webauthn_rp_name, user_id=PK_USER_ID, user_name=PK_USER_NAME,
            user_display_name=PK_USER_DISPLAY, challenge=challenge, timeout=120_000,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[_descriptor(c) for c in existing],
            supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.EDDSA,
                                    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256],
        )
        return _wa_json(options, challenges.put(challenge, "register"))

    @app.post("/api/webauthn/register/verify", status_code=201)
    async def wa_register_verify(request: Request, response: Response, store: StoreDep) -> Any:
        body = await _json_body(request)
        challenge = challenges.pop(request.cookies.get(WA_COOKIE), "register")
        _clear_wa_cookie(response)
        if challenge is None:
            raise HTTPException(status_code=400, detail="Výzva vypršela, zkus to znovu")
        try:
            v = webauthn.verify_registration_response(
                credential=body.get("credential", body), expected_challenge=challenge, expected_rp_id=rp_id,
                expected_origin=origins, require_user_verification=True)
        except Exception as exc:  # InvalidRegistrationResponse, špatný JSON…
            log.warning("api.passkey_register_failed", error=str(exc)[:200])
            raise HTTPException(status_code=400, detail="Registrace passkey se nepovedla") from None
        if await store.webauthn_get(v.credential_id) is not None:
            raise HTTPException(status_code=409, detail="Tenhle passkey už je zaregistrovaný")
        cred = body.get("credential", body)
        transports = cred.get("response", {}).get("transports") if isinstance(cred, dict) else None
        name = body.get("name") if isinstance(body.get("name"), str) else None
        row = await store.webauthn_add({
            "credential_id": v.credential_id, "public_key": v.credential_public_key, "sign_count": v.sign_count,
            "transports": [str(t)[:32] for t in transports][:8] if isinstance(transports, list) else None,
            "name": (name or device_name(request.headers.get("user-agent")))[:64],
            "aaguid": v.aaguid, "backed_up": bool(v.credential_backed_up),
        })
        log.info("api.passkey_registered", id=row["id"], name=row["name"])
        response.headers["Cache-Control"] = "no-store"
        return {"ok": True, "credential": _public_cred(row)}

    @app.post("/api/webauthn/login/options")
    async def wa_login_options(request: Request, store: StoreDep) -> Response:
        ip = client_ip(request)
        if limiter.blocked(ip):
            return _rate_limited(ip)
        creds = await store.webauthn_list()
        if not creds:
            return JSONResponse(status_code=404, content={"detail": "Žádný passkey není zaregistrovaný"},
                                headers={"Cache-Control": "no-store"})
        challenge = secrets.token_bytes(32)
        options = webauthn.generate_authentication_options(
            rp_id=rp_id, challenge=challenge, timeout=120_000,
            allow_credentials=[_descriptor(c) for c in creds],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return _wa_json(options, challenges.put(challenge, "login"))

    @app.post("/api/webauthn/login/verify")
    async def wa_login_verify(request: Request, store: StoreDep) -> Any:
        ip = client_ip(request)
        if limiter.blocked(ip):
            return _rate_limited(ip)
        body = await _json_body(request)
        challenge = challenges.pop(request.cookies.get(WA_COOKIE), "login")

        def fail(status: int, detail: str) -> JSONResponse:
            limiter.fail(ip)
            log.warning("api.login_failed", ip=ip, method="passkey", reason=detail)
            resp = JSONResponse(status_code=status, content={"ok": False, "detail": detail},
                                headers={"Cache-Control": "no-store"})
            _clear_wa_cookie(resp)
            return resp

        if challenge is None:
            return fail(400, "Výzva vypršela, zkus to znovu")
        cred = body.get("credential", body)
        try:
            raw_id = webauthn.base64url_to_bytes(cred["rawId"] if "rawId" in cred else cred["id"])
        except Exception:
            return fail(400, "Neplatná odpověď")
        stored = await store.webauthn_get(raw_id)
        if stored is None:
            return fail(401, "Neznámý passkey")
        try:
            v = webauthn.verify_authentication_response(
                credential=cred, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origins,
                credential_public_key=bytes(stored["public_key"]),
                credential_current_sign_count=int(stored["sign_count"]), require_user_verification=True)
        except Exception as exc:
            log.warning("api.passkey_verify_failed", error=str(exc)[:200])
            return fail(401, "Ověření passkey selhalo")
        await store.webauthn_touch(raw_id, v.new_sign_count)
        limiter.success(ip)
        log.info("api.login_ok", ip=ip, method="passkey", credential=stored["id"])
        resp = JSONResponse(content={"ok": True, "credential_id": stored["id"]}, headers={"Cache-Control": "no-store"})
        _clear_wa_cookie(resp)
        if pin:
            resp.set_cookie(COOKIE_NAME, make_token(secret), max_age=SESSION_MAX_AGE, path="/",  # type: ignore[arg-type]
                            httponly=True, secure=True, samesite="lax")
        return resp

    @app.get("/api/webauthn/credentials")
    async def wa_list(response: Response, store: StoreDep) -> list[dict[str, Any]]:
        response.headers["Cache-Control"] = "no-store"
        return [_public_cred(c) for c in await store.webauthn_list()]

    @app.post("/api/webauthn/credentials/{cred_id}/delete")
    async def wa_delete(cred_id: int, store: StoreDep) -> dict[str, Any]:
        if not await store.webauthn_delete(cred_id):
            raise HTTPException(status_code=404, detail="Passkey neexistuje")
        log.info("api.passkey_deleted", id=cred_id)
        return {"ok": True}

    # ------------------------------------------------------------------ routes

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """Liveness – nesahá do DB."""
        return {"status": "ok", "time": datetime.now(UTC)}

    @app.get("/api/ready")
    async def ready(store: StoreDep) -> dict[str, Any]:
        """Readiness – ověří spojení s DB (503, když neběží)."""
        await store.ping()
        return {"status": "ready"}

    @app.get("/api/pets")
    async def pets(store: StoreDep) -> list[dict[str, Any]]:
        return await store.list_pets()

    @app.get("/api/trackers")
    async def trackers(store: StoreDep) -> list[dict[str, Any]]:
        return await store.list_trackers()

    @app.get("/api/trackers/{tracker_id}/latest")
    async def latest(tracker_id: KnownTracker, store: StoreDep) -> dict[str, Any]:
        data = await store.latest(tracker_id)
        data["in_home_zone"] = await store.in_home_zone(tracker_id)
        return data

    @app.get("/api/trackers/{tracker_id}/positions")
    async def positions(
        tracker_id: KnownTracker,
        store: StoreDep,
        since: datetime | None = Query(default=None, description="ISO 8601; default = posledních API_POSITIONS_DEFAULT_HOURS h"),
        limit: int = Query(default=2000, ge=1),
    ) -> dict[str, Any]:
        if since is None:
            since = datetime.now(UTC) - timedelta(hours=settings.api_positions_default_hours)
        elif since.tzinfo is None:
            since = since.replace(tzinfo=UTC)  # bez zóny = UTC
        limit = min(limit, settings.api_positions_max_limit)
        pts = await store.positions(tracker_id, since, limit)
        return {"tracker_id": tracker_id, "since": since, "limit": limit, "count": len(pts), "positions": pts}

    @app.get("/api/trackers/{tracker_id}/health")
    async def health_data(
        tracker_id: KnownTracker, store: StoreDep, days: int = Query(default=7, ge=1, le=60)
    ) -> dict[str, Any]:
        return await store.health(tracker_id, days)

    @app.get("/api/trackers/{tracker_id}/commands")
    async def commands(tracker_id: KnownTracker, store: StoreDep, limit: int = Query(default=20, ge=1)) -> list[dict[str, Any]]:
        return await store.list_commands(tracker_id, min(limit, settings.api_commands_max_limit))

    @app.post("/api/trackers/{tracker_id}/commands", status_code=201)
    async def enqueue(tracker_id: KnownTracker, body: CommandRequest, store: StoreDep) -> dict[str, Any]:
        """Zařadí povel do fronty (`commands`, status `queued`). Odešle ho běžící ingestor."""
        try:
            cmd, action = parse_command(body.command, body.action)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        row = await store.enqueue_command(tracker_id, cmd, action)
        home = await store.in_home_zone(tracker_id)
        log.info("api.command_enqueued", command_id=row["id"], tracker_id=tracker_id, command=cmd.value, action=action)
        row["in_home_zone"] = home
        row["note"] = (
            "Pes je doma (WiFi / power-saving zóna) – povel se odloží, dokud nebude venku."
            if home
            else None
        )
        row["home_reason"] = REASON_HOME if home else None
        return row

    # ------------------------------------------------------------------ geofence doma / venku

    home_cfg = settings.home_config()
    if home_cfg is None:
        log.info("api.geofence_disabled", hint="nastav HOME_LAT a HOME_LON v .env")

    async def _resolve_tracker(store: ApiStore, tracker_id: str | None) -> str | None:
        if tracker_id:
            if not await store.tracker_exists(tracker_id):
                raise HTTPException(status_code=404, detail=f"Tracker {tracker_id} neexistuje")
            return tracker_id
        return await store.default_tracker()

    @app.get("/api/geofence/state")
    async def geofence_state(
        store: StoreDep,
        response: Response,
        tracker_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$",
                                       description="default = první tracker"),
    ) -> dict[str, Any]:
        """Stav doma/venku (s debouncingem). Při každém dotazu se stav inkrementálně dopočítá."""
        response.headers["Cache-Control"] = "no-store"
        tid = await _resolve_tracker(store, tracker_id)
        out: dict[str, Any] = {
            "enabled": home_cfg is not None,
            "tracker_id": tid,
            "state": "unknown",
            "since": None,
            "distance_m": None,
            "in_home_zone": None,
            "home_radius_m": settings.home_radius_m,
            "home": None,
            "escape_m": settings.geofence_escape_m,
            "escaped": False,
            "pending_state": None,
            "pending_since": None,
            "last_fix_ts": None,
            "schema_ready": True,
        }
        if tid is None:
            return out
        out["in_home_zone"] = await store.in_home_zone(tid)
        if home_cfg is None:
            return out
        # appka je za PINem -> střed domova smí API vrátit (kreslí se kruh); v gitu ani v PWA není
        out["home"] = {"lat": home_cfg.lat, "lng": home_cfg.lon}
        pos = await store.latest_position(tid)
        if pos is not None:
            out["distance_m"] = round(home_cfg.distance_m(pos["lat"], pos["lng"]), 1)
            out["last_fix_ts"] = pos["ts"]
        cp = await store.geofence_evaluate(tid, home_cfg)
        if cp is None:
            out["schema_ready"] = False
            return out
        out.update(state=cp["state"], since=cp["since"], pending_state=cp["pending_state"],
                   pending_since=cp["pending_since"])
        out["escaped"] = bool(
            cp["state"] == "away" and out["distance_m"] is not None and out["distance_m"] > home_cfg.escape_m
        )
        return out

    @app.get("/api/geofence/events")
    async def geofence_events(
        store: StoreDep,
        tracker_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        tid = await _resolve_tracker(store, tracker_id)
        if tid is None:
            return []
        return await store.geofence_events(tid, limit)

    # ------------------------------------------------------------------ Web Push

    push_cfg = settings.push_config()
    if push_cfg is not None and push_sender is None:
        push_sender = WebPushSender(push_cfg)
    if push_cfg is None:
        log.info("api.push_disabled", hint="nastav VAPID_PUBLIC_KEY a VAPID_PRIVATE_KEY v .env")

    def _require_push() -> Sender:
        if push_cfg is None or push_sender is None:
            raise HTTPException(status_code=503, detail="Upozornění nejsou na serveru nastavená (VAPID)")
        return push_sender

    @app.get("/api/push/vapid-public-key")
    async def push_public_key(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return {"enabled": push_cfg is not None, "public_key": push_cfg.public_key if push_cfg else None}

    @app.post("/api/push/subscribe", status_code=201)
    async def push_subscribe(body: PushSubscribeRequest, request: Request, store: StoreDep) -> dict[str, Any]:
        _require_push()
        try:
            sid = await store.push_subscribe(body.endpoint, body.keys.p256dh, body.keys.auth,
                                             request.headers.get("user-agent"))
        except InvalidSubscription as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        log.info("api.push_subscribed", id=sid)
        return {"ok": True, "id": sid}

    @app.post("/api/push/unsubscribe")
    async def push_unsubscribe(body: PushUnsubscribeRequest, store: StoreDep) -> dict[str, Any]:
        removed = await store.push_unsubscribe(body.endpoint)
        return {"ok": True, "removed": removed}

    @app.post("/api/push/test")
    async def push_test(store: StoreDep) -> dict[str, Any]:
        sender = _require_push()
        payload = make_payload("Mája 🐾 test upozornění", "Funguje to! Takhle ti dám vědět, když Mája odejde.",
                               tag="test", kind="test")
        res = await store.push_send_all(sender, payload)
        log.info("api.push_test", **res)
        return {"ok": True, **res}

    @app.get("/api/commands/types")
    async def command_types() -> dict[str, Any]:
        return {"commands": sorted({a for a in COMMAND_ALIASES}), "actions": ["on", "off"]}

    # ------------------------------------------------------------------ PWA

    dist = Path(settings.api_web_dist)
    if (dist / "index.html").is_file():
        app.mount("/", WebStatic(directory=dist, html=True), name="web")
        log.info("api.web_mounted", dist=str(dist))
    else:
        log.info("api.web_missing", dist=str(dist), hint="cd web && npm ci && npm run build")

    return app
