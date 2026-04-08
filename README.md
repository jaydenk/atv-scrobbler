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

### 2. Pair with Apple TV

Install pyatv and run the pairing wizard:

```bash
pip install pyatv
atvremote scan          # find your Apple TV
atvremote wizard        # pair all protocols (enter PIN shown on TV)
```

Note the **Identifier** from the scan output.

### 3. Configure

```bash
cp config.example.yaml config.yaml
```

Fill in your Trakt `client_id`, `client_secret`, and Apple TV `identifier`.

### 4. Run with Docker Compose

```bash
# Create empty files for volume mounts
touch trakt_tokens.json scrobble.jsonl

docker compose up -d
```

The container image is pulled from GHCR:

```yaml
# docker-compose.yml
services:
  atv-scrobbler:
    image: ghcr.io/jaydenk/atv-scrobbler:latest
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./trakt_tokens.json:/app/trakt_tokens.json
      - ./scrobble.jsonl:/app/scrobble.jsonl
      - /etc/localtime:/etc/localtime:ro
```

### 5. Connect Sequel to Trakt

In Sequel (requires Sequel+): Settings > Trakt > Connect. Choose "Merge" to keep existing data.

## First Run

On first start, the service runs an interactive Trakt OAuth device flow. Check the container logs:

```bash
docker compose logs -f atv-scrobbler
```

You will see a message like:

```
Go to https://trakt.tv/activate and enter code: A1B2C3D4
```

Open [trakt.tv/activate](https://trakt.tv/activate) in a browser, sign in, and enter the code shown in the logs. Once authorised, tokens are saved to `trakt_tokens.json` and the service begins monitoring your Apple TV. Subsequent restarts reuse the saved tokens — no re-authorisation is needed unless the tokens expire or are deleted.

## Configuration

See `config.example.yaml` for all options. Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `scrobble.min_duration` | 120 | Ignore content shorter than N seconds |
| `scrobble.debounce_seconds` | 30 | Wait N seconds before treating idle as "stopped" |
| `scrobble.ignored_apps` | Fitness, Home Screen | App bundle IDs to skip |
| `scrobble.media_types` | video, tv | What to scrobble (also: music) |

## Logs

Scrobble events are logged to `scrobble.jsonl`:

```json
{"ts":"2026-04-08T20:30:00Z","event":"start","app":"Netflix","title":"Ozymandias","series":"Breaking Bad","season":5,"episode":14,"duration":2820,"progress":0.0,"trakt_action":"start"}
```

## Docker Image

The image uses a multi-stage build for a smaller footprint and runs as a non-root `scrobbler` user for security hardening. A built-in healthcheck verifies the Python runtime is responsive.

## Deployment

CI/CD is handled by GitHub Actions:

- **Build:** On push to `main`, a workflow builds a multi-architecture Docker image (linux/amd64, linux/arm64) and pushes it to `ghcr.io/jaydenk/atv-scrobbler:latest`.
- **Deploy:** After a successful build, the workflow connects to **pimento** (Raspberry Pi 5) via Tailscale SSH and runs `docker compose pull && docker compose up -d` to roll out the new image.

No manual builds or image transfers are required — merging to `main` triggers the full pipeline.

## Known limitations

- **Amazon Prime Video** reports `playbackRate=0.0` (appears paused when playing)
- **Netflix** drops metadata during intros and between episodes
- Some niche apps bypass the system media player and report nothing
- `network_mode: host` is required for Bonjour/mDNS — won't work with bridge networking
