"""Trakt API client — OAuth device flow auth + scrobble endpoints."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import asyncio

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.trakt.tv"
TOKENS_FILE = "data/trakt_tokens.json"
_TOKEN_REFRESH_BUFFER = 86400  # refresh if <1 day before expiry


class TraktClient:
    def __init__(self, client_id: str, client_secret: str, tokens_path: str | Path = TOKENS_FILE):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tokens_path = Path(tokens_path)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0
        self._client: httpx.AsyncClient | None = None
        # Cache resolved episodes: (show_title, episode_title) → resolved media payload
        self._episode_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def _token_needs_refresh(self) -> bool:
        return time.time() >= self._expires_at - _TOKEN_REFRESH_BUFFER

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers={
                    "Content-Type": "application/json",
                    "trakt-api-version": "2",
                    "trakt-api-key": self.client_id,
                },
                timeout=15.0,
            )
        return self._client

    def _load_tokens(self) -> bool:
        if not self.tokens_path.exists():
            return False
        data = json.loads(self.tokens_path.read_text())
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token")
        self._expires_at = data.get("expires_at", 0)
        return self._access_token is not None

    def _save_tokens(self, data: dict[str, Any]) -> None:
        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        self._expires_at = data.get("created_at", time.time()) + data.get("expires_in", 7776000)
        self.tokens_path.write_text(json.dumps({
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
        }, indent=2))
        logger.info("Trakt tokens saved to %s", self.tokens_path)

    async def _refresh_access_token(self) -> None:
        client = await self._get_client()
        resp = await client.post("/oauth/token", json={
            "refresh_token": self._refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        self._save_tokens(resp.json())
        logger.info("Trakt access token refreshed")

    async def ensure_auth(self) -> None:
        """Load tokens from disk, refresh if expired, or run device auth flow."""
        if self._load_tokens():
            if not self._token_needs_refresh():
                logger.info("Trakt auth loaded from %s", self.tokens_path)
                return
            if self._refresh_token:
                await self._refresh_access_token()
                return

        # Device code auth flow
        await self._device_auth()

    async def _device_auth(self) -> None:
        client = await self._get_client()
        resp = await client.post("/oauth/device/code", json={
            "client_id": self.client_id,
        })
        resp.raise_for_status()
        data = resp.json()

        user_code = data["user_code"]
        verification_url = data["verification_url"]
        device_code = data["device_code"]
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 600)

        logger.warning("=" * 50)
        logger.warning("  TRAKT AUTHORISATION REQUIRED")
        logger.warning("  Go to: %s", verification_url)
        logger.warning("  Enter code: %s", user_code)
        logger.warning("=" * 50)

        deadline = time.time() + expires_in
        while time.time() < deadline:
            await asyncio.sleep(interval)
            poll_resp = await client.post("/oauth/device/token", json={
                "code": device_code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            if poll_resp.status_code == 200:
                self._save_tokens(poll_resp.json())
                logger.info("Trakt device auth completed successfully")
                return
            if poll_resp.status_code == 400:
                # Pending — user hasn't authorised yet
                continue
            if poll_resp.status_code in (404, 410, 418, 429):
                logger.error("Trakt device auth failed: %s", poll_resp.status_code)
                raise RuntimeError(f"Trakt auth failed with status {poll_resp.status_code}")

        raise RuntimeError("Trakt device auth timed out — user did not authorise in time")

    async def _authed_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self._token_needs_refresh() and self._refresh_token:
            await self._refresh_access_token()

        client = await self._get_client()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        resp = await client.request(method, path, headers=headers, **kwargs)
        return resp

    # --- Scrobble endpoints ---

    async def _resolve_episode(self, show_title: str, episode_title: str) -> dict[str, Any] | None:
        """Look up Trakt IDs for an episode by show title + episode title.

        Required when apps (e.g. HBO Max) report the series name and episode title
        but no season/episode numbers. Caches successful lookups per session.
        """
        cache_key = (show_title, episode_title)
        if cache_key in self._episode_cache:
            return self._episode_cache[cache_key]

        # Find the show
        shows = await self.search("show", show_title)
        if not shows:
            logger.warning("Trakt: no show match for %r", show_title)
            return None
        show = shows[0].get("show", {})
        show_ids = show.get("ids", {})
        trakt_id = show_ids.get("trakt")
        if not trakt_id:
            return None

        # Fetch all seasons + episodes for the show
        resp = await self._authed_request(
            "GET", f"/shows/{trakt_id}/seasons", params={"extended": "episodes"}
        )
        if resp.status_code != 200:
            logger.warning("Trakt: failed to fetch seasons for show %s (trakt id %s)", show_title, trakt_id)
            return None

        normalised_target = episode_title.strip().lower()
        for season in resp.json():
            for ep in season.get("episodes", []) or []:
                if (ep.get("title") or "").strip().lower() == normalised_target:
                    resolved = {
                        "show": {"ids": show_ids},
                        "episode": {
                            "season": ep.get("season"),
                            "number": ep.get("number"),
                            "ids": ep.get("ids", {}),
                        },
                    }
                    self._episode_cache[cache_key] = resolved
                    logger.info(
                        "Resolved episode via search: %s — %r → S%sE%s",
                        show_title, episode_title, ep.get("season"), ep.get("number"),
                    )
                    return resolved

        logger.warning("Trakt: no episode match for %s — %r", show_title, episode_title)
        return None

    async def _scrobble(self, action: str, media: dict[str, Any], progress: float) -> dict | None:
        # Resolve episode by title if season/number are missing (e.g. HBO Max)
        episode = media.get("episode") or {}
        if "show" in media and "season" not in episode and episode.get("title"):
            show_title = media["show"].get("title")
            if show_title:
                resolved = await self._resolve_episode(show_title, episode["title"])
                if resolved is None:
                    return None
                media = resolved

        payload = {**media, "progress": progress}
        resp = await self._authed_request("POST", f"/scrobble/{action}", json=payload)
        if resp.status_code == 201:
            result = resp.json()
            trakt_action = result.get("action", action)
            logger.info("Scrobble %s (%s) at %.1f%%: %s", action, trakt_action, progress, _summary(media))
            return result
        if resp.status_code == 404:
            logger.warning("Scrobble %s: not found on Trakt — %s", action, _summary(media))
            return None
        logger.error("Scrobble %s failed [%s]: %s", action, resp.status_code, resp.text)
        return None

    async def scrobble_start(self, media: dict[str, Any], progress: float) -> dict | None:
        return await self._scrobble("start", media, progress)

    async def scrobble_pause(self, media: dict[str, Any], progress: float) -> dict | None:
        return await self._scrobble("pause", media, progress)

    async def scrobble_stop(self, media: dict[str, Any], progress: float) -> dict | None:
        return await self._scrobble("stop", media, progress)

    async def search(self, media_type: str, query: str) -> list[dict]:
        resp = await self._authed_request("GET", f"/search/{media_type}", params={"query": query})
        if resp.status_code == 200:
            return resp.json()
        return []

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


def _summary(media: dict) -> str:
    if "show" in media:
        ep = media.get("episode", {})
        return f"{media['show'].get('title', '?')} S{ep.get('season', '?')}E{ep.get('number', '?')}"
    if "movie" in media:
        return media["movie"].get("title", "?")
    return str(media)
