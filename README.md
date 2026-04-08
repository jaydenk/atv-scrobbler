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

On first run, check the logs for the Trakt authorization prompt:

```bash
docker compose logs -f
```

You'll see a code to enter at https://trakt.tv/activate. After authorizing, tokens are saved and the service starts monitoring.

### 5. Connect Sequel to Trakt

In Sequel (requires Sequel+): Settings > Trakt > Connect. Choose "Merge" to keep existing data.

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

## Known limitations

- **Amazon Prime Video** reports `playbackRate=0.0` (appears paused when playing)
- **Netflix** drops metadata during intros and between episodes
- Some niche apps bypass the system media player and report nothing
- `network_mode: host` is required for Bonjour/mDNS — won't work with bridge networking
