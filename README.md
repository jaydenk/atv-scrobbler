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

pyatv requires pairing credentials to connect to your Apple TV. These credentials are stored in a `.pyatv.conf` file.

#### Find your Apple TV

```bash
pip install pyatv
atvremote scan
```

Note the **Identifier** from the scan output — you will need it for the configuration file.

#### Run the pairing wizard

**If running natively** (on the same machine):

```bash
atvremote wizard
```

This pairs all protocols (MRP, AirPlay, Companion) and stores credentials in `$HOME/.pyatv.conf`.

**If running via Docker** (recommended):

Run the wizard from the service directory so the credentials file is created where Docker can mount it:

```bash
cd /path/to/atv-scrobbler
atvremote --storage .pyatv.conf wizard
```

Follow the on-screen prompts and enter the PIN displayed on your Apple TV for each protocol. Once complete, the `.pyatv.conf` file will contain the pairing credentials needed by the container.

> **Note:** If you do not already have pyatv installed on the host, you can pair inside a temporary container:
> ```bash
> docker run --rm -it --network host -v "$(pwd)/.pyatv.conf:/root/.pyatv.conf" ghcr.io/jaydenk/atv-scrobbler:latest atvremote wizard
> ```

### 3. Configure

```bash
cp config.example.yaml config.yaml
```

Fill in your Trakt `client_id`, `client_secret`, and Apple TV `identifier`.

### 4. Run with Docker Compose

```bash
# Create empty files for volume mounts
touch trakt_tokens.json scrobble.jsonl

# If you haven't already created .pyatv.conf via the pairing wizard:
touch .pyatv.conf

# Edit config.yaml with your Trakt credentials and Apple TV identifier
# Then start the service
docker compose up -d

# Watch for the Trakt authorisation prompt on first run
docker compose logs -f
```

The container image is pulled from GHCR:

```yaml
# docker-compose.yml
services:
  atv-scrobbler:
    image: ghcr.io/jaydenk/atv-scrobbler:latest
    restart: unless-stopped
    network_mode: host
    environment:
      - HOME=/app
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./trakt_tokens.json:/app/trakt_tokens.json
      - ./scrobble.jsonl:/app/scrobble.jsonl
      - ./.pyatv.conf:/app/.pyatv.conf
      - /etc/localtime:/etc/localtime:ro
```

The `HOME=/app` environment variable tells pyatv to look for `.pyatv.conf` inside the container at `/app/.pyatv.conf`, which is volume-mounted from the host.

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

Open [trakt.tv/activate](https://trakt.tv/activate) in a browser, sign in, and enter the code shown in the logs. Once authorised, tokens are saved to `trakt_tokens.json` and the service begins monitoring your Apple TV. Subsequent restarts reuse the saved tokens — no re-authorisation is needed unless the tokens expire or are deleted.

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
| `logging.file` | `scrobble.jsonl` | Path to the JSONL event log |
| `logging.level` | `info` | Log level (`debug`, `info`, `warning`, `error`) |

## Logs

Scrobble events are logged to `scrobble.jsonl`:

```json
{"ts":"2026-04-08T20:30:00Z","event":"start","app":"Netflix","title":"Ozymandias","series":"Breaking Bad","season":5,"episode":14,"duration":2820,"progress":0.0,"trakt_action":"start"}
```

## Docker image

The image uses a multi-stage build for a smaller footprint and runs as a non-root `scrobbler` user for security hardening. A built-in healthcheck verifies the asyncio event loop is alive by checking a heartbeat file written every 15 seconds — if the heartbeat is older than 60 seconds, the container is marked unhealthy.

## Deployment

On push to `main`, GitHub Actions builds a multi-architecture Docker image (linux/amd64, linux/arm64) and pushes it to `ghcr.io/jaydenk/atv-scrobbler:latest`.

To update the running service, pull the new image and recreate the container:

```bash
cd ~/services/atv-scrobbler
docker compose pull
docker compose up -d
```

## Known limitations

- **pyatv pairing required** — the `.pyatv.conf` file must contain valid pairing credentials. If pairing expires or the Apple TV is factory reset, you will need to re-run `atvremote wizard` and restart the container.
- **Amazon Prime Video** reports `playbackRate=0.0` (appears paused when playing)
- **Netflix** drops metadata during intros and between episodes
- Some niche apps bypass the system media player and report nothing
- `network_mode: host` is required for Bonjour/mDNS — the service will not work with bridge networking
