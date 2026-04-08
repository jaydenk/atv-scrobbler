"""Trakt API client — OAuth device flow auth + scrobble endpoints."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.trakt.tv"
TOKENS_FILE = "trakt_tokens.json"


class TraktClient:
    def __init__(self, client_id: str, client_secret: str, tokens_path: str | Path = TOKENS_FILE):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tokens_path = Path(tokens_path)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0
        self._client: httpx.AsyncClient | None = None

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
            if time.time() < self._expires_at - 86400:  # refresh if <1 day left
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

        print()
        print("=" * 50)
        print("  TRAKT AUTHORIZATION REQUIRED")
        print(f"  Go to: {verification_url}")
        print(f"  Enter code: {user_code}")
        print("=" * 50)
        print()

        deadline = time.time() + expires_in
        while time.time() < deadline:
            await _async_sleep(interval)
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
                # Pending — user hasn't authorized yet
                continue
            if poll_resp.status_code in (404, 410, 418, 429):
                logger.error("Trakt device auth failed: %s", poll_resp.status_code)
                raise RuntimeError(f"Trakt auth failed with status {poll_resp.status_code}")

        raise RuntimeError("Trakt device auth timed out — user did not authorize in time")

    async def _authed_request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if time.time() >= self._expires_at - 86400 and self._refresh_token:
            await self._refresh_access_token()

        client = await self._get_client()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        resp = await client.request(method, path, headers=headers, **kwargs)
        return resp

    # --- Scrobble endpoints ---

    async def scrobble_start(self, media: dict[str, Any], progress: float) -> dict | None:
        payload = {**media, "progress": progress}
        resp = await self._authed_request("POST", "/scrobble/start", json=payload)
        if resp.status_code == 201:
            logger.info("Scrobble start: %s", _summary(media))
            return resp.json()
        if resp.status_code == 404:
            logger.warning("Scrobble start: not found on Trakt — %s", _summary(media))
            return None
        logger.error("Scrobble start failed [%s]: %s", resp.status_code, resp.text)
        return None

    async def scrobble_pause(self, media: dict[str, Any], progress: float) -> dict | None:
        payload = {**media, "progress": progress}
        resp = await self._authed_request("POST", "/scrobble/pause", json=payload)
        if resp.status_code == 201:
            logger.info("Scrobble pause at %.1f%%: %s", progress, _summary(media))
            return resp.json()
        logger.error("Scrobble pause failed [%s]: %s", resp.status_code, resp.text)
        return None

    async def scrobble_stop(self, media: dict[str, Any], progress: float) -> dict | None:
        payload = {**media, "progress": progress}
        resp = await self._authed_request("POST", "/scrobble/stop", json=payload)
        if resp.status_code == 201:
            result = resp.json()
            action = result.get("action", "unknown")
            logger.info("Scrobble stop (%s) at %.1f%%: %s", action, progress, _summary(media))
            return result
        if resp.status_code == 404:
            logger.warning("Scrobble stop: not found on Trakt — %s", _summary(media))
            return None
        logger.error("Scrobble stop failed [%s]: %s", resp.status_code, resp.text)
        return None

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


async def _async_sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)
