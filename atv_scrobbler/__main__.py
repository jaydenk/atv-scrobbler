"""Entry point for atv-scrobbler."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from .config import Config
from .event_log import JSONLEventLogger
from .monitor import ATVMonitor
from .state import ScrobbleState
from .trakt_client import TraktClient

logger = logging.getLogger("atv_scrobbler")


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yaml")
    config = Config.load(config_path)

    # Setup logging
    log_level = getattr(logging, config.logging.level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not config.trakt.client_id or not config.trakt.client_secret:
        logger.error("Trakt client_id and client_secret must be set in config.yaml")
        logger.error("Register an app at https://trakt.tv/oauth/applications")
        sys.exit(1)

    asyncio.run(_async_main(config))


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
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Run monitor in background, wait for shutdown
    monitor_task = asyncio.create_task(monitor.run())

    logger.info("atv-scrobbler started — monitoring Apple TV")

    await shutdown_event.wait()

    logger.info("Shutting down...")
    await monitor.stop()
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    await trakt.close()
    logger.info("Goodbye")


if __name__ == "__main__":
    main()
