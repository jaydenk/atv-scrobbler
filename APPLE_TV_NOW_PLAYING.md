# Apple TV "Now Playing" API Research

**Date:** March 31, 2026
**Purpose:** Determine how to programmatically query what is currently playing on an Apple TV from another device on the same LAN.

---

## Executive Summary

**Yes, Apple TV exposes now-playing metadata over the network, and it can be queried programmatically.** The primary mechanism is the **MRP (Media Remote Protocol)**, a protobuf-over-TCP protocol introduced with the 4th-generation Apple TV. The best tool for accessing it is **pyatv**, a mature Python library that provides both polling and real-time push updates for now-playing state. Home Assistant's Apple TV integration (which uses pyatv under the hood) confirms this works in production.

However, metadata completeness **varies by app** -- some streaming apps (notably Amazon Prime Video, BBC iPlayer) report incomplete or incorrect data, and all apps can have brief metadata gaps during transitions (intros, "next episode" screens, etc.).

---

## 1. Protocols That Expose Now-Playing Metadata

### MRP (Media Remote Protocol) -- Primary Protocol

- **What it is:** Apple's proprietary protocol for the Remote app and Control Center widget. Introduced with Apple TV 4 (tvOS).
- **Transport:** Length-prefixed Protocol Buffers over an encrypted TCP connection.
- **Discovery:** Bonjour/mDNS service `_mediaremotetv._tcp.local`. Port is ephemeral (typically starting at 49152).
- **Encryption:** Chacha20Poly1305, session key derived via HAP (HomeKit Authentication Protocol).
- **Now-playing mechanism:** The device sends `SetStateMessage` protobuf messages containing `NowPlayingInfo`, `SupportedCommands`, and `PlaybackQueue`. These can be received passively (push) or actively polled via `PlaybackQueueRequestMessage`.
- **Metadata fields:** Title, artist, album, genre, duration, position, media type, series name, season/episode numbers, content identifier, iTunes Store identifier, artwork, shuffle/repeat state, playback state.
- **Limitation:** Passive `SetStateMessage` updates can be delayed by several seconds. Active polling (every few seconds) yields more responsive results.

### DMAP (Digital Audio/Media Access Protocol) -- Legacy

- **What it is:** HTTP-based protocol on port 3689, used by older Apple TV models (Gen 1-3).
- **Metadata fields:** Title, artist, album, genre, duration, position, artwork, shuffle, repeat.
- **Missing from DMAP:** `series_name`, `season_number`, `episode_number` (TV show metadata not available).
- **Authentication:** Session-based via HSGID or pairing GUID.
- **Still relevant?** Only for Apple TV 3 and earlier. Modern Apple TVs (4th gen+) use MRP.

### AirPlay -- Limited Metadata

- **Metadata is sent *to* the Apple TV** when streaming from another device (e.g., iPhone to Apple TV). The AirPlay receiver shows track name, artist, album, and artwork sent by the client.
- **Cannot be used to query what a locally-running app is playing.** AirPlay metadata only reflects content being streamed *to* the device, not content played natively in apps like Netflix or Plex.
- **AirPlay 2** adds HAP-based authentication and encrypted channels but does not change the metadata model.

### Companion Link Protocol -- No Metadata

- **Purpose:** App launching, power management, touch/HID control, system status (awake/asleep/screensaver).
- **Does NOT provide now-playing metadata.** Use MRP for that.
- **Useful for:** Determining which app is active (`App.name`, `App.identifier`), waking/sleeping the device, launching apps.

### RAOP (Remote Audio Output Protocol / AirTunes) -- Audio Only

- **Audio streaming protocol.** Metadata only reflects what pyatv itself is streaming to the device, not what is playing locally.

---

## 2. pyatv -- The Primary Tool

[pyatv](https://github.com/postlund/pyatv) is an asyncio Python library by postlund. It is the most mature and complete open-source implementation for Apple TV control. Home Assistant's Apple TV integration is built on top of it.

### Installation

```bash
pip install pyatv
```

### Device Discovery

```bash
atvremote scan
```

Returns device names, models, IP addresses, MAC addresses, and available services (MRP, Companion, AirPlay, RAOP).

### Pairing

```bash
# Guided wizard (recommended)
atvremote wizard

# Or pair individual protocols
atvremote --id <device-id> --protocol mrp pair
atvremote --id <device-id> --protocol companion pair
atvremote --id <device-id> --protocol airplay pair
```

Pairing displays a PIN on the Apple TV screen that you enter on the client. Credentials are saved to local file storage (as of v0.14.0). You should pair **all available protocols** for maximum functionality.

### Polling Now-Playing Data

```bash
# CLI
atvremote --id <device-id> playing
```

```python
# Python
import asyncio
import pyatv

async def main():
    atvs = await pyatv.scan(asyncio.get_event_loop())
    config = atvs[0]
    atv = await pyatv.connect(config, asyncio.get_event_loop())
    try:
        playing = await atv.metadata.playing()
        print(f"State:    {playing.device_state}")
        print(f"Title:    {playing.title}")
        print(f"Artist:   {playing.artist}")
        print(f"Album:    {playing.album}")
        print(f"Genre:    {playing.genre}")
        print(f"Type:     {playing.media_type}")
        print(f"Duration: {playing.total_time}s")
        print(f"Position: {playing.position}s")
        print(f"Series:   {playing.series_name}")
        print(f"Season:   {playing.season_number}")
        print(f"Episode:  {playing.episode_number}")
    finally:
        atv.close()

asyncio.run(main())
```

### Real-Time Push Updates

```python
from pyatv import interface

class MyPushListener(interface.PushListener):
    def playstatus_update(self, updater, playstatus):
        print(f"Now playing: {playstatus.title} ({playstatus.device_state})")

    def playstatus_error(self, updater, exception):
        print(f"Error: {exception}")

# After connecting:
listener = MyPushListener()  # MUST keep a reference (weak refs used internally)
atv.push_updater.listener = listener
atv.push_updater.start()
# Updates are delivered to playstatus_update whenever state changes
```

Push updates are only sent when state actually changes -- no duplicate callbacks.

### Available Metadata Fields (Playing class)

| Field | Type | Description |
|---|---|---|
| `device_state` | `DeviceState` | Idle, Loading, Paused, Playing, Stopped, Seeking |
| `media_type` | `MediaType` | Unknown, Video, Music, TV |
| `title` | `str \| None` | Title of current media (movie/song/episode name) |
| `artist` | `str \| None` | Artist name |
| `album` | `str \| None` | Album name |
| `genre` | `str \| None` | Genre |
| `total_time` | `int \| None` | Total duration in seconds |
| `position` | `int \| None` | Current position in seconds |
| `shuffle` | `ShuffleState` | Off, Albums, Songs |
| `repeat` | `RepeatState` | Off, Track, All |
| `series_name` | `str \| None` | TV series title |
| `season_number` | `int \| None` | Season number |
| `episode_number` | `int \| None` | Episode number |
| `content_identifier` | `str \| None` | App-specific content ID |
| `itunes_store_identifier` | `int \| None` | iTunes Store ID |
| `hash` | `str` | SHA256 of title+artist+album+duration (change detection) |

### Additional Metadata

```python
# Current artwork (returns bytes)
artwork = await atv.metadata.artwork(width=300, height=200)

# Current app
app = atv.metadata.app
print(f"App: {app.name} ({app.identifier})")
# e.g., "Netflix (com.netflix.Netflix)"
```

---

## 3. Home Assistant Integration

The [Home Assistant Apple TV integration](https://www.home-assistant.io/integrations/apple_tv/) uses pyatv internally and exposes:

### Media Player Entity Attributes

- `media_title` -- current title
- `media_content_type` -- "video", "music", "tvshow"
- `media_duration` -- total length
- `media_position` -- current position
- `media_content_id` -- content identifier
- `app_id` -- bundle identifier of current app
- `app_name` -- display name of current app
- `entity_picture` -- artwork URL
- `state` -- playing, paused, idle, standby, off

### Remote Entity

Exposes all remote control buttons: wakeup, suspend, home, menu, select, play, pause, up, down, left, right, volume_up, volume_down, previous, next, skip_backward, skip_forward.

### Binary Sensor

`binary_sensor.apple_tv_keyboard` -- indicates when on-screen keyboard is focused.

### Template Sensor for Logging

```yaml
template:
  - sensor:
      - name: "Apple TV Now Playing"
        state: "{{ state_attr('media_player.apple_tv', 'media_title') }}"
        attributes:
          app: "{{ state_attr('media_player.apple_tv', 'app_name') }}"
          content_type: "{{ state_attr('media_player.apple_tv', 'media_content_type') }}"
```

---

## 4. Other Tools and Libraries

### node-appletv (Node.js)

- **Repo:** [github.com/evandcoleman/node-appletv](https://github.com/evandcoleman/node-appletv) (also forked as `node-appletv-x` on npm)
- **What it does:** Node.js implementation of MRP. Supports pairing, remote control, and now-playing state monitoring.
- **CLI commands:** `scan`, `pair`, `command`, `state` (logs state changes including now-playing info), `artwork`.
- **Status:** Less actively maintained than pyatv. The original repo has limited recent activity. Several forks exist (casey-chow, socalrds, VannaDii, SeppSTA).
- **TypeScript:** Written in TypeScript.
- **License:** MIT.

### node-red-contrib-apple-tv (Node-RED)

- Node-RED nodes for Apple TV integration. Uses pyatv under the hood.

### atvlib (Go)

- **Repo:** [github.com/samthor/atvlib](https://github.com/samthor/atvlib)
- **Status:** Minimal Go library. Limited documentation, unclear feature completeness. Not a mature alternative to pyatv.

### atvremote (Go)

- **Repo:** `github.com/drosocode/atvremote`
- **Status:** Another Go implementation, available as a package on pkg.go.dev. Limited documentation.

### atvproxy (pyatv tool)

- **What it does:** Proxy tool that sits between an Apple device (e.g., iPhone Remote app) and Apple TV. Fully decrypts and logs all MRP messages.
- **Useful for:** Protocol research, debugging, understanding what metadata apps actually send.

---

## 5. Limitations and Per-App Behavior

### General Principle

The Apple TV exposes now-playing metadata **only if the app reports it via the system media player framework (MPNowPlayingInfoCenter)**. Apps that implement their own custom media player and bypass the system framework will show no metadata or incomplete metadata. **There is nothing pyatv or any external tool can do about this -- they can only read what the device reports.**

### Per-App Behavior

| App | Title | Duration/Position | Media Type | Notes |
|---|---|---|---|---|
| **Apple TV+** | Yes | Yes | Yes | Best integration (Apple's own app) |
| **Netflix** | Yes (e.g., "S1: E7 'Bells'") | Yes | Yes | Goes idle during intros and "next episode" screens. App name sometimes returns null. |
| **Plex** | Yes | Yes | Yes | Generally good metadata. Reports media type correctly. |
| **Spotify** | Yes | Yes | Yes (Music) | Good metadata for music playback. |
| **YouTube** | Partial | Partial | Partial | Entity picture/artwork may not work. Metadata quality varies. |
| **Disney+** | Yes | Yes | Yes | Generally works but may vary by content. |
| **Amazon Prime Video** | Partial | Yes | Partial | **Known bug:** Reports `playbackRate=0.0` instead of `1.0`, causing state to show "paused" even during playback. |
| **BBC iPlayer** | Partial | Yes | Partial | Same `playbackRate` bug as Amazon Prime Video. |
| **Max (HBO)** | Partial | Partial | Sometimes missing | `media_content_type` may not be populated. |
| **Cellcom TV** | None | None | None | Implements own media player, bypasses system metadata entirely. |

### Common Metadata Gaps

1. **Intro sequences:** Netflix (and likely others) report idle/no metadata during show intros.
2. **"Next episode" screens:** Metadata drops to null between episodes.
3. **Preview/auto-play:** Netflix preview content sends play status updates that can be confusing. Disabling auto-play previews in Netflix settings is recommended.
4. **App name:** Sometimes returns null even when other fields are populated.
5. **Artwork:** Not available for all apps. YouTube artwork is a known issue.

### DRM Impact

DRM does **not** block now-playing metadata. The MRP metadata channel is separate from the content decryption pipeline. Whether an app reports metadata depends on whether it uses Apple's `MPNowPlayingInfoCenter` API, not on DRM status. DRM-protected content from Netflix, Disney+, etc. still reports title/position/duration when the app cooperates.

---

## 6. tvOS Compatibility

### tvOS 17

- Fully supported by pyatv. No major known issues.

### tvOS 18 (18.0 - 18.3)

- **MAC address change:** tvOS 18 may assign a different MAC address to the device, breaking scripts that identify by MAC. Use device unique identifier instead.
- **OPACK decoding issue:** Companion protocol has an extra null byte in NowPlayingInfo packets from tvOS 18 clients. A fix was merged in pyatv (PR #2472).
- **`/playback-info` endpoint:** AirPlay 1's playback-info endpoint returns HTTP 500 on tvOS 18. This only affects legacy AirPlay 1 calls, not MRP metadata.
- **Overall:** MRP now-playing queries work on tvOS 18.0-18.3 with recent pyatv versions.

### tvOS 18.4

- **Companion protocol broken:** A hardcoded identifier (`_i: "cafecafecafe"`) in pyatv's companion protocol handshake is rejected by tvOS 18.4, causing immediate disconnection.
- **Fix merged:** pyatv now generates random per-device identifiers stored persistently.
- **MRP/AirPlay unaffected:** Only companion protocol is impacted; MRP (the protocol used for now-playing) and AirPlay continue to work.

### Recommendation

Use the latest pyatv release. Keep pyatv updated as tvOS updates occasionally require protocol adjustments. The library is actively maintained and tracks tvOS changes.

---

## 7. Logging Approaches

### Approach A: Standalone Python Script with pyatv

The simplest approach -- a Python script using pyatv's push updater to log state changes to a file or database.

```python
import asyncio
import json
import datetime
from pyatv import interface
import pyatv

class NowPlayingLogger(interface.PushListener):
    def __init__(self, log_file="viewing_log.jsonl"):
        self.log_file = log_file

    def playstatus_update(self, updater, playstatus):
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "state": str(playstatus.device_state),
            "title": playstatus.title,
            "artist": playstatus.artist,
            "album": playstatus.album,
            "media_type": str(playstatus.media_type),
            "series": playstatus.series_name,
            "season": playstatus.season_number,
            "episode": playstatus.episode_number,
            "duration": playstatus.total_time,
            "position": playstatus.position,
        }
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"[{entry['timestamp']}] {entry['state']}: {entry['title']}")

    def playstatus_error(self, updater, exception):
        print(f"Error: {exception}")

async def main():
    atvs = await pyatv.scan(asyncio.get_event_loop())
    if not atvs:
        print("No Apple TV found")
        return
    atv = await pyatv.connect(atvs[0], asyncio.get_event_loop())
    logger = NowPlayingLogger()
    atv.push_updater.listener = logger
    atv.push_updater.start()

    # Also log the current app
    try:
        while True:
            await asyncio.sleep(3600)  # keep running
    finally:
        atv.close()

asyncio.run(main())
```

Run as a systemd service or Docker container for persistent logging. Output is JSONL for easy processing.

### Approach B: Home Assistant + Recorder

1. Set up the Apple TV integration in Home Assistant.
2. Configure the recorder to track `media_player.apple_tv` with all attributes.
3. Create template sensors for specific fields you want to track.
4. Use the History panel or InfluxDB/Grafana for visualization.
5. Optionally create automations that log to a webhook, database, or notification service when content changes.

### Approach C: Home Assistant + Automation to External Log

```yaml
automation:
  - alias: "Log Apple TV Now Playing"
    trigger:
      - platform: state
        entity_id: media_player.apple_tv
        attribute: media_title
    condition:
      - condition: state
        entity_id: media_player.apple_tv
        state: "playing"
    action:
      - service: rest_command.log_viewing
        data:
          title: "{{ state_attr('media_player.apple_tv', 'media_title') }}"
          app: "{{ state_attr('media_player.apple_tv', 'app_name') }}"
          content_type: "{{ state_attr('media_player.apple_tv', 'media_content_type') }}"
          timestamp: "{{ now().isoformat() }}"
```

### Existing Projects

No dedicated "Apple TV viewing history logger" project was found on GitHub. The closest is:
- **Home Assistant's built-in recorder** tracking the media_player entity state history
- **pyatv's CLI tool `atvremote`** which can output now-playing data as JSON for scripting
- The Home Assistant community thread ["How to record Apple TV viewing history?"](https://community.home-assistant.io/t/how-to-record-apple-tv-viewing-history/413354) discusses approaches but no standalone project emerged from it

Building a custom logger with pyatv (Approach A above) is straightforward and would be a good candidate for a small open-source project.

---

## 8. Recommendation

**For logging what is played on an Apple TV:**

1. **If you already run Home Assistant:** Use the Apple TV integration. It exposes all available metadata as entity attributes. Add template sensors and automations to log changes. This is the lowest-effort path.

2. **If you want a standalone solution:** Use pyatv directly. Write a small Python script with push updates (see Approach A above). Run it as a service. This gives you the most control over what gets logged and where.

3. **Pair all protocols:** When setting up, pair MRP + Companion + AirPlay. MRP provides the now-playing data. Companion provides app information and power state. AirPlay is needed for some operations.

4. **Expect per-app variation:** Netflix, Plex, Apple TV+, Spotify, and Disney+ generally provide good metadata. Amazon Prime Video and BBC iPlayer have a known `playbackRate` bug. Some niche apps provide nothing. Always log the `app.identifier` alongside now-playing data so you can contextualize gaps.

5. **Keep pyatv updated:** tvOS updates regularly break protocol details. The pyatv maintainer tracks these, but you need to update the library when you update tvOS.

---

## Sources

- [pyatv GitHub Repository](https://github.com/postlund/pyatv)
- [pyatv Official Documentation](https://pyatv.dev/)
- [pyatv API Interface (Playing class)](https://pyatv.dev/api/interface/)
- [pyatv Protocols Documentation](https://pyatv.dev/documentation/protocols/)
- [pyatv Supported Features Matrix](https://pyatv.dev/documentation/supported_features/)
- [pyatv Metadata Guide](https://pyatv.dev/development/metadata/)
- [pyatv FAQ](https://pyatv.dev/support/faq/)
- [pyatv Constants (MediaType, DeviceState)](https://pyatv.dev/api/const/)
- [pyatv Push Listeners](https://pyatv.dev/development/listeners/)
- [pyatv Getting Started](https://pyatv.dev/documentation/getting-started/)
- [MRP Protocol Dissection (Evan Coleman)](https://medium.com/@evancoleman/apple-tv-meet-my-lights-dissecting-the-media-remote-protocol-d07d1909ad82)
- [Unofficial AirPlay Protocol Specification](https://nto.github.io/AirPlay.html)
- [AirPlay Service Discovery Spec](https://openairplay.github.io/airplay-spec/service_discovery.html)
- [Home Assistant Apple TV Integration](https://www.home-assistant.io/integrations/apple_tv/)
- [HA Community: Record Apple TV Viewing History](https://community.home-assistant.io/t/how-to-record-apple-tv-viewing-history/413354)
- [HA Community: Apple TV Media Type Issues](https://community.home-assistant.io/t/apple-tv-no-longer-showing-media-type/594622)
- [node-appletv (Node.js)](https://github.com/evandcoleman/node-appletv)
- [node-appletv-x on npm](https://www.npmjs.com/package/node-appletv-x)
- [pyatv tvOS 18 Compatibility Tracking](https://github.com/postlund/pyatv/issues/2403)
- [pyatv tvOS 18.4 Companion Fix](https://github.com/postlund/pyatv/issues/2656)
- [Netflix Intro State Issue](https://github.com/postlund/pyatv/issues/605)
