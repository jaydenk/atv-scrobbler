"""Map pyatv now-playing metadata to Trakt media objects."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from pyatv.const import MediaType

logger = logging.getLogger(__name__)


@dataclass
class MediaInfo:
    """Normalized media info extracted from pyatv."""
    title: str | None = None
    series_name: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    media_type: MediaType = MediaType.Unknown
    app_name: str | None = None
    app_id: str | None = None
    duration: int | None = None
    content_id: str | None = None

    @property
    def is_tv(self) -> bool:
        if self.media_type == MediaType.TV:
            return True
        # Any content with a series name is TV content, even without season/episode numbers
        if self.series_name:
            return True
        return False

    @property
    def is_identifiable(self) -> bool:
        if self.is_tv and self.series_name:
            return True
        if not self.is_tv and self.title:
            return True
        return False


# Patterns for Netflix-style episode titles: "S1:E7 'Bells'" or "Season 1: Episode 7"
_NETFLIX_PATTERNS = [
    re.compile(r"S(\d+)\s*:\s*E(\d+)\s*[\"']?(.+?)[\"']?\s*$", re.IGNORECASE),
    re.compile(r"Season\s+(\d+)\s*:\s*Episode\s+(\d+)\s*[-–—]\s*(.+)$", re.IGNORECASE),
    re.compile(r"S(\d+)\s*E(\d+)\s*[-–—:]\s*(.+)$", re.IGNORECASE),
]


def extract_media_info(playing: Any, app_name: str | None = None, app_id: str | None = None) -> MediaInfo:
    """Extract normalized MediaInfo from a pyatv Playing object."""
    info = MediaInfo(
        title=playing.title,
        series_name=playing.series_name,
        season_number=playing.season_number,
        episode_number=playing.episode_number,
        media_type=playing.media_type,
        app_name=app_name,
        app_id=app_id,
        duration=playing.total_time,
        content_id=playing.content_identifier,
    )

    # Legacy MPNowPlayingInfoCenter convention: apps like HBO Max put the series
    # name in the "trackArtistName" field (pyatv exposes this as .artist). Use it
    # as a series_name fallback for video content.
    if not info.series_name and info.media_type != MediaType.Music:
        artist = getattr(playing, "artist", None)
        if artist:
            info.series_name = artist

    # If series metadata is missing but title looks like an episode string, try parsing it
    if not info.series_name and info.title:
        _try_parse_episode_title(info)

    return info


def _try_parse_episode_title(info: MediaInfo) -> None:
    """Try to extract season/episode from a title like 'S1:E7 Bells'."""
    for pattern in _NETFLIX_PATTERNS:
        m = pattern.match(info.title or "")
        if m:
            info.season_number = int(m.group(1))
            info.episode_number = int(m.group(2))
            info.title = m.group(3).strip()
            # Can't determine series_name from the title alone
            logger.debug("Parsed episode from title: S%dE%d '%s'", info.season_number, info.episode_number, info.title)
            return


def to_trakt_media(info: MediaInfo) -> dict[str, Any] | None:
    """Convert MediaInfo to a Trakt scrobble payload (the media portion).

    For TV content without season/episode numbers, episode is sent with only a title —
    the Trakt client resolves it via search before scrobbling. Resolution hints
    (duration, content_id) are attached under the internal `_hints` key, which the
    Trakt client strips before sending to the API.
    """
    if info.is_tv:
        if not info.series_name:
            logger.warning("TV content detected but no series name — cannot match: title=%s", info.title)
            return None
        episode: dict[str, Any] = {}
        if info.season_number is not None and info.episode_number is not None:
            episode["season"] = info.season_number
            episode["number"] = info.episode_number
        if info.title:
            episode["title"] = info.title
        if not episode:
            logger.warning("TV content detected but no episode info — cannot match: series=%s", info.series_name)
            return None
        return {
            "show": {"title": info.series_name},
            "episode": episode,
            "_hints": {
                "duration": info.duration,
                "content_id": info.content_id,
            },
        }
    else:
        if not info.title:
            return None
        return {
            "movie": {"title": info.title},
        }
