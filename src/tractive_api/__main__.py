"""Entrypoint: `python -m tractive_api` -> uvicorn s nastavením z env/.env."""

from __future__ import annotations

import uvicorn

from tractive_ingestor.logging_setup import configure_logging

from .app import create_app
from .config import get_api_settings


def main() -> None:
    settings = get_api_settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        proxy_headers=False,
        server_header=False,
        access_log=False,  # access log by na Pi zbytečně plnil disk (PWA polluje)
    )


if __name__ == "__main__":
    main()
