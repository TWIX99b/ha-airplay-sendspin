# AirPlay 2 → Sendspin Bridge

[🇩🇪 Deutsch](#deutsch) · [🇬🇧 English](#english)

> 🤖 Erstellt mit **Claude** (KI von Anthropic), getestet von Niklas.
> *Created with **Claude** (Anthropic's AI), tested by Niklas.*

---

## Deutsch

Macht Sendspin-Lautsprecher, z. B. Amazon Echos mit
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist), zu
**echten AirPlay-2-Empfängern**. Auf iPhone, iPad und Mac lassen sie sich
dann im AirPlay-Menü mit HomePods, Sonos und anderen AirPlay-2-Lautsprechern
gruppieren und spielen synchron.

> ⚠️ **Experimentell.** Getestet mit Echo 2 (echo-dot-assist) zusammen mit
> einer Sonos.

### So funktioniert es

```
iPhone ──AirPlay 2──► shairport-sync (pro Zone) ──PCM──► Bridge ──Sendspin──► Lautsprecher
                         │ nqptp (PTP-Uhr)                  │ eigener Sendspin-Server
                         └─ netns „ap2“ mit eigener IP      └─ übernimmt ihn nur während AirPlay
```

- Pro Zone läuft eine shairport-sync-Instanz im AirPlay-2-Modus. Die Bridge
  liest den Ton, versieht jedes Stück mit dem Zeitpunkt, an dem es gespielt
  werden soll, und schickt es per Sendspin an den Lautsprecher.
- Startet AirPlay, übernimmt die Bridge den Lautsprecher. Music Assistant
  wird dabei abgemeldet, das sieht die Sendspin-Spezifikation so vor. Nach
  `idle_timeout_s` Sekunden ohne Ton gibt die Bridge ihn wieder frei.
- nqptp, avahi und shairport-sync laufen in einem eigenen
  Netzwerk-Namespace mit **eigener IP**. Music Assistant braucht für
  AirPlay 2 dieselben UDP-Ports 319/320, so kommen sich beide nicht in die
  Quere.

### Voraussetzungen

- Home Assistant OS bzw. Supervised, **per Kabel** am Netz. macvlan
  funktioniert nicht über WLAN.
- Eine **freie IP im Netz von Home Assistant** für `ap2_ip`, außerhalb des
  DHCP-Bereichs.
- Pro Lautsprecher den **Sendspin-Pairing-Token**. Bei echo-dot-assist ist
  das die Entität „Sendspin pairing token“. Sie ist ab Werk deaktiviert und
  muss aktiviert werden.
- iPhone, iPad und Mac müssen die Zonen per mDNS sehen. Liegen sie in einem
  anderen VLAN, muss die mDNS-Weiterleitung aktiv sein.

### Installation

1. Einstellungen → Add-ons → Add-on-Store → ⋮ → **Repositories**, dann
   `https://github.com/TWIX99b/ha-airplay-sendspin` hinzufügen.
2. „AirPlay 2 → Sendspin Bridge“ installieren. Der erste Build dauert einige
   Minuten, weil shairport-sync und nqptp kompiliert werden.
3. Konfigurieren (siehe unten) und starten.

### Konfiguration

```yaml
ap2_ip: 192.168.1.240/24        # freie IP + Präfix im HA-Netz
ap2_gateway: 192.168.1.1
ap2_interface: ""               # leer = Schnittstelle der Standardroute
latency_ms: 500                 # Puffer, mindestens 150
sendspin_port: 8937             # nicht 8927 (Music Assistant)
idle_timeout_s: 4
volume_range_db: 30
ignore_airplay_volume: false
log_level: info
zones:
  - name: Wohnzimmer Echo
    host: 192.168.1.50
    port: 28928                 # echo-dot-assist
    path: /sendspin
    token_entity: sensor.wohnzimmer_echo_sendspin_pairing_token
    latency_offset_ms: -120
```

| Option | Bedeutung |
|---|---|
| `ap2_ip` | Eigene IP mit Präfix für die AirPlay-Zonen. Leer = Host-IP, dann kann es Konflikte mit Music Assistant geben. |
| `latency_ms` | So früh gibt shairport-sync den Ton heraus, so spät spielt ihn der Lautsprecher. Höher = robuster gegen WLAN-Aussetzer. |
| `volume_range_db` | Regelbereich der AirPlay-Lautstärke. 30 entspricht ungefähr HomePod und Sonos. Kleiner = in der Reglermitte lauter. |
| `ignore_airplay_volume` | `true` = immer volle Lautstärke, geregelt wird nur am Lautsprecher. |
| `zones[].pairing_token` / `token_entity` | Token direkt eintragen **oder** aus einer HA-Entität lesen. |
| `zones[].latency_offset_ms` | Feinabgleich pro Zone: + = später, − = früher. |

### Abgleich

Spiel den Lautsprecher zusammen mit einem HomePod oder einer Sonos ab,
z. B. ein Lied mit klarem Klatschen oder Schlagzeug wie „We Will Rock You“,
und stell dich in die Mitte. Dann `latency_offset_ms` in Schritten von
20–50 ms verstellen, bis nichts mehr doppelt klingt. Beim Echo 2 mit Sonos
passten **−120 ms**.

### Hinweise

- Das Symbol im AirPlay-Menü bestimmt iOS selbst. Die Zonen erscheinen als
  normaler Lautsprecher.
- Pausiert AirPlay, bleibt der Lautsprecher noch `idle_timeout_s` Sekunden
  belegt.
- Beim Dazuschalten oder Wegnehmen von Lautsprechern baut iOS die Sitzung
  neu auf. Ein kurzer Aussetzer ist dann normal.
- Die Sicherheitsbewertung in HA ist niedrig, weil das Add-on das
  Host-Netzwerk, `NET_ADMIN`/`SYS_ADMIN` und kein AppArmor braucht. Das ist
  für die eigene IP per macvlan nötig.

---

## English

Turns Sendspin speakers, e.g. Amazon Echos running
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist), into
**real AirPlay 2 receivers**. iPhone, iPad and Mac can group them with
HomePods, Sonos and other AirPlay 2 speakers in the AirPlay picker, and they
play in sync.

> ⚠️ **Experimental.** Tested with an Echo 2 (echo-dot-assist) together with
> a Sonos.

### How it works

```
iPhone ──AirPlay 2──► shairport-sync (per zone) ──PCM──► bridge ──Sendspin──► speaker
                         │ nqptp (PTP clock)                │ own Sendspin server
                         └─ netns "ap2" with own IP         └─ takes it over only during AirPlay
```

- One shairport-sync instance runs in AirPlay 2 mode per zone. The bridge
  reads the audio, stamps each chunk with the time it should play, and sends
  it to the speaker over Sendspin.
- When AirPlay starts, the bridge takes the speaker over. Music Assistant is
  disconnected, as the Sendspin spec intends. After `idle_timeout_s` seconds
  without audio the bridge releases the speaker again.
- nqptp, avahi and shairport-sync run in their own network namespace with a
  **dedicated IP**. Music Assistant needs the same UDP ports 319/320 for
  AirPlay 2, so the two don't get in each other's way.

### Requirements

- Home Assistant OS or Supervised, **wired** network. macvlan doesn't work
  over Wi-Fi.
- A **free IP in Home Assistant's network** for `ap2_ip`, outside the DHCP
  range.
- A **Sendspin pairing token** for each speaker. With echo-dot-assist this is
  the "Sendspin pairing token" entity. It is disabled by default and must be
  enabled.
- iPhone, iPad and Mac must see the zones via mDNS. If they are on another
  VLAN, mDNS forwarding has to be on.

### Installation

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**, then add
   `https://github.com/TWIX99b/ha-airplay-sendspin`.
2. Install "AirPlay 2 → Sendspin Bridge". The first build takes a few
   minutes because shairport-sync and nqptp are compiled.
3. Configure (see the example above) and start it.

### Options

| Option | Meaning |
|---|---|
| `ap2_ip` | Dedicated IP with prefix for the AirPlay zones. Empty = host IP, which can clash with Music Assistant. |
| `latency_ms` | How early shairport-sync releases audio and how much later the speaker plays it. Higher = more robust against Wi-Fi dropouts. |
| `volume_range_db` | AirPlay volume range. 30 roughly matches HomePod and Sonos. Smaller = louder mid-slider. |
| `ignore_airplay_volume` | `true` = always full volume, loudness is set on the speaker only. |
| `zones[].pairing_token` / `token_entity` | Enter the token directly **or** read it from an HA entity. |
| `zones[].latency_offset_ms` | Per-zone fine tuning: + = later, − = earlier. |

### Sync tuning

Play the speaker together with a HomePod or Sonos, e.g. a track with clear
claps or drums like "We Will Rock You", and stand in between. Adjust
`latency_offset_ms` in 20–50 ms steps until nothing sounds doubled. An Echo 2
with a Sonos needed **−120 ms**.

### Notes

- iOS picks the icon in the AirPlay picker. The zones show up as a regular
  speaker.
- When AirPlay pauses, the speaker stays taken for `idle_timeout_s` seconds.
- iOS rebuilds the session when speakers are added or removed from a group.
  A short dropout is normal then.
- HA's security rating is low because the add-on needs host networking,
  `NET_ADMIN`/`SYS_ADMIN` and no AppArmor. That is required for the
  dedicated IP via macvlan.

---

## Credits

[shairport-sync](https://github.com/mikebrady/shairport-sync) and
[nqptp](https://github.com/mikebrady/nqptp) by Mike Brady ·
[aiosendspin](https://github.com/Sendspin/aiosendspin) ·
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist) ·
network namespace idea from [iVolt1/hassio-apps](https://github.com/iVolt1/hassio-apps)

AirPlay is a trademark of Apple Inc. This project is not affiliated with
Apple or Amazon. / AirPlay ist eine Marke von Apple Inc. Dieses Projekt
steht in keiner Verbindung zu Apple oder Amazon.
