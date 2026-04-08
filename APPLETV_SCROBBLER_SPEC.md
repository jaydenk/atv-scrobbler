# Apple TV Scrobbler — Design Spec

> v1.0 — April 8, 2026

## Problem

You want to automatically log what's being watched on your Apple TV into the Sequel media tracker app, without manual input.

## Solution

A lightweight Python service running on the always-on machine that:

1. Monitors Apple TV playback via **pyatv** (MRP protocol, push updates)
2. Scrobbles to **Trakt** via its API (start/pause/stop semantics)
3. **Sequel** picks up watched history automatically via its built-in Trakt sync

```
Apple TV ──MRP──→ Scrobbler Service ──API──→ Trakt.tv ──sync──→ Sequel (iOS/Mac)
          (LAN)     (always-on machine)       (cloud)            (automatic)
```

## Prerequisites

| Item | Cost | Notes |
|------|------|-------|
| Trakt account | Free | Scrobble API is free, no VIP needed |
| Trakt API app | Free | Register at trakt.tv/oauth/applications |
| Sequel+ subscription | $2.99/mo or $19.99/yr | Required for Trakt sync |
| Always-on machine on same LAN as Apple TV | — | Runs the scrobbler |
| Python 3.10+ | — | pyatv requires asyncio |
| One-time Apple TV pairing | — | `atvremote wizard` (enter PIN shown on TV) |

---

## Architecture

### Components

```
┌──────────────────────────────────────────────────┐
│  Scrobbler Service (Python, systemd/Docker)       │
│                                                    │
│  ┌─────────────┐   ┌──────────────┐   ┌────────┐ │
│  │ ATV Monitor │──→│ Media Matcher│──→│ Trakt  │ │
│  │ (pyatv push)│   │ (title→ID)   │   │Scrobble│ │
│  └─────────────┘   └──────────────┘   └────────┘ │
│         │                                   │      │
│         v                                   v      │
│  ┌─────────────┐                    ┌────────────┐│
│  │ State Mgr   │                    │ Local Log  ││
│  │ (dedup,     │                    │ (JSONL)    ││
│  │  debounce)  │                    └────────────┘│
│  └─────────────┘                                   │
└──────────────────────────────────────────────────┘
```

### 1. ATV Monitor

Uses pyatv's `PushListener` to receive real-time now-playing updates from the Apple TV over MRP.

**Data received per update:**
- `device_state`: Playing, Paused, Stopped, Idle, Loading, Seeking
- `title`, `artist`, `album`, `genre`
- `series_name`, `season_number`, `episode_number`
- `total_time` (duration in seconds), `position` (current position in seconds)
- `media_type`: Video, Music, TV, Unknown
- `content_identifier` (app-specific content ID)
- `app.name`, `app.identifier` (e.g. "Netflix", "com.netflix.Netflix")
- `hash` (SHA256 of title+artist+album+duration — useful for change detection)

### 2. State Manager

Handles the messy reality of push updates: deduplication, debouncing, and state transitions.

**Key behaviours:**
- **Debounce rapid transitions** — Netflix drops to idle during intros and between episodes. Don't scrobble "stop" for gaps under 30 seconds.
- **Track current item** — Use the `hash` field to detect when content changes vs. position updates for the same content.
- **Calculate progress** — `progress = (position / total_time) * 100`. Trakt auto-marks as watched at >80%.
- **Handle the Amazon Prime bug** — `playbackRate=0.0` reports as "paused" even during playback. If `position` is advancing and state shows paused, treat it as playing.
- **Ignore screensaver/idle** — Don't scrobble when Apple TV is on the home screen or screensaver.
- **Ignore music** — Only scrobble video/TV content (configurable).

**State machine:**

```
                  ┌──────────────┐
                  │   IDLE       │
                  │ (no content) │
                  └──────┬───────┘
                         │ playing detected
                         v
                  ┌──────────────┐
         ┌───────│   PLAYING    │───────┐
         │       │              │       │
         │pause  └──────┬───────┘       │ stopped/idle
         v              │               │ (after debounce)
  ┌──────────────┐      │ content       v
  │   PAUSED     │      │ changed  ┌──────────────┐
  │              │──────┘          │   STOPPED    │
  └──────────────┘                 │ (scrobble    │
         │                         │  stop sent)  │
         │ resumed                 └──────────────┘
         └──→ PLAYING
```

**Scrobble triggers:**
- `IDLE → PLAYING`: Send `scrobble/start` to Trakt
- `PLAYING → PAUSED`: Send `scrobble/pause` to Trakt
- `PAUSED → PLAYING`: Send `scrobble/start` to Trakt (resume)
- `PLAYING → STOPPED` (after debounce): Send `scrobble/stop` with final progress %
- Content changes while playing: Send `scrobble/stop` for old content, `scrobble/start` for new

### 3. Media Matcher

Maps pyatv metadata to Trakt identifiers. This is the trickiest part.

**TV Shows (best case — series_name + season + episode available):**
```python
# Trakt scrobble accepts fuzzy text matching
trakt_scrobble_start(
    show={'title': 'Breaking Bad'},
    episode={'season': 3, 'number': 7},
    progress=1
)
```
Trakt does its own fuzzy matching on title. No need to pre-resolve IDs for most cases.

**Movies (only title available):**
```python
trakt_scrobble_start(
    movie={'title': 'The Matrix', 'year': 1999},
    progress=1
)
```
Year helps disambiguation. If not available from pyatv, omit and let Trakt guess (works well for popular titles).

**Fallback — search then scrobble:**
If Trakt's fuzzy matching fails (returns 404), do an explicit search:
```python
results = trakt_search('movie', query=title)
if results:
    trakt_scrobble_start(movie={'ids': {'trakt': results[0].id}}, progress=1)
```

**Edge cases to handle:**
- Netflix titles like `"S1: E7 'Bells'"` in the title field with no series_name → parse with regex
- Apps that put everything in the title field → split on common patterns
- Unknown/unmatchable content → log locally but don't send to Trakt (avoid polluting history)
- Music content → skip (or make configurable)

### 4. Trakt Scrobble Client

Wraps the Trakt API. Uses OAuth2 device code flow for initial auth (no browser needed on the server).

**API endpoints used:**
- `POST /scrobble/start` — start watching
- `POST /scrobble/pause` — pause
- `POST /scrobble/stop` — stop watching (marks as watched if progress > 80%)
- `GET /search/{type}` — search for movie/show (fallback matching)
- `POST /sync/history` — direct history add (batch import, backfill)

**Auth flow:**
1. Service starts, no tokens found
2. Calls `POST /oauth/device/code` → gets a user code + verification URL
3. Prints: "Go to https://trakt.tv/activate and enter code: XXXXXX"
4. Polls `POST /oauth/device/token` until user approves
5. Stores access + refresh tokens in config file
6. Auto-refreshes tokens on expiry

**Rate limits:** Trakt allows 1000 calls per 5 minutes. Scrobbling generates ~3-5 calls per viewing session. Not a concern.

### 5. Local Log

Append-only JSONL file logging all scrobble events for debugging and local history.

```jsonl
{"ts":"2026-04-08T20:30:00Z","event":"start","app":"Netflix","title":"Breaking Bad","series":"Breaking Bad","season":3,"episode":7,"duration":2820,"trakt_id":62085,"trakt_status":200}
{"ts":"2026-04-08T21:17:00Z","event":"stop","app":"Netflix","title":"Breaking Bad","series":"Breaking Bad","season":3,"episode":7,"progress":97,"trakt_id":62085,"trakt_status":200}
```

---

## Configuration

```yaml
# config.yaml
apple_tv:
  identifier: "AA:BB:CC:DD:EE:FF"   # from atvremote scan
  # credentials stored separately by pyatv after pairing

trakt:
  client_id: "your-app-client-id"
  client_secret: "your-app-client-secret"
  # tokens stored in tokens.json after first auth

scrobble:
  min_duration: 120          # ignore content shorter than 2 min
  debounce_seconds: 30       # wait before treating idle as "stopped"
  ignored_apps:              # apps to skip
    - "com.apple.Fitness"
    - "com.apple.TVHomeScreen"
  media_types:               # what to scrobble
    - video
    - tv
  progress_threshold: 80     # % at which Trakt marks as watched (Trakt default)

logging:
  file: "scrobble.jsonl"
  level: "info"
```

---

## Deployment

### Option A: systemd service (recommended for always-on Linux/Mac)

```ini
# /etc/systemd/system/atv-scrobbler.service
[Unit]
Description=Apple TV Trakt Scrobbler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kerrj
WorkingDirectory=/opt/atv-scrobbler
ExecStart=/opt/atv-scrobbler/.venv/bin/python -m atv_scrobbler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Option B: Docker Compose

```yaml
services:
  atv-scrobbler:
    build: .
    restart: unless-stopped
    network_mode: host      # required for mDNS/Bonjour discovery
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./tokens.json:/app/tokens.json
      - ./scrobble.jsonl:/app/scrobble.jsonl
    environment:
      - TZ=Pacific/Auckland
```

`network_mode: host` is required because pyatv uses Bonjour/mDNS for device discovery and MRP uses ephemeral ports. Bridge networking breaks both.

### Option C: launchd (macOS)

```xml
<!-- ~/Library/LaunchAgents/com.atv-scrobbler.plist -->
<plist version="1.0">
<dict>
    <key>Label</key><string>com.atv-scrobbler</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/atv-scrobbler/.venv/bin/python</string>
        <string>-m</string>
        <string>atv_scrobbler</string>
    </array>
    <key>WorkingDirectory</key><string>/opt/atv-scrobbler</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
</dict>
</plist>
```

---

## Project Structure

```
atv-scrobbler/
├── pyproject.toml
├── config.yaml
├── Dockerfile
├── docker-compose.yml
├── README.md
└── atv_scrobbler/
    ├── __init__.py
    ├── __main__.py          # entry point, asyncio.run()
    ├── monitor.py           # pyatv connection + push listener
    ├── state.py             # state machine + debounce logic
    ├── matcher.py           # pyatv metadata → Trakt identifiers
    ├── trakt_client.py      # Trakt API wrapper (auth, scrobble, search)
    └── logger.py            # JSONL local logging
```

---

## Dependencies

```toml
[project]
requires-python = ">=3.10"
dependencies = [
    "pyatv>=0.16.0",
    "httpx>=0.27.0",        # async HTTP for Trakt API
    "pyyaml>=6.0",          # config file
]
```

Using `httpx` directly for Trakt API rather than `trakt.py` — the scrobble API surface is small (3 endpoints) and we need async support to match pyatv's asyncio loop. Avoids pulling in a heavy sync library.

---

## Implementation Plan

### Phase 1: Core scrobbler (MVP)

- pyatv connection + push listener
- State machine (playing/paused/stopped with debounce)
- Trakt OAuth device flow auth
- Trakt scrobble start/pause/stop for TV shows (series_name + season + episode)
- Local JSONL logging
- Config file
- Run as a script

### Phase 2: Movie support + matching

- Movie scrobbling (title-only matching)
- Trakt search fallback for unmatched content
- Netflix title parsing (regex for "S1: E7 'Title'" format)
- Amazon Prime playbackRate workaround

### Phase 3: Deployment

- Dockerfile + docker-compose.yml
- systemd / launchd service files
- Reconnection logic (Apple TV sleeps/wakes, network drops)
- Graceful shutdown (scrobble stop on SIGTERM)

### Phase 4: Polish

- Multiple Apple TV support
- Web dashboard (optional — simple status page showing current state + recent scrobbles)
- Prometheus metrics (optional — scrobble count, error rate, uptime)
- Backfill from local log to Trakt (if Trakt was down)

---

## Known Limitations

1. **App-dependent metadata quality** — Amazon Prime reports paused when playing. Some niche apps report nothing. See APPLE_TV_NOW_PLAYING.md for per-app breakdown.
2. **Sequel sync delay** — Trakt → Sequel sync is not instant. Items may take minutes to hours to appear in Sequel depending on sync frequency.
3. **Title matching is fuzzy** — Trakt's scrobble endpoint handles most cases but can misidentify content with generic titles.
4. **Network dependency** — Apple TV and scrobbler must be on the same LAN. Trakt API requires internet.
5. **Pairing is manual** — Initial Apple TV pairing requires entering a PIN shown on the TV screen. Must be redone if credentials are lost.
6. **tvOS updates can break things** — pyatv tracks protocol changes but needs updating when tvOS updates. The service should pin pyatv to a tested version and update deliberately.

---

## Confirmed Setup (Apr 8, 2026)

1. **Always-on machine is on the same LAN as the Apple TV** ✓
2. **Docker Compose deployment** (consistent with other homelab services)
3. **Single Apple TV** (no multi-ATV support needed initially)
4. **Trakt account exists** (needs API app registration + Sequel connection)

---

## References

- [pyatv documentation](https://pyatv.dev/)
- [Trakt API docs](https://trakt.docs.apiary.io/)
- [Trakt scrobble API](https://trakt.docs.apiary.io/#reference/scrobble)
- [Trakt OAuth device flow](https://trakt.docs.apiary.io/#reference/authentication-devices)
- [trakt-for-appletv (archived, proof of concept)](https://github.com/stigger/trakt-for-appletv)
- [Sequel Trakt sync (v2.1 changelog)](https://www.getsequel.app/changelogs/2-1-changelog)
- Research: APPLE_TV_NOW_PLAYING.md, SEQUEL_AUTOMATION_RESEARCH.md
