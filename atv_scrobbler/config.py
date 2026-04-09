from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class AppleTVConfig:
    identifier: str = ""


@dataclass
class TraktConfig:
    client_id: str = ""
    client_secret: str = ""


@dataclass
class ScrobbleConfig:
    min_duration: int = 120
    debounce_seconds: int = 30
    ignored_apps: list[str] = field(default_factory=lambda: [
        "com.apple.Fitness",
        "com.apple.TVHomeScreen",
    ])
    media_types: list[str] = field(default_factory=lambda: ["video", "tv"])


@dataclass
class LoggingConfig:
    file: str = "data/scrobble.jsonl"
    level: str = "info"


@dataclass
class Config:
    apple_tv: AppleTVConfig = field(default_factory=AppleTVConfig)
    trakt: TraktConfig = field(default_factory=TraktConfig)
    scrobble: ScrobbleConfig = field(default_factory=ScrobbleConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        path = Path(path)
        if not path.exists():
            logger.warning("Config file %s not found, using defaults", path)
            return cls()

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        cfg = cls()
        if atv := raw.get("apple_tv"):
            cfg.apple_tv = AppleTVConfig(**{k: v for k, v in atv.items() if k in AppleTVConfig.__dataclass_fields__})
        if trakt := raw.get("trakt"):
            cfg.trakt = TraktConfig(**{k: v for k, v in trakt.items() if k in TraktConfig.__dataclass_fields__})
        if scrobble := raw.get("scrobble"):
            cfg.scrobble = ScrobbleConfig(**{k: v for k, v in scrobble.items() if k in ScrobbleConfig.__dataclass_fields__})
        if log := raw.get("logging"):
            cfg.logging = LoggingConfig(**{k: v for k, v in log.items() if k in LoggingConfig.__dataclass_fields__})

        return cfg
