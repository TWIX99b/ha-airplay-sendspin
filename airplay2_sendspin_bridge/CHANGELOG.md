# Changelog

## 0.4.3

- "Measure" starts the matching track itself (click track for timing, noise
  track for volume), waits until the speaker receives it via AirPlay, and
  stops it afterwards. If something already plays on the speaker (Music app,
  second device), the page leaves it alone. Switching between the tracks
  waits for the old one to drain from the AirPlay buffers.

## 0.4.2

- The click and noise tracks can be played directly on the "AirPlay Sync"
  page (▶ button, loops). The audio follows the device's output, so set it
  to the AirPlay group first. The tracks are served with Range support,
  which Safari needs for playback.
- The measurement notices when AirPlay playback stopped in between (e.g.
  because the microphone interrupts it on iPhone/iPad) and says so.

## 0.4.1

- Wording: "zone" is now called "speaker" ("Lautsprecher") on the page,
  in the option texts and in the docs. The option key `zones` stays the
  same, so existing configurations keep working.

## 0.4.0

- **Speaker volume during AirPlay** (`speaker_volume`, default 100 %): the
  add-on turns the speaker itself up via Sendspin while AirPlay plays and
  restores the old value afterwards. Loudness is then controlled via AirPlay
  like a HomePod. Fixes very quiet Echos.
- **Volume matching** on the "AirPlay Sync" page: noise track, the zone and
  the reference are measured separately, result as `volume_trim_db`
  (0 to −30 dB, simulated accuracy ±0.5 dB), applied live and saved.
- Manual ± dB buttons for the trim.
- DOCS: volume section (de/en), info table with a `volume_trim_db` column.
- tests/test_volume_matching.py: end-to-end simulation with the real bridge
  and page code.

## 0.3.0

- **Automatic calibration** via its own page "AirPlay Sync" (ingress):
  click track, microphone recording in the browser, zone muted alternately,
  evaluation by folding onto the click period with onset detection
  (simulated accuracy ±1–2 ms). The result is applied live and saved in the
  options.
- Manual ± fine tuning on the page, takes effect immediately while playing.
- `latency_offset_ms` can be changed live (no restart needed).
- Info table with measured values in DOCS.

## 0.2.1

- Add-on description in English, DOCS in German and English.
- Code cleanup: unused event logic removed, lint/format (ruff), redundant
  `requirements.txt` removed.

## 0.2.0

- First public version: icon/logo, docs, license (MIT).
- No zone in the default config any more (see DOCS.md for an example).

## 0.1.5

- Sync: shairport-sync's output buffer (1.0 s, now set explicitly) is
  added back on automatically. Before, the speaker played about 1 s too early.
  **Set `latency_offset_ms` back to 0**, then fine-tune.
- Volume: new option `volume_range_db` (default 30 instead of 60 dB). Mid
  slider is noticeably louder now. Optional `ignore_airplay_volume`.

## 0.1.4

- Fix for stutter/dropouts: audio is timestamped on arrival (own reader
  task), no longer when it is sent.
- Jitter of single chunks no longer triggers a resync. Only real jumps
  (> 250 ms) or a lasting offset (smoothed > 60 ms) do.
- Audio left over from the takeover that is already late is dropped instead
  of being played on top of other audio.
- Up to 100 ms per commit (less load).

## 0.1.3

- Fix: the readiness check for the Echo failed after a few milliseconds instead of 10 s.
- Status line every 10 s while streaming (chunks, resyncs).
- shairport-sync: deprecated `-u` removed, `session_timeout` 60.

## 0.1.2

- Sendspin events are matched by class name (in 11.0.0 they are not in `server.events`).
- Readiness check through `client.is_connected`.

## 0.1.1

- aiosendspin pinned to 11.0.0 (5.1.0 is too old and has no `aiosendspin.audio`).

## 0.1.0

- First proof of concept: one shairport-sync instance (AirPlay 2) per zone,
  audio is pushed to the Sendspin player through its own Sendspin server
  (aiosendspin).
- Own IP through macvlan in netns `ap2`, so it does not clash with Music
  Assistant on UDP 319/320.
- Pairing via the PSK pairing token (option or HA entity).
