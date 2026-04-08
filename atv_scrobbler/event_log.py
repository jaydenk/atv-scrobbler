"""Local JSONL event logger for scrobble events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .matcher import MediaInfo

logger = logging.getLogger(__name__)


class JSONLEventLogger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def log_event(self, event: str, info: MediaInfo, progress: float, trakt_response: dict | None) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "app": info.app_name,
            "app_id": info.app_id,
            "title": info.title,
            "series": info.series_name,
            "season": info.season_number,
            "episode": info.episode_number,
            "duration": info.duration,
            "progress": round(progress, 1),
            "trakt_action": trakt_response.get("action") if trakt_response else None,
        }
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            logger.exception("Failed to write event log")
