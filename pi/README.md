# Uplink für den Raspberry Pi

Sendet die Vogelerkennungen von BirdNET-Pi an den eigenen Server. Läuft als
Dienst im Hintergrund und übersteht Netzausfälle, ohne Erkennungen zu verlieren.

## Was der Dienst tut

Alle 60 Sekunden liest er die Datenbank von BirdNET-Pi (`birds.db`, nur lesend)
und schickt neue Einträge an den Server. Bereits Gesendetes merkt er sich, jede
Erkennung trägt einen eindeutigen Schlüssel — mehrfaches Senden legt also keine
Doppel an.

Ist der Server nicht erreichbar, sammelt er in einem eigenen Zwischenspeicher
und liefert nach, sobald die Verbindung steht. Er gibt nie auf: die Wartezeit
zwischen Versuchen wächst auf höchstens 15 Minuten. Alle fünf Minuten meldet er
ein Lebenszeichen, damit am Server auffällt, wenn das Mikrofon still ist.

Er braucht **keine zusätzlichen Python-Pakete** — nur die Standardbibliothek.
Auf dem Pi ist also nichts nachzuinstallieren.

## Einrichten

Am einfachsten vom Mac aus, das erledigt alles auf einmal:

```bash
cd "$PROJEKT/bereitstellung" && ./setup-pi.sh uplink
```

Von Hand auf dem Pi:

```bash
mkdir -p ~/avian-uplink
cp avian_uplink.py config.example.toml ~/avian-uplink/
mv ~/avian-uplink/config.example.toml ~/avian-uplink/config.toml
# config.toml anpassen: server_url und device_token eintragen
sudo cp avian-uplink.service /etc/systemd/system/
sudo sed -i "s/__USER__/$USER/g" /etc/systemd/system/avian-uplink.service
sudo systemctl daemon-reload && sudo systemctl enable --now avian-uplink
```

## Nachsehen, ob es läuft

```bash
systemctl status avian-uplink
journalctl -u avian-uplink -f
```

## Zum Ausprobieren

```bash
python3 avian_uplink.py --once --verbose      # ein Durchlauf, dann Schluss
python3 avian_uplink.py --once --dry-run      # nichts senden, nur zeigen
python3 avian_uplink.py --db /pfad/test.db    # andere Datenbank, etwa zum Testen am Mac
python3 avian_uplink.py --reset-cursor        # alles noch einmal von vorn senden
```

## Wenn etwas klemmt

**Nichts kommt an.** Erreicht der Pi den Server? `curl http://<server>:8090/healthz`.
In `config.toml` muss die Adresse des Servers stehen, nicht `localhost` — das
wäre der Pi selbst.

**„unauthorized".** Der `device_token` stimmt nicht mit dem Server überein. Er
steht dort in `server/.env` als `DEVICE_TOKEN`.

**„database is locked".** BirdNET-Pi schreibt gerade. Der Dienst wartet von
selbst und versucht es erneut; das ist unbedenklich.

**Datenbank nicht lesbar.** Als Ausweg `http_source_url` in der Konfiguration
setzen — dann holt der Dienst die Erkennungen über die Weboberfläche von
BirdNET-Pi statt direkt aus der Datei.

## Temperaturwächter

Der Pi schaltet sich bei **75 °C** selbst ab, bevor die Hitze Schaden anrichtet
(`temperatur_waechter.sh`, läuft als Dienst `vogel-temperatur.service` und
prüft alle 30 Sekunden). Ab **70 °C** zeigt die Vogelseite eine gelbe Leiste
mit der aktuellen Temperatur. Nach einer Abschaltung — wieder einschalten muss
ihn jemand von Hand — zeigt die Seite eine rote Leiste mit dem Zeitpunkt, bis
sie weggeklickt wird.

Die Seite liest dafür `temperatur.json`, die der Wächter pflegt; eingeblendet
wird die Leiste von `temperatur-banner.js`, das per `<script>`-Zeile in der
`index.html` hängt (Sicherung: `index.html.bak.temperatur`).

Einrichten auf einem neuen Pi:

```bash
cd "$PROJEKT/bereitstellung" && ./setup-pi.sh temperatur
```

Zum Testen ohne echte Abschaltung: `NUR_PRUEFEN=1` und die Schwellen
`WARNUNG_C`/`ABSCHALTUNG_C` als Umgebungsvariablen setzen. Wichtig im Skript:
`LC_ALL=C`, sonst schreibt awk die Temperatur mit Komma und die Datei ist
kein gültiges JSON mehr.
