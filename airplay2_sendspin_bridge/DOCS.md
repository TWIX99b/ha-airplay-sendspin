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
- **Eine** freie IP im Netz von Home Assistant für `ap2_ip`, außerhalb des
  DHCP-Bereichs – für alle Lautsprecher zusammen (sie unterscheiden sich nur
  im Port). Beim Start prüft das Add-on per ARP, ob die IP wirklich frei ist;
  antwortet dort schon ein Gerät, steht eine Warnung im Log und auf der Seite.
- Pro Lautsprecher den **Sendspin-Pairing-Token**. Bei echo-dot-assist ist
  das die Entität „Sendspin pairing token“. Sie ist ab Werk deaktiviert; beim
  Hinzufügen eines Lautsprechers zeigt die Seite solche Echos mit dem Knopf
  **Aktivieren** an (oder von Hand in HA aktivieren).
- iPhone, iPad und Mac müssen die Lautsprecher per mDNS sehen. Liegen sie in einem
  anderen VLAN, muss die mDNS-Weiterleitung aktiv sein.

### Installation

1. Einstellungen → Add-ons → Add-on-Store → ⋮ → **Repositories**, dann
   `https://github.com/TWIX99b/ha-airplay-sendspin` hinzufügen.
2. „AirPlay 2 → Sendspin Bridge“ installieren. Der erste Build dauert einige
   Minuten, weil shairport-sync und nqptp kompiliert werden.
3. Konfigurieren (siehe unten) und starten.

### Lautsprecher verwalten

Am einfachsten über die eigene Seite des Add-ons, **„AirPlay Bridge“** in der
HA-Seitenleiste, Reiter **Lautsprecher**:

- Jeder Lautsprecher ist eine Kachel: Status (spielt / bereit / nicht
  gekoppelt), laufender Titel mit Cover, Feinabgleich, Lautstärke.
- **Lautsprecher hinzufügen** zeigt alle Echos mit echo-dot-assist, die
  Home Assistant kennt, mit Raum, Modell und IP. Antippen, Namen prüfen,
  speichern. Für bekannte Modelle schlägt das Formular den Wert aus der
  Messwerte-Tabelle vor (z. B. Echo 2: −225 ms).
- **Bearbeiten**: Name, Echo, Stereo-Partner, Feinabgleich, Lautstärke;
  unter „Erweitert“ Port, Pfad, Pairing-Token und Sprachassistent.
- Feinabgleich und Lautstärke wirken sofort. Neue, entfernte oder geänderte
  Lautsprecher übernimmt das Add-on mit einem kurzen Neustart; die Seite
  lädt danach von selbst neu.

Die Lautsprecher stehen weiterhin auch im Tab „Konfiguration“ unter
`zones` und lassen sich dort von Hand bearbeiten.

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
    latency_offset_ms: -225
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
| `lower_volume_during_voice` | Solange der Sprachassistent des Lautsprechers zuhört oder antwortet, bekommt er seine alte Lautstärke zurück (Standard an). |
| `metadata` | Titel, Interpret, Album und Cover weitergeben (Standard an). |
| `zones[].satellite_entity` | Sprachassistent des Lautsprechers (`assist_satellite.*`). Leer = automatisch über `token_entity`. |
| `zones[].partner_host` usw. | Zweiter Lautsprecher als Stereo-Paar, siehe unten. |
| `report_model` | Meldet AirPlay das echte Modell und den Hersteller (z. B. „Echo 2“ von Amazon) statt „ShairportSync“ (Standard an). Ändert das Symbol nicht. |
| `airplay_icon` | Symbol im AirPlay-Menü: `standard` oder als Experiment `airport_express` (siehe unten). |
| `icon_model`, `icon_manufacturer` | Experiment: freie Modellkennung und Hersteller für alle Lautsprecher (gilt vor `airplay_icon`). |
| `zones[].speaker_volume` | AirPlay-Lautstärke dieses Lautsprechers in %, überschreibt `speaker_volume`. |
| `zones[].night_volume`, `night_start`, `night_end` | Nachtmodus: zwischen Start und Ende höchstens so viel % (z. B. 40, 22:00, 07:00). |

### Zeit einmessen

Das Add-on hat eine eigene Seite **„AirPlay Bridge“** (Reiter **Einmessen**)
in der HA-Seitenleiste bzw. unter „Web-UI öffnen“. Dort stellst du ein, wie
viele Millisekunden ein Echo früher oder später spielt, damit er mit einem
HomePod oder einer Sonos zusammenpasst.

1. **Lautsprecher wählen.** Die Seite zeigt, ob er gerade spielt und ob der
   Takt eingerastet ist.
2. **Klick-Track abspielen** (ein Klick pro Sekunde, 10 Minuten, ohne
   Schleife) per AirPlay auf die Gruppe aus **Echo + Referenz
   (HomePod/Sonos)**: iPhone/iPad über Kontrollzentrum → AirPlay, Mac über
   Menüleiste › Ton. Oder `sync-click.wav` herunterladen und in der
   Musik-App abspielen.
3. **Angleichen**, auf eine von zwei Arten:

**Nach Gehör (Standard):** Etwa gleich weit von beiden Lautsprechern
hinstellen. Passt alles, ist nur ein Klick zu hören; „klick-klick“ heißt,
der Echo ist daneben. Mit **−20/−5/+5/+20 ms** angleichen (minus = Echo
früher, plus = später, nach 2–3 Sekunden hörbar). Die Seite zeigt, ob der
aktuelle Wert gespeichert ist; **Speichern** bestätigt es ausdrücklich.
„Zurück zum gespeicherten Wert“ verwirft Versuche, für bekannte Modelle gibt
es den Standardwert per Knopf.

**Mit Mikrofon:** Den Track auf einem Gerät abspielen (z. B. iPhone), die
Seite auf einem **zweiten Gerät** öffnen (iPad, Mac) und mittig hinlegen.
Das Messgerät spielt selbst nichts ab, so kann das Mikrofon die Wiedergabe
nicht stören. **Messen** wartet, bis der Takt eingerastet ist, und misst dann
dreimal (je Echo stumm / Echo an, ca. 1 Minute). Passen die Runden nicht
zusammen (mehr als 8 ms auseinander) oder ist das Signal zu schwach, schlägt
die Seite nichts vor. Sonst: **Übernehmen, speichern & prüfen** – danach
läuft automatisch eine Prüfmessung.

Hinweise:
- Seit 0.9 rastet die Bridge in jeder AirPlay-Sitzung auf denselben Takt ein
  (auf etwa ±1 ms). Vorher landete jede Sitzung zufällig ein paar zehn
  Millisekunden daneben – Einmessen war deshalb kaum reproduzierbar. **Nach
  dem Update die Werte einmal neu prüfen.**
- Der Browser gibt das Mikrofon nur über **HTTPS** frei (z. B. externe
  HA-Adresse). Für „Nach Gehör“ ist das egal.
- Jeder Meter Abstandsunterschied macht etwa 3 ms aus.
- Bricht die Wiedergabe während der Messung ab oder startet neu, meldet die
  Seite das, statt einen falschen Wert vorzuschlagen.

### Lautstärke angleichen

Auf derselben Seite gibt es darunter **„Lautstärke angleichen“**. Vorher die
Zeit einmessen.

1. Der **Rausch-Track** (`level-noise.wav`) läuft beim Messen automatisch
   über die Seite (oder herunterladen und selbst abspielen), per AirPlay auf
   dieselbe Gruppe, in normaler Hörlautstärke.
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

### Sprachassistent, Titel, Stereo-Paar, HA-Entitäten

**Sprachassistent:** Bei AirPlay steht der Echo auf `speaker_volume`
(100 %). Hört F.R.I.D.A.Y. zu oder antwortet, stellt das Add-on ihn auf seine
alte Lautstärke zurück, damit die Antwort nicht in AirPlay-Lautstärke kommt.
Zwei Sekunden nach dem Ende geht es wieder hoch. Die Musik senkt der Echo
dabei selbst ab (echo-dot-assist, Einstellung „Music ducking“). Den
Satelliten findet das Add-on über `token_entity`.

**Titel und Cover:** Titel, Interpret, Album und Cover aus AirPlay gehen an
den Lautsprecher. Echos mit Bildschirm (z. B. Show 8) zeigen sie an.

**Stereo-Paar:** Zwei Lautsprecher als ein AirPlay-Lautsprecher, links und
rechts. `host` spielt links, `partner_host` rechts:

```yaml
zones:
  - name: Stube
    host: 192.168.1.50
    port: 28928
    token_entity: sensor.stube_links_sendspin_pairing_token
    partner_host: 192.168.1.51
    partner_token_entity: sensor.stube_rechts_sendspin_pairing_token
    latency_offset_ms: -225
```

Beide bekommen dieselbe Spielzeit, der Feinabgleich gilt für beide. Fällt
einer aus, spielt der andere weiter.

**Entitäten in HA:** über die Integration „AirPlay Bridge“, siehe unten.

### Integration „AirPlay Bridge“ (Entitäten in HA)

Seit 0.8 gibt es statt MQTT eine eigene Integration. Sie hängt die
AirPlay-Entitäten **direkt an das Gerät des Echos** (z. B. „Wohnzimmer
Satellit“), gleich neben Sprachassistent und Media Player:

| Entität | Was |
|---|---|
| AirPlay | an, solange über AirPlay gespielt wird |
| AirPlay-Titel | Titel, Interpret und Album als Attribute |
| AirPlay-Cover | Cover des laufenden Titels (Bild) |
| AirPlay-Lautstärke (Tag) | Lautstärke des Echos bei AirPlay (%), sofort wirksam |
| AirPlay-Lautstärke jetzt | Was gerade gilt: Tag- oder Nachtwert (%) |
| AirPlay-Nachtmodus | Nachtmodus an/aus (Zeiten und Wert auf der Add-on-Seite) |
| AirPlay-Test-Ton | Knopf: kurzer Ton auf dem Echo |
| AirPlay-Versatz, AirPlay-Lautstärke-Anpassung | Einstellungen, sofort wirksam und gespeichert |
| AirPlay-Aussetzer | Diagnose: Aussetzer seit dem Start des Add-ons |

**Einrichten:**
1. Einstellungen → **Marktplatz** (bis HA 2026.10: HACS) → ⋮ → Eigene
   Repositories → `https://github.com/TWIX99b/ha-airplay-sendspin`, Typ
   **Integration** → „AirPlay Bridge“ installieren, HA neu starten.
2. Das Add-on meldet sich selbst: Unter Einstellungen → Geräte & Dienste
   erscheint **„AirPlay Bridge gefunden“** → Konfigurieren → Absenden.
3. Von Hand (falls nichts erscheint): Integration hinzufügen → „AirPlay
   Bridge“ → Adresse `127.0.0.1`, Port `8099`, Token von der Add-on-Seite
   (ganz unten, „Home Assistant – Integration von Hand einrichten“).

Die Werte kommen live: Das Add-on meldet jede Änderung sofort, ohne
Abfrage-Intervall. Ein MQTT-Broker ist nicht mehr nötig.

**Umstieg von 0.7 (MQTT):** Die alten Geräte „… AirPlay“ unter Einstellungen
→ MQTT → Geräte löschen; Automationen auf die neuen Entitäten umstellen.

### Symbol, Test-Ton, Verbindung, Nachtmodus

**Symbol im AirPlay-Menü:** iOS zeigt nur für Modellkennungen, die es kennt,
ein eigenes Symbol (Apple-Geräte, Sonos …); für alle anderen den Lautsprecher
mit Wellen. Echos kennt iOS nicht – das gemeldete echte Modell („Echo 2“ von
Amazon, Option `report_model`) ändert am Symbol also nichts, auch nicht die
Home-App (getestet). Als **Experiment** meldet die Option `airplay_icon:
airport_express` stattdessen die Kennung einer AirPort Express. Das betrifft
nur das Symbol; iOS zeigt nach dem Verbinden teils wieder das Standardsymbol.

**Test-Ton:** Der ♪-Knopf auf der Kachel spielt direkt über das Add-on einen
kurzen Ton auf dem Echo, ohne iPhone und AirPlay – zum Prüfen von Kopplung und
IP. Ein Stereo-Paar spielt ihn erst links, dann rechts. Ist der Echo leiser
als 40 % gestellt, kommt der Ton mit 40 %; danach gilt wieder die alte
Lautstärke.

**Verbindung:** Die Kachel zeigt, wie oft es seit dem Start des Add-ons
gehakt hat (Nachsynchronisierung, Lücke, verlorener Lautsprecher) und wie
viel Musik lief: gut unter 1 Aussetzer pro Stunde, mäßig unter 4, sonst
schlecht. Als Sensor „AirPlay-Aussetzer“ auch in HA.

**Lautstärke pro Lautsprecher und Nachtmodus:** Im Bearbeiten-Dialog eigene
AirPlay-Lautstärke pro Echo und ein Nachtmodus, z. B. höchstens 40 % von
22:00 bis 07:00. Beginnt die Nacht während der Wiedergabe, wird der Echo
leiser, außer er wurde am Gerät selbst verstellt. Es gilt die Zeitzone von HA.

### Messwerte (Info)

Gemessene bzw. abgehörte Werte als Orientierung (vor 0.9 ermittelt, bitte neu prüfen). Die Einmessung ersetzt sie,
denn die Lautstärke hängt auch von Raum und Referenz ab. Weitere Werte gern per Issue melden.

| Lautsprecher | Software | Referenz | `latency_offset_ms` | `volume_trim_db` | Methode | Datum |
|---|---|---|---|---|---|---|
| Echo 2 (radar) | echo-dot-assist 2026.11.18 | Sonos Roam 2 | **−225** | *noch offen* | von Hand | 26.11.2026 |
| Echo 2 (radar) | echo-dot-assist 2026.11.18 | HomePod mini | **−225** | *noch offen* | von Hand | 26.11.2026 |
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
  Host-Netzwerk und `NET_ADMIN`/`SYS_ADMIN` braucht. Das ist für die eigene
  IP per macvlan nötig. Seit 0.5.0 hat es ein eigenes AppArmor-Profil; es ist
  bewusst großzügig, sperrt aber den Zugriff auf Kernel-Speicher, Firmware
  und Sicherheits-Dateisysteme des Hosts.

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
- **One** free IP in Home Assistant's network for `ap2_ip`, outside the DHCP
  range – for all speakers together (they only differ in the port). At start
  the add-on checks via ARP whether the IP really is free; if a device
  answers there, the log and the page show a warning.
- A **Sendspin pairing token** for each speaker. With echo-dot-assist this is
  the "Sendspin pairing token" entity. It is disabled by default; when adding
  a speaker, the page lists such Echos with an **Enable** button (or enable
  it by hand in HA).
- iPhone, iPad and Mac must see the speakers via mDNS. If they are on another
  VLAN, mDNS forwarding has to be on.

### Installation

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories**, then add
   `https://github.com/TWIX99b/ha-airplay-sendspin`.
2. Install "AirPlay 2 → Sendspin Bridge". The first build takes a few
   minutes because shairport-sync and nqptp are compiled.
3. Configure (see the example in the German section) and start it.

### Managing speakers

Easiest on the add-on's own page, **"AirPlay Bridge"** in the HA sidebar,
tab **Speakers**:

- Each speaker is a tile: status (playing / ready / not paired), current
  track with cover, sync offset, volume.
- **Add speaker** lists every Echo with echo-dot-assist that Home Assistant
  knows, with room, model and IP. Tap one, check the name, save. For known
  models the form suggests the value from the measured-values table (e.g.
  Echo 2: −225 ms).
- **Edit**: name, Echo, stereo partner, offset, volume; under "Advanced"
  port, path, pairing token and voice assistant.
- Offset and volume take effect immediately. New, removed or changed
  speakers are applied with a short restart; the page reloads by itself.

The speakers also stay in the "Configuration" tab under `zones` and can be
edited there by hand.

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
| `lower_volume_during_voice` | While the speaker's voice assistant listens or answers, it gets its old volume back (default on). |
| `metadata` | Pass on title, artist, album and cover (default on). |
| `zones[].satellite_entity` | The speaker's voice assistant (`assist_satellite.*`). Empty = found via `token_entity`. |
| `zones[].partner_host` etc. | Second speaker as a stereo pair, see below. |
| `report_model` | Tells AirPlay the real model and manufacturer (e.g. "Echo 2" by Amazon) instead of "ShairportSync" (default on). Does not change the icon. |
| `airplay_icon` | Icon in the AirPlay menu: `standard` or, as an experiment, `airport_express` (see below). |
| `icon_model`, `icon_manufacturer` | Experiment: any model identifier and manufacturer for all speakers (wins over `airplay_icon`). |
| `zones[].speaker_volume` | AirPlay volume of this speaker in %, overrides `speaker_volume`. |
| `zones[].night_volume`, `night_start`, `night_end` | Night mode: at most this % between start and end (e.g. 40, 22:00, 07:00). |

### Calibrating the timing

The add-on has its own page **"AirPlay Bridge"** (tab **Calibrate**) in the
HA sidebar or via "Open Web UI". There you set how many milliseconds an Echo
plays earlier or later so it matches a HomePod or Sonos.

1. **Choose the speaker.** The page shows whether it is playing and whether
   the timing has locked.
2. **Play the click track** (one click per second, 10 minutes, no loop) via
   AirPlay to the group **Echo + reference (HomePod/Sonos)**: iPhone/iPad via
   Control Center → AirPlay, Mac via menu bar › Sound. Or download
   `sync-click.wav` and play it in the Music app.
3. **Match**, in one of two ways:

**By ear (default):** Stand about the same distance from both speakers. When
it matches you hear a single click; "click-click" means the Echo is off.
Adjust with **−20/−5/+5/+20 ms** (minus = Echo earlier, plus = later,
audible after 2–3 seconds). The page shows whether the current value is
saved; **Save** confirms it explicitly. "Back to the saved value" discards
experiments; known models have a button for their default.

**With microphone:** Play the track on one device (e.g. iPhone), open the page
on a **second device** (iPad, Mac) and put it in the middle. The measuring
device plays nothing itself, so the microphone cannot disturb playback.
**Measure** waits for the timing to lock and then measures three times (Echo
muted / Echo on each, about 1 minute). If the rounds disagree (more than
8 ms apart) or the signal is too weak, the page suggests nothing. Otherwise:
**Apply, save & verify** – a verification run follows automatically.

Notes:
- Since 0.9 the bridge locks onto the same timing in every AirPlay session
  (to about ±1 ms). Before, every session landed randomly a few tens of
  milliseconds off, which made calibration hardly reproducible. **Check your
  values once after the update.**
- Browsers only allow the microphone over **HTTPS** (e.g. external HA
  address). "By ear" works without it.
- Every metre of distance difference is about 3 ms.
- If playback stops or restarts during a measurement, the page says so
  instead of suggesting a wrong value.

### Volume matching

Further down on the same page is **"Match volume"**. Calibrate the timing
first.

1. The **noise track** (`level-noise.wav`) plays automatically from the page
   when measuring (or download it and play it yourself), via AirPlay to the
   same group, at normal listening volume.
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

### Voice assistant, track info, stereo pair, HA entities

**Voice assistant:** During AirPlay the Echo is at `speaker_volume` (100 %).
While its voice assistant listens or answers, the add-on sets it back to its
old volume, so the reply doesn't come at AirPlay volume. Two seconds after it
is done, it goes back up. The Echo lowers the music itself meanwhile
(echo-dot-assist, setting "Music ducking"). The add-on finds the satellite
via `token_entity`.

**Title and cover:** Title, artist, album and cover from AirPlay go to the
speaker. Echos with a screen (e.g. Show 8) show them.

**Stereo pair:** Two speakers as one AirPlay speaker, left and right. `host`
plays left, `partner_host` right:

```yaml
zones:
  - name: Living room
    host: 192.168.1.50
    port: 28928
    token_entity: sensor.living_left_sendspin_pairing_token
    partner_host: 192.168.1.51
    partner_token_entity: sensor.living_right_sendspin_pairing_token
    latency_offset_ms: -225
```

Both get the same play time; the fine offset applies to both. If one fails,
the other plays on.

**Entities in HA:** via the "AirPlay Bridge" integration, see below.

### Integration "AirPlay Bridge" (entities in HA)

Since 0.8 there is an integration of its own instead of MQTT. It puts the
AirPlay entities **straight on the Echo's device** (e.g. "Living room
satellite"), next to the voice assistant and media player:

| Entity | What |
|---|---|
| AirPlay | on while playing via AirPlay |
| AirPlay title | title, artist and album as attributes |
| AirPlay cover | cover of the current track (image) |
| AirPlay volume (day) | the Echo's volume during AirPlay (%), immediate |
| AirPlay volume now | what applies right now: day or night value (%) |
| AirPlay night mode | night mode on/off (times and value on the add-on page) |
| AirPlay test tone | button: short tone on the Echo |
| AirPlay sync offset, AirPlay volume trim | settings, immediate and saved |
| AirPlay dropouts | diagnostic: dropouts since the add-on started |

**Setup:**
1. Settings → **Marketplace** (up to HA 2026.10: HACS) → ⋮ → Custom
   repositories → `https://github.com/TWIX99b/ha-airplay-sendspin`, type
   **Integration** → install "AirPlay Bridge", restart HA.
2. The add-on announces itself: Settings → Devices & services shows
   **"AirPlay Bridge discovered"** → Configure → Submit.
3. By hand (if nothing shows up): Add integration → "AirPlay Bridge" →
   host `127.0.0.1`, port `8099`, token from the add-on page (at the bottom,
   "Home Assistant – set up the integration by hand").

Values arrive live: the add-on reports every change at once, no polling.
An MQTT broker is no longer needed.

**Moving from 0.7 (MQTT):** delete the old "… AirPlay" devices under
Settings → MQTT → Devices; switch automations to the new entities.

### Icon, test tone, connection, night mode

**Icon in the AirPlay menu:** iOS only shows a specific icon for model
identifiers it knows (Apple devices, Sonos …); everything else gets the
speaker with waves. iOS does not know Echos – reporting the real model ("Echo
2" by Amazon, option `report_model`) does not change the icon, and neither
does the Home app (tested). As an **experiment**, the option `airplay_icon:
airport_express` reports an AirPort Express identifier instead. This only
affects the icon; iOS sometimes shows the standard icon again after connecting.

**Test tone:** The ♪ button on the tile plays a short tone on the Echo
straight from the add-on, without iPhone or AirPlay – to check pairing and IP.
A stereo pair plays it left first, then right. If the Echo is set below 40 %,
the tone plays at 40 %; the old volume is restored afterwards.

**Connection:** The tile shows how often it hiccupped since the add-on
started (resync, gap, lost speaker) and how much music played: good below 1
dropout per hour, fair below 4, otherwise poor. Also as sensor "AirPlay
dropouts" in HA.

**Per-speaker volume and night mode:** In the edit dialog, an AirPlay volume
per Echo and a night mode, e.g. at most 40 % from 22:00 to 07:00. If night
starts during playback the Echo gets quieter, unless it was changed on the
device itself. HA's time zone applies.

### Measured values (info)

Measured or by-ear values as a starting point (found before 0.9, please re-check). The calibration replaces
them, since volume also depends on the room and the reference. Feel free to report more values via an issue.

| Speaker | Software | Reference | `latency_offset_ms` | `volume_trim_db` | Method | Date |
|---|---|---|---|---|---|---|
| Echo 2 (radar) | echo-dot-assist 2026.11.18 | Sonos Roam 2 | **−225** | *open* | manual | 2026-11-26 |
| Echo 2 (radar) | echo-dot-assist 2026.11.18 | HomePod mini | **−225** | *open* | manual | 2026-11-26 |
| Echo Dot 2 (biscuit) | echo-dot-assist | – | *open* | *open* | – | – |
| Echo Dot 3 (donut) | echo-dot-assist | – | *open* | *open* | – | – |
| Echo Show 8 (crown) | echo-dot-assist | – | *open* | *open* | – | – |

### Notes

- iOS picks the icon in the AirPlay picker. They show up as a regular
  speaker.
- When AirPlay pauses, the speaker stays taken for `idle_timeout_s` seconds.
- iOS rebuilds the session when speakers are added or removed from a group.
  A short dropout is normal then.
- HA's security rating is low because the add-on needs host networking and
  `NET_ADMIN`/`SYS_ADMIN`. That is required for the dedicated IP via macvlan.
  Since 0.5.0 it has its own AppArmor profile; it is deliberately broad but
  blocks access to the host's kernel memory, firmware and security file
  systems.

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
