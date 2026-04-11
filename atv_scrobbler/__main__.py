"""Entry point for atv-scrobbler."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

from .config import Config
from .event_log import JSONLEventLogger
from .monitor import ATVMonitor
from .state import ScrobbleState
from .trakt_client import TraktClient

# Register custom TRACE level (below DEBUG)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")
logging.TRACE = TRACE  # type: ignore[attr-defined]

logger = logging.getLogger("atv_scrobbler")

# Third-party loggers that are excessively verbose at DEBUG level.
# At "debug" level these get raised to INFO so the app's own debug
# messages remain readable. At "trace" level they stay at TRACE.
_NOISY_LOGGERS = [
    "pyatv.protocols.companion.protocol",
    "pyatv.protocols.companion.connection",
    "pyatv.protocols.companion.api",
    "pyatv.core.protocol",
    "pyatv.core.facade",
    "httpcore",
]


def _setup_logging(level_name: str) -> None:
    level_name = level_name.upper()

    if level_name == "TRACE":
        root_level = TRACE
    else:
        root_level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=root_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Always suppress httpx request-level logging
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # At "debug" level, suppress the noisiest third-party loggers so the
    # output stays useful for debugging app logic. "trace" leaves them open.
    if level_name == "DEBUG":
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.INFO)


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yaml")
    config = Config.load(config_path)

    _setup_logging(config.logging.level)

    if not config.trakt.client_id or not config.trakt.client_secret:
        logger.error("Trakt client_id and client_secret must be set in config.yaml")
        logger.error("Register an app at https://trakt.tv/oauth/applications")
        sys.exit(1)

    asyncio.run(_async_main(config))


async def _heartbeat_loop(interval: int = 15) -> None:
    heartbeat_path = Path("/app/heartbeat")
    while True:
        try:
            heartbeat_path.write_text(str(time.time()))
        except OSError:
            pass
        await asyncio.sleep(interval)


async def _async_main(config: Config) -> None:
    # Initialize Trakt client
    trakt = TraktClient(
        client_id=config.trakt.client_id,
        client_secret=config.trakt.client_secret,
    )

    logger.info("Authenticating with Trakt...")
    await trakt.ensure_auth()
    logger.info("Trakt auth OK")

    # Initialize state machine
    state = ScrobbleState(
        debounce_seconds=config.scrobble.debounce_seconds,
        min_duration=config.scrobble.min_duration,
    )
    state.set_sink(trakt)

    # Initialize event logger
    event_logger = JSONLEventLogger(config.logging.file)
    state.set_event_logger(event_logger)

    # Initialize Apple TV monitor
    monitor = ATVMonitor(config.apple_tv, config.scrobble, state)

    # Handle shutdown signals
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Run monitor in background, wait for shutdown
    monitor_task = asyncio.create_task(monitor.run())
    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    logger.info("atv-scrobbler started — monitoring Apple TV")

    await shutdown_event.wait()

    logger.info("Shutting down...")
    await monitor.stop()
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass
    await trakt.close()
    logger.info("Goodbye")


if __name__ == "__main__":
    main()
