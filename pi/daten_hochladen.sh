#!/bin/sh
# Schiebt die BirdNET-Daten vom Pi zum Server, damit dort die Original-
# Oberflaeche damit arbeiten kann.
#
# Uebertragen werden:
#   - birds.db          die Datenbank mit allen Erkennungen
#   - BirdSongs/Extracted  die Aufnahmen und Spektrogramme
#
# ZWEI GESCHWINDIGKEITEN, und warum:
#   Frueher lief alles im Zehn-Minuten-Takt. Damit stand ein Vogel erst nach
#   durchschnittlich fuenfeinhalb, schlimmstenfalls elf Minuten auf der
#   Webseite - obwohl BirdNET ihn schon nach 6 bis 21 Sekunden kannte und die
#   Seite alle 30 Sekunden nachfragt. Die Bremse war allein dieser Abgleich.
#
#   Aufgeteilt statt einfach beschleunigt: Die Datenbank ist 0,6 MB und in
#   vier Sekunden oben - die geht jetzt alle zwei Minuten. Fuer die Aufnahmen
#   muss rsync jedes Mal den ganzen Aufnahmeordner durchgehen, und haeufigeres
#   Lesen der SD-Karte ist genau das, was an diesen Geraeten am ehesten
#   kaputtgeht. Die bleiben deshalb im Zehn-Minuten-Takt - man braucht sie
#   ohnehin erst, wenn jemand auf Abspielen tippt.
#
#   Aufruf:  daten_hochladen.sh db      nur Zustand und Datenbank (schnell)
#            daten_hochladen.sh alles   alles (Vorgabe, wie bisher)
#
# WICHTIG - warum die Datenbank nicht einfach kopiert wird:
# BirdNET schreibt laufend in birds.db. Eine schlichte Kopie waere womoeglich
# mitten in einem Schreibvorgang entstanden und damit beschaedigt. Der Befehl
# ".backup" von sqlite3 erzeugt dagegen eine in sich stimmige Kopie, auch
# waehrend geschrieben wird.
set -eu

MODUS="${1:-alles}"
case "$MODUS" in
  db|alles) ;;
  *) echo "Aufruf: $0 [db|alles]" >&2; exit 2 ;;
esac

# Beide Zeitgeber greifen auf dieselbe Leitung und dieselbe Zwischendatei zu.
# Ohne Schloss koennten sich der schnelle und der langsame Lauf ueberholen und
# sich gegenseitig die Datenbank-Kopie unter den Fuessen wegziehen. -n heisst:
# nicht warten - laeuft schon einer, wird dieser Lauf einfach uebersprungen.
# Der naechste kommt in zwei Minuten ohnehin.
SCHLOSS="${SCHLOSS:-/run/lock/vogel-upload.lock}"
if [ "${VOGEL_UPLOAD_IM_SCHLOSS:-}" != "1" ]; then
  VOGEL_UPLOAD_IM_SCHLOSS=1
  export VOGEL_UPLOAD_IM_SCHLOSS
  flock -n -E 99 "$SCHLOSS" "$0" "$@" && exit 0
  ERG=$?
  if [ "$ERG" = 99 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') laeuft bereits - uebersprungen ($MODUS)"
    exit 0
  fi
  exit "$ERG"
fi

SERVER="${SERVER:-pisync@203.0.113.10}"
KEY="${KEY:-$HOME/.ssh/id_server}"
DB="$HOME/BirdNET-Pi/scripts/birds.db"
AUFNAHMEN="$HOME/BirdSongs/Extracted"
ZWISCHEN="/tmp/birds-kopie.db"

# ConnectTimeout gilt NUR fuer den Verbindungsaufbau. Bricht die Leitung
# waehrend der Uebertragung weg - WLAN-Aussetzer, NAT-Zeitablauf im Router -
# merkt SSH das ohne ServerAlive* nicht und wartet endlos. Genau so hing der
# Abgleich am 25.08.2026 zwei Stunden lang, ohne dass irgendwo ein Fehler stand.
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 \
     -o ServerAliveInterval=15 -o ServerAliveCountMax=4"

# 0. Zustand des Pi erfassen - Spannung, Temperatur, Platte, SD-Karte.
#    Der Server zeigt daraus bei Bedarf eine Warnung auf der Wandanzeige.
#
#    NUR im vollen Lauf. Als der schnelle Weg dazukam, lief das erst in beiden
#    Modi - also 720-mal am Tag statt 144, jedes Mal 660 Byte auf die SD-Karte
#    geschrieben. Fuer eine Warnung, die auch zehn Minuten alt sein darf, ist
#    das der falsche Handel: Eine sterbende Karte ist an diesen Geraeten die
#    haeufigste Ausfallursache ueberhaupt.
if [ "$MODUS" = alles ]; then
  [ -x "$HOME/zustand_erfassen.sh" ] && "$HOME/zustand_erfassen.sh" >/dev/null 2>&1 || true
fi

# 1. Stimmige Kopie der Datenbank ziehen
sqlite3 "$DB" ".backup '$ZWISCHEN'" || {
  echo "Datenbank-Kopie fehlgeschlagen" >&2
  exit 1
}

# 2. Datenbank und Zustand hochladen (beides klein, geht schnell)
rsync -az --timeout=120 -e "$SSH" "$ZWISCHEN" "$SERVER:/birds.db"
rm -f "$ZWISCHEN"
if [ -f "$HOME/BirdNET-Pi/zustand.json" ]; then
  rsync -az --timeout=60 -e "$SSH" "$HOME/BirdNET-Pi/zustand.json" "$SERVER:/zustand.json"
fi

# 3. Aufnahmen und Spektrogramme abgleichen - ohne --delete.
#    Der Server verbietet dem Pi das Loeschen ausdruecklich (-no-del in seiner
#    authorized_keys). Waere es erlaubt, koennte ein uebernommener Pi den
#    gesamten Datenbestand auf dem Server leeren.
#    Aufgeraeumt wird deshalb auf dem Server selbst - siehe aufraeumen.sh dort.
if [ "$MODUS" = alles ] && [ -d "$AUFNAHMEN" ]; then
  rsync -az --timeout=300 -e "$SSH" "$AUFNAHMEN/" "$SERVER:/Extracted/"
fi

# 4. Falsch-Liste vom Server holen. Sie ist der Qualitaetsfilter fuer
#    BirdWeather: als falsch markierte Arten werden nicht geteilt. Schlaegt
#    der Abruf fehl, bleibt die letzte Kopie liegen - eine leicht veraltete
#    Liste ist besser als gar keine, und der Tonschutz haengt nicht daran.
if [ "$MODUS" = alles ]; then
  rsync -az --timeout=60 -e "$SSH" "$SERVER:/verstecken.json" \
        "$HOME/BirdNET-Pi/verstecken.json" 2>/dev/null || true
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Abgleich fertig ($MODUS)"
