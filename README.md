# Home Assistant App: AirPlay 2 → Sendspin Bridge

![logo](airplay2_sendspin_bridge/logo.png)

[![Test & build](https://github.com/TWIX99b/ha-airplay-sendspin/actions/workflows/build.yaml/badge.svg)](https://github.com/TWIX99b/ha-airplay-sendspin/actions/workflows/build.yaml)
![Version](https://img.shields.io/badge/version-0.7.0-blue)
![Arch](https://img.shields.io/badge/arch-amd64%20%7C%20aarch64-lightgrey)

[🇩🇪 Deutsch](#deutsch) · [🇬🇧 English](#english)

> 🤖 **Hinweis / Note:** Dieses Projekt wurde vollständig mit **Claude**
> (KI von Anthropic) erstellt: Code, Dockerfile, Doku, Logo. Ich (Niklas)
> teste es in meinem eigenen Setup und gebe Rückmeldungen.
> *This project was created entirely with **Claude** (Anthropic's AI):
> code, Dockerfile, docs, logo. I (Niklas) test it on my own setup and
> provide feedback.*

---

## Deutsch

Macht Sendspin-Lautsprecher, z. B. Amazon Echos mit
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist), zu
**echten AirPlay-2-Empfängern**. Auf iPhone, iPad und Mac lassen sie sich
dann im AirPlay-Menü mit HomePods, Sonos und anderen AirPlay-2-Lautsprechern
gruppieren und spielen synchron.

**Status:** experimentell. Getestet mit Echo 2 zusammen mit Sonos Roam 2 und
HomePod mini.

### Funktionen

- **AirPlay 2 für Echos:** jeder Lautsprecher erscheint auf iPhone, iPad und
  Mac, gruppierbar mit HomePod, Sonos & Co., synchron.
- **Eigene Seite in HA** („AirPlay Bridge“): Lautsprecher als Kacheln mit
  Status, Titel und Cover; **Lautsprecher aus HA vorschlagen lassen**,
  bearbeiten, entfernen.
- **Einmessen per Mikrofon:** Zeitversatz (±1–2 ms) und Lautstärke gegenüber
  HomePod/Sonos, direkt im Browser.
- **Lautstärke:** Echo spielt bei AirPlay auf eigener Lautstärke (pro
  Lautsprecher), **Nachtmodus** mit Zeitfenster; bei Sprachbefehlen kommt die
  Antwort in normaler Lautstärke.
- **Titel und Cover** auf Echos mit Bildschirm (z. B. Echo Show 8).
- **Stereo-Paar:** zwei Echos als ein AirPlay-Lautsprecher, links/rechts.
- **Test-Ton** und **Verbindungsqualität** pro Lautsprecher.
- **Echtes Modell** in AirPlay („Echo 2“ von Amazon statt „ShairportSync“).
- **HA-Entitäten über MQTT:** AirPlay aktiv, Titel, Feinabgleich,
  Lautstärke-Anpassung, Aussetzer – auch mit externem Broker.
- **Fertige Images** für amd64 und aarch64: Updates in Sekunden.

### Installation

[![Repository hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FTWIX99b%2Fha-airplay-sendspin)

Oder: Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories →
`https://github.com/TWIX99b/ha-airplay-sendspin`

Alles Weitere – Konfiguration, Einmessen, Messwerte-Tabelle – in der
[DOCS.md](airplay2_sendspin_bridge/DOCS.md), Änderungen im
[CHANGELOG](airplay2_sendspin_bridge/CHANGELOG.md).

### So funktioniert es

Pro Lautsprecher läuft eine
[shairport-sync](https://github.com/mikebrady/shairport-sync)-Instanz
(AirPlay 2, mit [nqptp](https://github.com/mikebrady/nqptp)). Alle laufen in
einem eigenen Netzwerk-Namespace mit eigener IP, damit sie sich nicht mit
Music Assistant um die UDP-Ports 319/320 streiten. Die Bridge versieht jedes
Tonstück mit einem Zeitstempel in der Sendspin-Uhr und schickt es per
[aiosendspin](https://github.com/Sendspin/aiosendspin) an den Lautsprecher.
Den Lautsprecher übernimmt sie nur, solange AirPlay spielt. Danach steht er
wieder Music Assistant zur Verfügung.

### Voraussetzungen

- Home Assistant OS bzw. Supervised, **per Kabel** angeschlossen
- eine freie IP im HA-Netz
- Sendspin-Pairing-Token pro Lautsprecher (echo-dot-assist: Entität
  „Sendspin pairing token“ aktivieren)
- fürs Einmessen: HA über **HTTPS** öffnen (Mikrofon im Browser)
- optional: MQTT-Broker (Mosquitto-Add-on oder extern) für die Entitäten

---

## English

Turns Sendspin speakers, e.g. Amazon Echos running
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist), into
**real AirPlay 2 receivers**. iOS and macOS can group them with HomePods,
Sonos and other AirPlay 2 speakers, and they play in sync.

**Status:** experimental. Tested with Echo 2 together with Sonos Roam 2 and
HomePod mini.

### Features

- **AirPlay 2 for Echos:** each speaker shows up on iPhone, iPad and Mac,
  groupable with HomePod, Sonos & co., in sync.
- **Own page in HA** ("AirPlay Bridge"): speakers as tiles with status, track
  and cover; **speakers suggested from HA**, edit, remove.
- **Microphone calibration:** timing offset (±1–2 ms) and volume against a
  HomePod/Sonos, right in the browser.
- **Volume:** the Echo plays at its own AirPlay volume (per speaker),
  **night mode** with a time window; voice replies come at normal volume.
- **Title and cover** on Echos with a screen (e.g. Echo Show 8).
- **Stereo pair:** two Echos as one AirPlay speaker, left/right.
- **Test tone** and **connection quality** per speaker.
- **Real model** in AirPlay ("Echo 2" by Amazon instead of "ShairportSync").
- **HA entities via MQTT:** AirPlay active, title, sync offset, volume trim,
  dropouts – also with an external broker.
- **Prebuilt images** for amd64 and aarch64: updates in seconds.

### Install

[![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FTWIX99b%2Fha-airplay-sendspin)

Or: Settings → Add-ons → Add-on Store → ⋮ → Repositories →
`https://github.com/TWIX99b/ha-airplay-sendspin`

Everything else – configuration, calibration, measured values – in
[DOCS.md](airplay2_sendspin_bridge/DOCS.md), changes in the
[CHANGELOG](airplay2_sendspin_bridge/CHANGELOG.md).

### How it works

One [shairport-sync](https://github.com/mikebrady/shairport-sync) instance
(AirPlay 2, with [nqptp](https://github.com/mikebrady/nqptp)) runs per speaker.
All of them run in their own network namespace with a dedicated IP, so they
don't clash with Music Assistant on UDP 319/320. The bridge timestamps the
PCM in the Sendspin clock and pushes it with
[aiosendspin](https://github.com/Sendspin/aiosendspin). It takes over the
speaker only while AirPlay is playing and hands it back to Music Assistant
afterwards.

### Requirements

- Home Assistant OS or Supervised, **wired** network connection
- a free IP in HA's network
- a Sendspin pairing token for each speaker (echo-dot-assist: enable the
  entity "Sendspin pairing token")
- for calibration: open HA via **HTTPS** (microphone in the browser)
- optional: an MQTT broker (Mosquitto add-on or external) for the entities

---

## Credits

[shairport-sync](https://github.com/mikebrady/shairport-sync) and
[nqptp](https://github.com/mikebrady/nqptp) by Mike Brady ·
[aiosendspin](https://github.com/Sendspin/aiosendspin) ·
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist) ·
network namespace idea from [iVolt1/hassio-apps](https://github.com/iVolt1/hassio-apps)

## License / Lizenz

MIT. AirPlay is a trademark of Apple Inc. This project is not affiliated
with Apple or Amazon. / AirPlay ist eine Marke von Apple Inc. Dieses Projekt
steht in keiner Verbindung zu Apple oder Amazon.
