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
iPhone ──AirPlay 2──► shairport-sync (pro Lautsprecher) ──PCM──► Bridge ──Sendspin──► Lautsprecher
                         │ nqptp (PTP-Uhr)                  │ eigener Sendspin-Server
                         └─ netns „ap2“ mit eigener IP      └─ übernimmt ihn nur während AirPlay
```

- Pro Lautsprecher läuft eine shairport-sync-Instanz im AirPlay-2-Modus. Die Bridge
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
- iPhone, iPad und Mac müssen die Lautsprecher per mDNS sehen. Liegen sie in einem
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
speaker_volume: 100             # Lautsprecher bei AirPlay auf 100 %
log_level: info
zones:
  - name: Wohnzimmer Echo
    host: 192.168.1.50
    port: 28928                 # echo-dot-assist
    path: /sendspin
    token_entity: sensor.wohnzimmer_echo_sendspin_pairing_token
    latency_offset_ms: -120
    volume_trim_db: 0
```

| Option | Bedeutung |
|---|---|
| `ap2_ip` | Eigene IP mit Präfix für die AirPlay-Lautsprecher. Leer = Host-IP, dann kann es Konflikte mit Music Assistant geben. |
| `latency_ms` | So früh gibt shairport-sync den Ton heraus, so spät spielt ihn der Lautsprecher. Höher = robuster gegen WLAN-Aussetzer. |
| `volume_range_db` | Regelbereich der AirPlay-Lautstärke. 30 entspricht ungefähr HomePod und Sonos. Kleiner = in der Reglermitte lauter. |
| `ignore_airplay_volume` | `true` = immer volle Lautstärke, geregelt wird nur am Lautsprecher. Dann `speaker_volume` herunterstellen. |
| `speaker_volume` | Lautstärke des Lautsprechers selbst, solange AirPlay spielt (Standard 100 %). Danach kommt der alte Wert zurück, außer er wurde zwischendurch am Lautsprecher verstellt. `0` = nicht anfassen. |
| `zones[].pairing_token` / `token_entity` | Token direkt eintragen **oder** aus einer HA-Entität lesen. |
| `zones[].latency_offset_ms` | Feinabgleich pro Lautsprecher: + = später, − = früher. |
| `zones[].volume_trim_db` | Lautstärke-Anpassung pro Lautsprecher, 0 bis −30 dB. Wird per Einmessung gesetzt. |

### Automatisch einmessen

Das Add-on hat eine eigene Seite **„AirPlay Sync“** in der HA-Seitenleiste
bzw. unter „Web-UI öffnen“. Dort misst es per Mikrofon den Versatz eines
Lautsprechers gegenüber einem HomePod oder einer Sonos und stellt ihn automatisch ein.

1. Den **Klick-Track** (`sync-click.wav`) auf der Seite herunterladen.
2. Ihn per AirPlay auf die Gruppe aus **Lautsprecher + Referenz (HomePod/Sonos)**
   abspielen, z. B. mit der Musik-App am Mac oder iPhone.
3. Das Gerät mit der Seite **ungefähr gleich weit von beiden** Lautsprechern
   hinlegen, beide etwa gleich laut. Dann auf **Messen** tippen. Das dauert
   etwa 20 Sekunden.
4. Die Seite schaltet den Lautsprecher erst stumm (nur die Referenz klickt), dann
   wieder an (beide klicken), und berechnet daraus den Versatz auf etwa 1–2 ms
   genau.
5. **Übernehmen & speichern**: Der neue Wert wirkt sofort und wird in den
   Add-on-Optionen gespeichert. Zur Kontrolle einfach nochmal messen.

Hinweise:
- Der Browser gibt das Mikrofon nur über **HTTPS** frei, also z. B. über die
  externe HA-Adresse. Klappt es nicht, die Seite über „In neuem Tab öffnen“
  aufrufen.
- Jeder Meter Abstandsunterschied zum Mikrofon verfälscht um etwa 3 ms.
  Deshalb das Gerät in die Mitte legen.
- Von Hand geht es auch: `latency_offset_ms` in Schritten von 20–50 ms
  verstellen, bis Klatschen oder Schlagzeug nicht mehr doppelt klingt. Auf
  der Seite gibt es dafür auch ±-Knöpfe, die sofort wirken.

### Lautstärke angleichen

Auf derselben Seite gibt es darunter **„Lautstärke angleichen“**. Vorher die
Zeit einmessen.

1. Den **Rausch-Track** (`level-noise.wav`) herunterladen und per AirPlay auf
   dieselbe Gruppe abspielen, in normaler Hörlautstärke.
2. In iOS beide Lautsprecher der Gruppe auf **dieselbe Einzel-Lautstärke**
   stellen. Gerät in die Mitte legen, **Messen** (etwa 30 Sekunden).
3. Der Track rauscht 1,5 s und ist dann 3,5 s still. Während des Rauschens
   bleibt der Lautsprecher stumm, in der Stille spielt das Add-on dasselbe
   Rauschen auf dem Lautsprecher. So misst die Seite beide Lautsprecher einzeln und berechnet
   den Unterschied, simuliert auf etwa ±0,5 dB genau.
4. **Übernehmen & speichern** setzt `volume_trim_db`. Von Hand geht es mit
   den ±-dB-Knöpfen.

Lauter als 0 dB geht nicht. Ist der Lautsprecher dann immer noch leiser, zuerst
`speaker_volume` prüfen (Standard 100 %), sonst den Referenz-Lautsprecher in
iOS etwas leiser stellen. Gleich laut heißt nicht gleicher Klang: Ein Echo
hat weniger Bass als eine Sonos.

### Messwerte (Info)

Gemessene bzw. abgehörte Werte als Orientierung. Die Einmessung ersetzt sie,
denn die Lautstärke hängt auch von Raum und Referenz ab. Weitere Werte gern per Issue melden.

| Lautsprecher | Software | Referenz | `latency_offset_ms` | `volume_trim_db` | Methode | Datum |
|---|---|---|---|---|---|---|
| Echo 2 (radar) | echo-dot-assist 2026.11.18 | Sonos | **−120** | *noch offen* | Gehör | 26.11.2026 |
| Echo Dot 2 (biscuit) | echo-dot-assist | – | *noch offen* | *noch offen* | – | – |
| Echo Dot 3 (donut) | echo-dot-assist | – | *noch offen* | *noch offen* | – | – |
| Echo Show 8 (crown) | echo-dot-assist | – | *noch offen* | *noch offen* | – | – |

### Hinweise

- Das Symbol im AirPlay-Menü bestimmt iOS selbst. Die Lautsprecher erscheinen als
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
iPhone ──AirPlay 2──► shairport-sync (per speaker) ──PCM──► bridge ──Sendspin──► speaker
                         │ nqptp (PTP clock)                │ own Sendspin server
                         └─ netns "ap2" with own IP         └─ takes it over only during AirPlay
```

- One shairport-sync instance runs in AirPlay 2 mode per speaker. The bridge
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
- iPhone, iPad and Mac must see the speakers via mDNS. If they are on another
  VLAN, mDNS forwarding has to be on.

### Installation

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**, then add
   `https://github.com/TWIX99b/ha-airplay-sendspin`.
2. Install "AirPlay 2 → Sendspin Bridge". The first build takes a few
   minutes because shairport-sync and nqptp are compiled.
3. Configure (see the example in the German section) and start it.

### Options

| Option | Meaning |
|---|---|
| `ap2_ip` | Dedicated IP with prefix for the AirPlay speakers. Empty = host IP, which can clash with Music Assistant. |
| `latency_ms` | How early shairport-sync releases audio and how much later the speaker plays it. Higher = more robust against Wi-Fi dropouts. |
| `volume_range_db` | AirPlay volume range. 30 roughly matches HomePod and Sonos. Smaller = louder mid-slider. |
| `ignore_airplay_volume` | `true` = always full volume, loudness is set on the speaker only. Lower `speaker_volume` then. |
| `speaker_volume` | Volume of the speaker itself while AirPlay plays (default 100 %). The old value is restored afterwards, unless it was changed on the speaker meanwhile. `0` = don't touch. |
| `zones[].pairing_token` / `token_entity` | Enter the token directly **or** read it from an HA entity. |
| `zones[].latency_offset_ms` | Per-speaker fine tuning: + = later, − = earlier. |
| `zones[].volume_trim_db` | Per-speaker volume trim, 0 to −30 dB. Set by the calibration. |

### Automatic calibration

The add-on has its own page **"AirPlay Sync"** in the HA sidebar or via
"Open Web UI". It uses the microphone to measure a speaker's offset against a
HomePod or Sonos and sets it automatically.

1. Download the **click track** (`sync-click.wav`) from the page.
2. Play it via AirPlay to the group **speaker + reference (HomePod/Sonos)**, e.g. with
   the Music app on Mac or iPhone.
3. Put the device showing the page **roughly the same distance from both**
   speakers, both at about the same volume. Then tap **Measure**. It takes
   about 20 seconds.
4. The page first mutes the speaker (only the reference clicks), then unmutes it
   (both click), and computes the offset to within about 1–2 ms.
5. **Apply & save**: the new value takes effect immediately and is saved in
   the add-on options. Measure again to verify.

Notes:
- Browsers only allow the microphone over **HTTPS**, e.g. via your external
  HA address. If it doesn't work, use "Open in new tab".
- Every metre of distance difference to the microphone skews the result by
  about 3 ms, so place the device in between.
- Manual tuning also works: adjust `latency_offset_ms` in 20–50 ms steps
  until claps or drums no longer sound doubled. The page has ± buttons for
  this too, which take effect immediately.

### Volume matching

Further down on the same page is **"Match volume"**. Calibrate the timing
first.

1. Download the **noise track** (`level-noise.wav`) and play it via AirPlay to
   the same group, at normal listening volume.
2. In iOS, set both speakers of the group to the **same individual volume**.
   Put the device in between, tap **Measure** (about 30 seconds).
3. The track plays 1.5 s of noise, then 3.5 s of silence. During the noise
   the speaker stays silent; in the silence the add-on plays the same noise
   on the speaker. So the page measures both speakers separately and computes the
   difference, simulated to about ±0.5 dB.
4. **Apply & save** sets `volume_trim_db`. The ± dB buttons adjust it by hand.

Above 0 dB is not possible. If the speaker is still quieter, check
`speaker_volume` first (default 100 %), otherwise turn the reference speaker
down a little in iOS. Equally loud doesn't mean equal sound: an Echo has less
bass than a Sonos.

### Measured values (info)

Measured or by-ear values as a starting point. The calibration replaces
them, since volume also depends on the room and the reference. Feel free to report more values via an issue.

| Speaker | Software | Reference | `latency_offset_ms` | `volume_trim_db` | Method | Date |
|---|---|---|---|---|---|---|
| Echo 2 (radar) | echo-dot-assist 2026.11.18 | Sonos | **−120** | *open* | by ear | 2026-11-26 |
| Echo Dot 2 (biscuit) | echo-dot-assist | – | *open* | *open* | – | – |
| Echo Dot 3 (donut) | echo-dot-assist | – | *open* | *open* | – | – |
| Echo Show 8 (crown) | echo-dot-assist | – | *open* | *open* | – | – |

### Notes

- iOS picks the icon in the AirPlay picker. They show up as a regular
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
