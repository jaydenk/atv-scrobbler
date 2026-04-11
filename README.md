# atv-scrobbler

Automatically scrobble what you watch on Apple TV to [Trakt](https://trakt.tv). Works with any app that reports now-playing metadata (Netflix, Plex, Apple TV+, Disney+, Spotify, etc.).

Trakt syncs to [Sequel](https://getsequel.app) (or any Trakt-connected service) automatically.

## How it works

1. **pyatv** connects to your Apple TV over the local network (MRP protocol)
2. Push updates trigger a **state machine** that tracks play/pause/stop transitions
3. Scrobble events are sent to the **Trakt API** (start/pause/stop)
4. Trakt marks content as watched at >80% progress
5. **Sequel** picks up watched history via its built-in Trakt sync

## Setup

### 1. Register a Trakt API app

Go to [trakt.tv/oauth/applications](https://trakt.tv/oauth/applications) and create a new app:
- **Name:** atv-scrobbler (or whatever you like)
- **Redirect URI:** `urn:ietf:wg:oauth:2.0:oob`

Note the **Client ID** and **Client Secret**.

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Fill in your Trakt `client_id`, `client_secret`, and Apple TV `identifier`.

### 3. Pair with Apple TV

pyatv requires pairing credentials to connect to your Apple TV. The container includes pyatv, so you can pair directly through Docker — no host installation needed.

Find your Apple TV and note the **Identifier**:

```bash
docker compose run --rm atv-scrobbler atvremote scan
```

Run the pairing wizard (enter the PIN displayed on your Apple TV for each protocol):

```bash
docker compose run --rm atv-scrobbler atvremote wizard
```

Credentials are saved to `data/.pyatv.conf` automatically.

### 4. Start the service

```bash
docker compose up -d
docker compose logs -f   # watch for Trakt authorisation prompt
```

Runtime data (Trakt tokens, scrobble log, pyatv credentials) is persisted in the `data/` directory, which is volume-mounted from the host.

### 5. Connect Sequel to Trakt

In Sequel (requires Sequel+): Settings > Trakt > Connect. Choose "Merge" to keep existing data.

## First run

On first start, the service runs an interactive Trakt OAuth device flow. Check the container logs:

```bash
docker compose logs -f atv-scrobbler
```

You will see a message like:

```
Go to https://trakt.tv/activate and enter code: A1B2C3D4
```

Open [trakt.tv/activate](https://trakt.tv/activate) in a browser, sign in, and enter the code shown in the logs. Once authorised, tokens are saved to `data/trakt_tokens.json` and the service begins monitoring your Apple TV. Subsequent restarts reuse the saved tokens — no re-authorisation is needed unless the tokens expire or are deleted.

## Configuration

See `config.example.yaml` for all options. Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `apple_tv.identifier` | *(empty)* | Device identifier from `atvremote scan`. Leave blank to auto-discover the first Apple TV on the network. |
| `trakt.client_id` | *(empty)* | Trakt API client ID (required) |
| `trakt.client_secret` | *(empty)* | Trakt API client secret (required) |
| `scrobble.min_duration` | `120` | Ignore content shorter than N seconds (filters menus and trailers) |
| `scrobble.debounce_seconds` | `30` | Wait N seconds before treating idle as "stopped" (handles brief gaps between episodes) |
| `scrobble.ignored_apps` | `com.apple.Fitness`, `com.apple.TVHomeScreen` | App bundle identifiers to skip |
| `scrobble.media_types` | `video`, `tv` | What to scrobble (`video`, `tv`, `music`) |
| `logging.file` | `data/scrobble.jsonl` | Path to the JSONL event log |
| `logging.level` | `info` | Log level: `trace`, `debug`, `info`, `warning`, `error`. `debug` shows app-level diagnostics; `trace` adds raw protocol frames. |

## Logs

Scrobble events are logged to `data/scrobble.jsonl`:

```json
{"ts":"2026-04-08T20:30:00Z","event":"start","app":"Netflix","title":"Ozymandias","series":"Breaking Bad","season":5,"episode":14,"duration":2820,"progress":0.0,"trakt_action":"start"}
```

## Docker image

The image uses a multi-stage build for a smaller footprint. A built-in healthcheck verifies the asyncio event loop is alive by checking a heartbeat file written every 15 seconds — if the heartbeat is older than 60 seconds, the container is marked unhealthy. The entrypoint supports passing through commands (e.g. `atvremote scan`) for pairing and diagnostics.

## Deployment

On push to `main`, GitHub Actions builds a multi-architecture Docker image (linux/amd64, linux/arm64) and pushes it to `ghcr.io/jaydenk/atv-scrobbler:latest`.

To update the running service, pull the new image and recreate the container:

```bash
cd ~/services/atv-scrobbler
docker compose pull
docker compose up -d
```

## Known limitations

- **pyatv pairing required** — `data/.pyatv.conf` must contain valid pairing credentials. If pairing expires or the Apple TV is factory reset, re-run `docker compose run --rm atv-scrobbler atvremote wizard` and restart the container.
- **Amazon Prime Video** reports `playbackRate=0.0` (appears paused when playing)
- **Netflix** drops metadata during intros and between episodes
- Some niche apps bypass the system media player and report nothing
- `network_mode: host` is required for Bonjour/mDNS — the service will not work with bridge networking
