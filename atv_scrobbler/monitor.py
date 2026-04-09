"""Apple TV connection and push update monitoring via pyatv."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pyatv
from pyatv import interface
from pyatv.const import DeviceState, MediaType
from pyatv.storage.file_storage import FileStorage

from .config import AppleTVConfig, ScrobbleConfig
from .matcher import MediaInfo, extract_media_info
from .state import ScrobbleState

logger = logging.getLogger(__name__)

# How long to wait before retrying connection after a failure
RECONNECT_DELAY = 15

# Map pyatv MediaType enum to config-friendly strings
_MEDIA_TYPE_MAP = {
    MediaType.Video: "video",
    MediaType.TV: "tv",
    MediaType.Music: "music",
}


class ATVMonitor(interface.PushListener):
    """Connects to an Apple TV and feeds push updates into the ScrobbleState."""

    def __init__(self, atv_config: AppleTVConfig, scrobble_config: ScrobbleConfig, state: ScrobbleState):
        self.atv_config = atv_config
        self.scrobble_config = scrobble_config
        self.state = state
        self._atv: Any = None
        self._running = False
        self._storage: FileStorage | None = None

    async def run(self) -> None:
        """Main loop — connect, listen, reconnect on failure."""
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Apple TV connection error")
            finally:
                await self._disconnect()

            if self._running:
                logger.info("Reconnecting in %ds...", RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    async def stop(self) -> None:
        self._running = False
        await self.state.force_stop()
        await self._disconnect()

    async def _connect_and_listen(self) -> None:
        loop = asyncio.get_running_loop()

        # Load pairing credentials once, reuse across reconnections
        if self._storage is None:
            self._storage = FileStorage.default_storage(loop)
            await self._storage.load()

        logger.info("Scanning for Apple TV...")
        atvs = await pyatv.scan(loop, storage=self._storage)

        if not atvs:
            logger.error("No Apple TV found on the network")
            return

        config = self._pick_device(atvs)
        if config is None:
            logger.error("Configured Apple TV not found (identifier: %s)", self.atv_config.identifier)
            return

        logger.info("Connecting to %s (%s)...", config.name, config.address)
        self._atv = await pyatv.connect(config, loop, storage=self._storage)
        logger.info("Connected to %s", config.name)

        # Start push updates
        self._atv.push_updater.listener = self
        self._atv.push_updater.start()

        # Keep alive until disconnected
        while self._running and self._atv:
            await asyncio.sleep(1)

    def _pick_device(self, atvs: list) -> Any | None:
        identifier = self.atv_config.identifier
        if not identifier:
            logger.info("No identifier configured, using first device: %s", atvs[0].name)
            return atvs[0]
        for config in atvs:
            if str(config.identifier) == identifier:
                return config
        return None

    async def _disconnect(self) -> None:
        if self._atv:
            self._atv.close()
            self._atv = None

    # --- PushListener callbacks ---

    def playstatus_update(self, updater: Any, playstatus: Any) -> None:
        """Called by pyatv when now-playing state changes."""
        device_state = playstatus.device_state

        # Check if we should ignore this app
        app_name = None
        app_id = None
        if self._atv:
            try:
                app = self._atv.metadata.app
                app_name = app.name if app else None
                app_id = app.identifier if app else None
            except Exception:
                pass

        if app_id and app_id in self.scrobble_config.ignored_apps:
            return

        # Check media type filter
        type_str = _MEDIA_TYPE_MAP.get(playstatus.media_type, "unknown")
        if type_str not in self.scrobble_config.media_types and type_str != "unknown":
            return

        info = extract_media_info(playstatus, app_name=app_name, app_id=app_id)

        logger.debug(
            "Push update: state=%s title=%s series=%s S%sE%s app=%s pos=%s/%s",
            device_state, info.title, info.series_name,
            info.season_number, info.episode_number,
            info.app_name, playstatus.position, playstatus.total_time,
        )

        # Schedule state update on the event loop (push callbacks are sync)
        asyncio.get_running_loop().create_task(
            self.state.update(info, device_state, playstatus.position, playstatus.total_time)
        )

    def playstatus_error(self, updater: Any, exception: Exception) -> None:
        logger.error("Push updater error: %s", exception)
        # Stop any active scrobble before disconnecting
        asyncio.get_running_loop().create_task(self.state.force_stop())
        # Force disconnect so the main loop reconnects
        if self._atv:
            self._atv.close()
            self._atv = None
