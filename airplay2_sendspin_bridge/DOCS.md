# AirPlay 2 → Sendspin Bridge

Macht Sendspin-Lautsprecher, z. B. Amazon Echos mit
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist), zu
**echten AirPlay-2-Empfängern**. Auf iPhone, iPad und Mac lassen sie sich
dann im AirPlay-Menü mit HomePods, Sonos und anderen AirPlay-2-Lautsprechern
gruppieren und spielen synchron.

> ⚠️ **Experimentell.** Getestet mit Echo 2 (echo-dot-assist) zusammen mit
> einer Sonos.

## So funktioniert es

```
iPhone ──AirPlay 2──► shairport-sync (pro Zone) ──PCM──► Bridge ──Sendspin──► Echo
                         │ nqptp (PTP-Uhr)                  │ eigener Sendspin-Server
                         └─ netns „ap2“ mit eigener IP      └─ übernimmt den Echo nur während AirPlay
```

- Pro Zone läuft eine shairport-sync-Instanz im AirPlay-2-Modus. Die Bridge
  liest den Ton, versieht jedes Stück mit dem Zeitpunkt, an dem es gespielt
  werden soll, und schickt es per Sendspin an den Lautsprecher.
- Startet eine AirPlay-Sitzung, übernimmt die Bridge den Echo. Music
  Assistant wird dabei vom Echo abgemeldet, das sieht die Sendspin-Spezifikation
  so vor. Nach `idle_timeout_s` Sekunden ohne Ton gibt die Bridge den Echo
  wieder frei, und Music Assistant kann ihn wieder nutzen.
- nqptp, avahi und shairport-sync laufen in einem eigenen Netzwerk-Namespace
  mit **eigener IP**. Music Assistant braucht für AirPlay 2 dieselben
  UDP-Ports 319/320, so kommen sich beide nicht in die Quere.

## Voraussetzungen

- Home Assistant OS bzw. Supervised, **per Kabel** am Netz. macvlan
  funktioniert nicht über WLAN.
- Eine **freie IP im Netz von Home Assistant** für `ap2_ip`, außerhalb des
  DHCP-Bereichs.
- Pro Lautsprecher den **Sendspin-Pairing-Token**. Bei echo-dot-assist ist
  das die Entität „Sendspin pairing token“. Sie ist ab Werk deaktiviert und
  muss aktiviert werden.
- iPhone, iPad und Mac müssen die AirPlay-Zonen per mDNS sehen. Liegen sie in
  einem anderen VLAN, muss die mDNS-Weiterleitung aktiv sein.

## Installation

1. Einstellungen → Add-ons → Add-on-Store → ⋮ → **Repositories**, dann
   `https://github.com/TWIX99b/ha-airplay-sendspin` hinzufügen.
2. „AirPlay 2 → Sendspin Bridge“ installieren. Der erste Build dauert einige
   Minuten, weil shairport-sync und nqptp kompiliert werden.
3. Konfigurieren (siehe unten) und starten.

## Konfiguration

```yaml
ap2_ip: 192.168.10.101/23       # freie IP + Präfix im HA-Netz
ap2_gateway: 192.168.10.1
ap2_interface: ""               # leer = Schnittstelle der Standardroute
latency_ms: 500                 # Puffer, nicht unter 150
sendspin_port: 8937             # nicht 8927 (Music Assistant)
idle_timeout_s: 4
volume_range_db: 30
ignore_airplay_volume: false
log_level: info
zones:
  - name: Stube Echo
    host: 192.168.30.103
    port: 28928                 # echo-dot-assist
    path: /sendspin
    token_entity: sensor.og_stube_satellit_sendspin_pairing_token
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
z. B. ein Lied mit klarem Klatschen oder Schlagzeug, und stell dich in die
Mitte. Dann `latency_offset_ms` in Schritten von 20–50 ms verstellen, bis
nichts mehr doppelt klingt. Beim Echo 2 mit Sonos passten **−120 ms**.

## Hinweise

- Das Symbol im AirPlay-Menü bestimmt iOS selbst. Die Zonen erscheinen als
  normaler Lautsprecher.
- Pausiert AirPlay, bleibt der Echo noch `idle_timeout_s` Sekunden belegt.
- Beim Dazuschalten oder Wegnehmen von Lautsprechern baut iOS die Sitzung
  neu auf. Ein kurzer Aussetzer ist dann normal.

## Danke

[shairport-sync](https://github.com/mikebrady/shairport-sync) und
[nqptp](https://github.com/mikebrady/nqptp) von Mike Brady,
[aiosendspin](https://github.com/Sendspin/aiosendspin),
[echo-dot-assist](https://github.com/Gamer92000/echo-dot-assist). Die Idee
mit dem eigenen Netzwerk-Namespace stammt aus
[iVolt1/hassio-apps](https://github.com/iVolt1/hassio-apps).

AirPlay ist eine Marke von Apple Inc. Dieses Projekt steht in keiner
Verbindung zu Apple oder Amazon.
