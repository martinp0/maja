"""Entrypoint: `python -m tractive_ingestor [run|check|init-db|enqueue|gen-vapid|seed-demo]`.

    python -m tractive_ingestor gen-vapid   # nové VAPID klíče pro Web Push -> řádky do .env

    python -m tractive_ingestor enqueue <tracker_id> live on     # LIVE tracking
    python -m tractive_ingestor enqueue <tracker_id> buzzer      # zvuk (default on)
    python -m tractive_ingestor enqueue <tracker_id> led off     # světlo

Exit kódy: 0 OK · 1 neočekávaná chyba · 2 chybí konfigurace · 3 Tractive odmítl přihlášení.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

import structlog
from aiotractive.exceptions import TractiveError

from .config import get_settings
from .commands import ACTIONS, COMMAND_ALIASES
from .ingestor import AuthError, ConfigError, Ingestor, check, enqueue, init_db
from .logging_setup import configure_logging


async def _run_service() -> None:
    ingestor = Ingestor(get_settings())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, ingestor.request_stop)
        except NotImplementedError:  # Windows
            pass
    await ingestor.run()


def main(argv: list[str] | None = None) -> int:
    from .push import DEFAULT_VAPID_SUBJECT

    parser = argparse.ArgumentParser(prog="tractive_ingestor", description="Tractive -> Postgres/Supabase ingestor")
    sub = parser.add_subparsers(dest="command", metavar="{run,check,init-db,enqueue,gen-vapid,seed-demo}")
    sub.add_parser("run", help="služba (default)")
    sub.add_parser("check", help="login + snapshot bez DB")
    sub.add_parser("init-db", help="aplikovat schema.sql")
    demo = sub.add_parser(
        "seed-demo",
        help="vložit fiktivní fenu v Central Parku (ne účet Tractive, ne adresa domova)",
    )
    demo.add_argument("--clear", action="store_true", help="jen smazat ukázkovou fenu DEMO-LUNA")
    enq = sub.add_parser("enqueue", help="zařadit povel do fronty (odešle ho běžící služba)")
    enq.add_argument("tracker_id")
    enq.add_argument("cmd", metavar="command", choices=sorted(COMMAND_ALIASES), help="live | buzzer | led")
    enq.add_argument("action", nargs="?", default="on", choices=ACTIONS, help="on (default) | off")
    gv = sub.add_parser("gen-vapid", help="vygenerovat VAPID klíče pro Web Push (vypíše řádky do .env)")
    gv.add_argument("--subject", default=DEFAULT_VAPID_SUBJECT, help="mailto:… nebo https://…")
    args = parser.parse_args(argv)
    command = args.command or "run"
    if command == "gen-vapid":  # bez .env / logování / DB
        from .push import vapid_env_lines

        sys.stdout.write(vapid_env_lines(args.subject))
        return 0

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log = structlog.get_logger("tractive_ingestor")

    if command == "seed-demo":
        if not settings.dsn:
            log.error("config_error", error="DATABASE_URL není nastavené")
            return 2
        from .demo import seed_demo

        try:
            sys.stdout.write(asyncio.run(seed_demo(settings.dsn, clear=args.clear)))
        except Exception as exc:
            log.error("seed_demo_failed", error=repr(exc))
            return 1
        return 0

    coro = {
        "run": _run_service,
        "check": lambda: check(settings),
        "init-db": lambda: init_db(settings),
        "enqueue": lambda: enqueue(settings, args.tracker_id, args.cmd, args.action),
    }[command]
    try:
        asyncio.run(coro())
    except ConfigError as exc:
        log.error("config_error", error=str(exc))
        return 2
    except AuthError as exc:
        log.error("auth_failed", error=str(exc))
        return 3
    except TractiveError as exc:
        log.error("tractive_error", error=repr(exc), cause=repr(exc.__cause__))
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
