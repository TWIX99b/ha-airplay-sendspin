# Home Assistant App: AirPlay 2 → Sendspin Bridge

![logo](airplay2_sendspin_bridge/logo.png)

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

**Status:** experimentell. Getestet mit Echo 2 und Sonos.

### Installation

[![Repository hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FTWIX99b%2Fha-airplay-sendspin)

Oder: Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories →
`https://github.com/TWIX99b/ha-airplay-sendspin`

Konfiguration, **automatische Einmessung per Mikrofon** und Messwerte:
siehe [DOCS.md](airplay2_sendspin_bridge/DOCS.md).

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
  „Sendspin pairing token“)

---

## English

Turns Sendspin speakers, e.g. Amazon Echos running
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist), into
**real AirPlay 2 receivers**. iOS and macOS can group them with HomePods,
Sonos and other AirPlay 2 speakers, and they play in sync.

**Status:** experimental. Tested with Echo 2 and Sonos.

### Install

[![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FTWIX99b%2Fha-airplay-sendspin)

Or: Settings → Add-ons → Add-on Store → ⋮ → Repositories →
`https://github.com/TWIX99b/ha-airplay-sendspin`

Configuration, **automatic microphone calibration** and measured values:
see [DOCS.md](airplay2_sendspin_bridge/DOCS.md).

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
- a Sendspin pairing token for each speaker (echo-dot-assist: entity
  "Sendspin pairing token")

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
