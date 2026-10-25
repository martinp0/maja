"""Konfigurace API z prostředí / .env (pydantic-settings, stejný styl jako ingestor).

Sdílí s ingestorem DATABASE_URL, DB_POOL_*, LOG_* a geofence (HOME_*, GEOFENCE_*);
vlastní proměnné mají prefix API_.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from tractive_ingestor.config import HomeGeofenceSettings

from .auth import parse_read_tokens


class ApiSettings(HomeGeofenceSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: SecretStr | None = None
    # API je lehké (pár dotazů na request) -> menší pool než ingestor; šetří RAM Postgresu na Pi
    api_db_pool_min: int = 1
    api_db_pool_max: int = 3

    log_level: str = "INFO"
    log_format: str = "console"  # "console" | "json"

    # 127.0.0.1 = jen lokálně; v Dockeru 0.0.0.0 (Dockerfile.api/compose), ven publikuje compose
    api_host: str = "127.0.0.1"
    api_port: int = 8090
    # Čárkami oddělené originy pro CORS. Prázdné / nenastavené = žádný CORS
    # (produkce i same-origin PWA). Vyplň jen když prohlížeč volá API z jiného originu.
    api_cors_origins: str = ""
    # Adresář s buildem PWA (web/dist). Neexistuje -> servíruje se jen /api.
    api_web_dist: Path = Path("web/dist")
    # Strop počtu bodů v /positions (2GB RAM Pi + mobil – víc stejně nevykreslíme)
    api_positions_max_limit: int = 5000
    api_positions_default_hours: int = 24
    api_commands_max_limit: int = 100

    # PIN login. Prázdný APP_PIN = autentizace VYPNUTÁ (všechno otevřené, jen pro lokální vývoj).
    app_pin: SecretStr | None = None
    # Hex klíč pro HMAC session cookie (např. `openssl rand -hex 32`). Prázdný = náhodný
    # při každém startu -> po restartu API se musí znovu zadat PIN.
    app_session_secret: SecretStr | None = None
    # Read-only tokeny pro nativní appky (iPhone, widget, Apple Watch): hlavička
    # `Authorization: Bearer <token>`. Buď `jméno=token` (jeden na zařízení, v logu je jen jméno),
    # nebo starý seznam tokenů čárkami. Pustí jen GET na čtecí endpointy (poloha, baterie, doma/venku).
    # Prázdné = vypnuto. Kratší než 32 znaků se ignoruje. Vygeneruj: openssl rand -hex 32
    api_read_tokens: SecretStr | None = None

    # Passkeys (WebAuthn). RP ID = doména bez schématu/portu; origin = přesná adresa appky.
    # Víc originů čárkami. Výchozí je localhost; na vlastní doméně obojí nastav v .env
    # (passkey funguje jen na tom originu, na kterém vznikl).
    webauthn_rp_id: str = "localhost"
    webauthn_origin: str = "http://localhost:8090"
    webauthn_rp_name: str = "Mája"

    @property
    def webauthn_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.webauthn_origin.split(",") if o.strip()]

    @property
    def dsn(self) -> str | None:
        if self.database_url is None:
            return None
        value = self.database_url.get_secret_value().strip()
        return value or None

    @property
    def pin(self) -> str | None:
        if self.app_pin is None:
            return None
        value = self.app_pin.get_secret_value().strip()
        return value or None

    @property
    def session_secret(self) -> bytes | None:
        if self.app_session_secret is None:
            return None
        value = self.app_session_secret.get_secret_value().strip()
        if not value:
            return None
        try:
            return bytes.fromhex(value)
        except ValueError:
            return value.encode("utf-8")  # tolerance: ne-hex hodnota se použije tak, jak je

    @property
    def read_token_entries(self) -> list[tuple[str, str]]:
        if self.api_read_tokens is None:
            return []
        return parse_read_tokens(self.api_read_tokens.get_secret_value())

    @property
    def read_tokens(self) -> list[str]:
        return [secret for _, secret in self.read_token_entries]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
