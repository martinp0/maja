"""structlog konfigurace (console pro vývoj, JSON pro systemd/Docker)."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    # stdlib logging (aiotractive, asyncpg, aiohttp) posíláme na stderr
    logging.basicConfig(stream=sys.stderr, level=log_level, format="%(levelname)s %(name)s: %(message)s")
    # aiotractive na DEBUG loguje celé payloady včetně polohy – držet na INFO, pokud to není potřeba
    logging.getLogger("aiotractive").setLevel(max(log_level, logging.INFO))

    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
