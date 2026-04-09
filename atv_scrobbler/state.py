"""Scrobble state machine — tracks playback state and triggers scrobble events."""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pyatv.const import DeviceState

from .matcher import MediaInfo, to_trakt_media

logger = logging.getLogger(__name__)


class ScrobbleAction(enum.Enum):
    START = "start"
    PAUSE = "pause"
    STOP = "stop"


class ScrobbleSink(Protocol):
    """Interface for sending scrobble events (implemented by TraktClient)."""
    async def scrobble_start(self, media: dict[str, Any], progress: float) -> dict | None: ...
    async def scrobble_pause(self, media: dict[str, Any], progress: float) -> dict | None: ...
    async def scrobble_stop(self, media: dict[str, Any], progress: float) -> dict | None: ...


class EventLogger(Protocol):
    """Interface for logging scrobble events locally."""
    def log_event(self, event: str, info: MediaInfo, progress: float, trakt_response: dict | None) -> None: ...


class PlaybackState(enum.Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class ScrobbleState:
    """Tracks current playback and manages scrobble lifecycle."""
    debounce_seconds: float = 30.0
    min_duration: int = 120

    _state: PlaybackState = field(default=PlaybackState.IDLE, init=False)
    _current_info: MediaInfo | None = field(default=None, init=False)
    _current_trakt_media: dict[str, Any] | None = field(default=None, init=False)
    _last_position: int = field(default=0, init=False)
    _last_duration: int = field(default=0, init=False)
    _debounce_task: asyncio.Task | None = field(default=None, init=False)
    _sink: ScrobbleSink | None = field(default=None, init=False)
    _event_logger: EventLogger | None = field(default=None, init=False)
    _scrobble_started: bool = field(default=False, init=False)

    def set_sink(self, sink: ScrobbleSink) -> None:
        self._sink = sink

    def set_event_logger(self, logger: EventLogger) -> None:
        self._event_logger = logger

    def _compute_progress(self, position: int | None, duration: int | None) -> float:
        pos = position or self._last_position
        dur = duration or self._last_duration
        if dur and dur > 0:
            return min((pos / dur) * 100, 100.0)
        return 0.0

    def _content_changed(self, info: MediaInfo) -> bool:
        if self._current_info is None:
            return True
        old = self._current_info
        # Compare by series+season+episode for TV, title for movies
        if info.is_tv and old.is_tv:
            return (info.series_name != old.series_name or
                    info.season_number != old.season_number or
                    info.episode_number != old.episode_number)
        return info.title != old.title

    def _cancel_debounce(self) -> None:
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            self._debounce_task = None

    async def _debounced_stop(self) -> None:
        """Wait debounce period then send stop scrobble."""
        try:
            await asyncio.sleep(self.debounce_seconds)
        except asyncio.CancelledError:
            return
        await self._do_stop()

    async def _do_stop(self) -> None:
        if not self._scrobble_started or not self._current_trakt_media or not self._sink:
            self._state = PlaybackState.IDLE
            self._scrobble_started = False
            return

        progress = self._compute_progress(self._last_position, self._last_duration)
        result = await self._sink.scrobble_stop(self._current_trakt_media, progress)
        if self._event_logger and self._current_info:
            self._event_logger.log_event("stop", self._current_info, progress, result)

        self._state = PlaybackState.IDLE
        self._current_info = None
        self._current_trakt_media = None
        self._scrobble_started = False

    async def _do_start(self, info: MediaInfo, trakt_media: dict[str, Any]) -> None:
        if not self._sink:
            return
        progress = self._compute_progress(0, info.duration)
        result = await self._sink.scrobble_start(trakt_media, progress)
        if self._event_logger:
            self._event_logger.log_event("start", info, progress, result)
        self._scrobble_started = True

    async def update(self, info: MediaInfo, device_state: DeviceState, position: int | None, duration: int | None) -> None:
        """Called on every pyatv push update. Drives the state machine."""
        is_playing = device_state == DeviceState.Playing
        is_paused = device_state == DeviceState.Paused
        is_idle = device_state in (DeviceState.Idle, DeviceState.Stopped)

        # Update position tracking
        if position is not None:
            self._last_position = position
        if duration is not None and duration > 0:
            self._last_duration = duration

        # Filter: ignore content below minimum duration
        if duration is not None and 0 < duration < self.min_duration:
            return

        # Filter: unidentifiable content
        if not info.is_identifiable:
            if is_idle and self._state != PlaybackState.IDLE:
                # Content became unidentifiable (e.g. went to home screen) — stop scrobble
                self._cancel_debounce()
                self._debounce_task = asyncio.create_task(self._debounced_stop())
            return

        trakt_media = to_trakt_media(info)
        if trakt_media is None:
            return

        # Content changed while something was playing — stop old, start new
        if self._state != PlaybackState.IDLE and self._content_changed(info):
            self._cancel_debounce()
            await self._do_stop()

        if is_playing:
            self._cancel_debounce()
            if self._state == PlaybackState.IDLE:
                # New playback
                self._current_info = info
                self._current_trakt_media = trakt_media
                self._state = PlaybackState.PLAYING
                await self._do_start(info, trakt_media)
            elif self._state == PlaybackState.PAUSED:
                # Resume
                self._state = PlaybackState.PLAYING
                if self._sink and self._current_trakt_media:
                    progress = self._compute_progress(position, duration)
                    result = await self._sink.scrobble_start(self._current_trakt_media, progress)
                    if self._event_logger and self._current_info:
                        self._event_logger.log_event("resume", self._current_info, progress, result)
            # else: already playing, just a position update — no action needed

        elif is_paused:
            self._cancel_debounce()
            if self._state == PlaybackState.PLAYING and self._sink and self._current_trakt_media:
                self._state = PlaybackState.PAUSED
                progress = self._compute_progress(position, duration)
                result = await self._sink.scrobble_pause(self._current_trakt_media, progress)
                if self._event_logger and self._current_info:
                    self._event_logger.log_event("pause", self._current_info, progress, result)

        elif is_idle:
            if self._state != PlaybackState.IDLE:
                # Start debounce timer — don't stop immediately
                self._cancel_debounce()
                self._debounce_task = asyncio.create_task(self._debounced_stop())

    async def force_stop(self) -> None:
        """Force-stop any active scrobble (e.g. on shutdown)."""
        self._cancel_debounce()
        if self._state != PlaybackState.IDLE:
            await self._do_stop()
