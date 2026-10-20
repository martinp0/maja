"""Konfigurace z prostředí / .env (pydantic-settings).

Nic se nevaliduje "natvrdo" při importu – chybějící přihlašovací údaje
řeší až `Settings.missing_credentials()`, aby šly moduly importovat bez .env.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .push import DEFAULT_VAPID_SUBJECT


class PushSettings(BaseSettings):
    """Web Push (VAPID) – sdílí ingestor (odesílá upozornění) i API (odběry, test).

    Klíče jen v .env (`python -m tractive_ingestor gen-vapid`). Prázdné = push vypnutý.
    """

    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = DEFAULT_VAPID_SUBJECT
    push_eval_interval_s: float = 30.0  # ingestor: kontrola nových událostí/baterie (0 = vypnuto)
    push_max_event_age_s: float = 900.0  # starší nepotvrzené události se neposílají (po výpadku)

    def push_config(self):  # -> PushConfig | None
        from .push import PushConfig

        priv = self.vapid_private_key.get_secret_value() if self.vapid_private_key else None
        return PushConfig.build(self.vapid_public_key, priv, self.vapid_subject)


class HomeGeofenceSettings(PushSettings):
    """Geofence doma/venku – sdílí ingestor i API (stejné názvy proměnných).

    HOME_LAT/HOME_LON jsou CITLIVÉ (adresa domova): jen v .env, nikdy v gitu, žádný default.
    Nevyplněné = geofence vypnutý.
    """

    home_lat: float | None = None
    home_lon: float | None = None
    home_radius_m: float = 80.0
    geofence_debounce_leave_s: float = 120.0  # nepřetržitě venku N s -> "away"
    geofence_debounce_return_s: float = 60.0  # nepřetržitě uvnitř N s -> "home"
    geofence_max_accuracy_m: float = 150.0  # horší GPS fix se ignoruje
    geofence_escape_m: float = 300.0  # venku a dál než N m = "Utekla!" (UI)
    geofence_trust_tractive_home: bool = True  # KNOWN_WIFI / power-saving zóna potvrdí návrat hned
    geofence_eval_interval_s: float = 30.0  # ingestor: periodické vyhodnocení (0 = vypnuto)

    @field_validator("home_lat", "home_lon", mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        # compose předává `HOME_LAT: ${HOME_LAT:-}` -> prázdný string = nenastaveno
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    def home_config(self):  # -> HomeConfig | None (lazy import kvůli cyklům)
        from .home_geofence import HomeConfig

        return HomeConfig.build(
            self.home_lat,
            self.home_lon,
            radius_m=self.home_radius_m,
            leave_s=self.geofence_debounce_leave_s,
            return_s=self.geofence_debounce_return_s,
            max_accuracy_m=self.geofence_max_accuracy_m,
            escape_m=self.geofence_escape_m,
            trust_tractive_home=self.geofence_trust_tractive_home,
        )


class Settings(HomeGeofenceSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Tractive – jen e-mail/heslo účty (Apple/Google sign-in nefunguje)
    tractive_email: str = ""
    # SecretStr: heslo se neobjeví v repr()/logu
    tractive_password: SecretStr = SecretStr("")

    # Postgres/Supabase; None = běh bez DB (dry-run / zachytávání fixtures)
    database_url: SecretStr | None = None
    db_pool_min: int = 1
    db_pool_max: int = 5

    log_level: str = "INFO"
    log_format: str = "console"  # "console" | "json"

    backfill_days: int = 7
    backfill_chunk_hours: int = 24
    health_poll_interval_s: int = 3600  # pozor: Tractive health DATA (aktivita/spánek), ne /healthz

    reconnect_backoff_base_s: float = 5.0
    reconnect_backoff_max_s: float = 300.0
    channel_max_silence_s: int = 0  # 0 = vypnuto (keep-alive watchdog je v aiotractive)

    # Vestavěný HTTP health server (/healthz, /readyz). HEALTH_PORT=0 = vypnuto.
    # V Dockeru se binduje 0.0.0.0 (nastaveno v Dockerfile/compose).
    health_host: str = "127.0.0.1"
    health_port: int = 8080
    # /readyz: max. stáří posledního eventu/keep-alive/handshake z kanálu (s)
    health_ready_max_age_s: float = 90.0

    # Fronta povelů (tabulka `commands`, CLI `enqueue`). Bez DB se worker nespouští.
    command_poll_interval_s: float = 4.0  # jen dotaz do lokální DB, Tractive API se volá až při odeslání
    command_defer_timeout_s: float = 600.0  # jak dlouho od zařazení zkoušet (doma/power-saving), pak failed
    command_defer_retry_s: float = 30.0  # rozestup opakování odloženého povelu (a backoff po chybě API)
    command_max_attempts: int = 3  # max. skutečných volání API na jeden povel
    command_confirm_timeout_s: float = 120.0  # sent bez potvrzení ze streamu -> done (unconfirmed)

    fixtures_dir: Path | None = None
    local_tz: str = "Europe/Prague"

    def missing_credentials(self) -> list[str]:
        missing = []
        if not self.tractive_email:
            missing.append("TRACTIVE_EMAIL")
        if not self.tractive_password.get_secret_value():
            missing.append("TRACTIVE_PASSWORD")
        return missing

    @property
    def dsn(self) -> str | None:
        if self.database_url is None:
            return None
        value = self.database_url.get_secret_value().strip()
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
