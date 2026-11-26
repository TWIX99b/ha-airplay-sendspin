# Home Assistant App: AirPlay 2 → Sendspin Bridge

![logo](airplay2_sendspin_bridge/logo.png)

Turns Sendspin speakers, e.g. Amazon Echos running
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist), into
**real AirPlay 2 receivers**. iOS and macOS can group them with HomePods,
Sonos and other AirPlay 2 speakers, and they play in sync.

> Macht Sendspin-Lautsprecher (z. B. Echos mit echo-dot-assist) zu echten
> AirPlay-2-Empfängern, die sich in iOS mit HomePods und Sonos gruppieren
> lassen. Details: [DOCS.md](airplay2_sendspin_bridge/DOCS.md).

**Status:** experimental.

## Install

[![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FTWIX99b%2Fha-airplay-sendspin)

Or: Settings → Add-ons → Add-on Store → ⋮ → Repositories →
`https://github.com/TWIX99b/ha-airplay-sendspin`

## How it works

One [shairport-sync](https://github.com/mikebrady/shairport-sync) instance
(AirPlay 2, with [nqptp](https://github.com/mikebrady/nqptp)) per zone runs
in its own network namespace with a dedicated IP, so it doesn't clash with
Music Assistant on UDP 319/320. The bridge timestamps the PCM in the
Sendspin clock and pushes it with
[aiosendspin](https://github.com/Sendspin/aiosendspin). It takes over the
speaker only while AirPlay is playing.

## License

MIT. AirPlay is a trademark of Apple Inc. This project is not affiliated
with Apple or Amazon.
