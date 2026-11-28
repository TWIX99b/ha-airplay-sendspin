# Changelog

## 0.9.2

- **Test tone always audible:** if the speaker is turned down below 40 %,
  the test tone (page ♪ and HA button) plays at 40 % and the old volume is
  restored afterwards. Before, a speaker at 3 % played it inaudibly.

## 0.9.1

- **IP check at start:** before creating the AirPlay IP (`ap2_ip`) the
  add-on checks whether it belongs to the HA host or whether another device
  already answers on it (ARP duplicate address detection). If so: error in
  the log and a warning on the page.
- **Enable token entities from the page:** "Add speaker" now also lists
  Echos whose "Sendspin pairing token" entity is disabled in HA (they have
  no state, so they were invisible before) – with an **Enable** button; the
  Echo shows up ready once HA has reloaded it.
- README: one IP for all speakers made clear, version badge follows the
  latest release, screenshots (demo data).

## 0.9.0

- **Stable timing:** the bridge used to anchor every AirPlay session on the
  arrival of its very first audio chunk (part of shairport-sync's silent
  lead-in burst) and only corrected errors above 60 ms – each session ended
  up a random −30…+30 ms off, so calibration results jumped around. It now
  follows the earliest arrivals over a sliding window (shairport-sync
  releases packets 0–10 ms late, never early), ignores bursts, follows clock
  drift by up to 1 ms/s and starts over after a pause or skip. In a
  simulation all sessions agree within 1 ms. Check your offsets once.
- **Calibration page reworked:** mode **By ear** (default) with
  −20/−5/+5/+20 ms, a clear saved / not saved state, "back to the saved
  value" and the model default; mode **With microphone** for a second
  device: waits until the timing has locked, measures 3 rounds, refuses to
  suggest anything when they disagree, verifies after saving, can be
  cancelled.
- Click track: 10 minutes without a loop (a loop restart made iOS flush
  AirPlay mid-measurement).
- Page and API show the timing state (`sync_locked`, `sync_error_ms`).

## 0.8.0

- **Home Assistant integration "AirPlay Bridge" instead of MQTT**
  (`custom_components/airplay_bridge`, install via the Marketplace/HACS).
  The add-on announces itself (Supervisor discovery) – confirm once, no
  broker needed. Entities sit on the **Echo's own device**: AirPlay, title,
  cover (image), AirPlay volume, night mode (switch), test tone (button),
  offset, volume trim, dropouts. Live updates over a WebSocket.
- Add-on: token-protected API `/int/` for the integration; settings changed
  in HA are validated, saved and applied live.
- Night mode can be switched off without losing its times (`night_enabled`).
- Stable entity ids from the speaker name (umlauts transliterated).
- **Removed:** MQTT (options `mqtt*`, paho-mqtt). Delete the old "… AirPlay"
  MQTT devices in HA.
- CI: integration tested in a real Home Assistant against a fake add-on,
  hassfest, Marketplace validation, GitHub release per version.

## 0.7.0

- **Real model in AirPlay:** shairport-sync is patched at build time so each
  speaker reports its real model and manufacturer from HA (e.g. "Echo 2" by
  Amazon) instead of "ShairportSync", like a Sonos does; iOS picks the icon
  from it. Option `report_model` (default on). No Apple model is faked.
- **Test tone** per speaker (♪ on the tile): chime straight via Sendspin,
  without AirPlay; stereo pairs left, then right. API POST /api/zones/{i}/test.
- **Connection quality** on the tile (resyncs, gaps, lost speakers since
  start, per hour of music) and as MQTT sensor "Aussetzer"/"Dropouts".
- **Per-speaker AirPlay volume and night mode** (`zones[].speaker_volume`,
  `night_volume`, `night_start`, `night_end`): live, also during playback,
  in HA's time zone.
- Tests for night mode, test tone (pair order) and dropout counting.

## 0.6.0

- **New add-on page "AirPlay Bridge"** (sidebar) with two tabs:
  **Speakers** and **Calibrate**. Fresh look, light and dark mode.
- **Speaker tiles:** status (playing / ready / not paired), current track
  with cover, offset, volume, stereo pair; buttons to edit and calibrate.
- **Add speakers from Home Assistant:** the page finds every echo-dot-assist
  device (room, model, IP, token entity, voice assistant). Tap, check the
  name, save; known offsets from the docs table are offered (Echo 2: −225 ms).
- **Edit and remove** speakers, choose a stereo partner from the list.
  Offset and volume apply live; structural changes restart the add-on and
  the page reloads by itself. Pairing tokens are never sent to the page.
- The speakers stay visible in the configuration tab (`zones`).
- New API: /api/config, /api/discover, /api/zones/{i}/cover (speakers.py).

## 0.5.2

- External MQTT broker: new options `mqtt_host`, `mqtt_port`,
  `mqtt_username`, `mqtt_password`, `mqtt_tls`. Empty `mqtt_host` = the
  broker from Home Assistant (Mosquitto add-on), as before.
- MQTT disconnects are logged.

## 0.5.1

- Home Assistant now downloads the prebuilt image from GHCR instead of
  compiling shairport-sync and nqptp itself: installs and updates take
  seconds instead of minutes.

## 0.5.0

- **Voice assistant:** while the speaker's voice assistant listens or
  answers, it gets its old volume back (instead of the 100 % used for
  AirPlay), and goes up again 2 s after. The satellite is found via
  `token_entity` (or `satellite_entity`). Option `lower_volume_during_voice`.
- **Title and cover:** shairport-sync metadata (title, artist, album, cover)
  is passed to the speaker via Sendspin; Echos with a screen show it. Option
  `metadata`.
- **Stereo pair:** `partner_host` (+ `partner_port`, `partner_path`,
  `partner_pairing_token` / `partner_token_entity`): two speakers as one
  AirPlay receiver, left and right, with the same play time.
- **Home Assistant entities via MQTT:** per speaker a device with "AirPlay"
  (playing), "Titel" (track), "Feinabgleich" and "Lautstärke-Anpassung"
  (editable, live + saved). Option `mqtt`, needs a broker.
- **AppArmor:** own profile instead of `apparmor: false`.
- **CI:** GitHub Actions runs lint and all tests and builds images for
  amd64/aarch64 on GHCR.
- tests/test_features.py: stereo pair, track info, voice assistant and MQTT
  with simulated speakers.

## 0.4.5

- Tolerance: a timing result within ±5 ms (volume within ±1 dB) is shown as
  "✓ Passt" / "✓ Good", the value stays, and "Confirm & save value" saves it
  unchanged.
- The iPhone/iPad block from 0.4.4 is removed again: playing and measuring
  on the same iPhone works in practice. If playback does stop, the page still
  says so.

## 0.4.4

- iPhone/iPad: iOS ends AirPlay as soon as the microphone is on, so the page
  no longer starts the track itself there and explains how to play it from a
  second device instead.
- Step 2 wording ("Zone" → "Lautsprecher") fixed.

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
