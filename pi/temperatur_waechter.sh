#!/bin/sh
# Temperaturwaechter: schreibt den Zustand fuer die Vogelseite und schaltet
# den Pi ab, bevor er zu heiss wird. Laeuft als root unter systemd
# (vogel-temperatur.service).
#
# Zwei Schwellen:
#   WARNUNG_C     - die Seite zeigt einen gelben Hinweis mit der Temperatur
#   ABSCHALTUNG_C - der Pi merkt sich den Zeitpunkt und faehrt herunter;
#                   nach dem naechsten Einschalten zeigt die Seite einen
#                   roten Hinweis, bis er weggeklickt wird
#
# Der Merker ueberlebt den Neustart (/var/lib), der Zustand fuer die Seite
# liegt beim Frontend und ist als temperatur.json im Webroot verlinkt.

# Sonst schreibt awk auf deutsch eingestellten Systemen "49,2" mit Komma -
# und die Seite kann die Datei nicht lesen.
export LC_ALL=C

WARNUNG_C="${WARNUNG_C:-70}"
ABSCHALTUNG_C="${ABSCHALTUNG_C:-75}"
STATUS="${STATUS:-/home/vogel/BirdNET-Pi/avian/frontend/temperatur.json}"
MARKER="/var/lib/vogel-temperatur/letzte-abschaltung"
NUR_PRUEFEN="${NUR_PRUEFEN:-0}"   # 1 = niemals abschalten (zum Testen)

mkdir -p "$(dirname "$MARKER")"

schreibe_status() {
  # $1 = Temperatur in Grad, $2 = ok|warnung|abschaltung
  letzte=""
  [ -f "$MARKER" ] && letzte=$(cat "$MARKER")
  if [ -n "$letzte" ]; then letzte="\"$letzte\""; else letzte="null"; fi
  # Erst danebenschreiben, dann umbenennen - die Seite soll nie eine halbe
  # Datei lesen.
  printf '{"temp_c": %s, "status": "%s", "warnung_ab_c": %s, "abschaltung_ab_c": %s, "stand": "%s", "letzte_abschaltung": %s}\n' \
    "$1" "$2" "$WARNUNG_C" "$ABSCHALTUNG_C" "$(date '+%Y-%m-%d %H:%M:%S')" "$letzte" \
    > "$STATUS.neu"
  chmod 644 "$STATUS.neu"
  mv "$STATUS.neu" "$STATUS"
}

zustand=""
zaehler=0
while :; do
  milli=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)
  grad=$(awk "BEGIN{printf \"%.1f\", $milli/1000}")
  ganz=$((milli / 1000))

  if [ "$ganz" -ge "$ABSCHALTUNG_C" ] && [ "$NUR_PRUEFEN" != "1" ]; then
    echo "Abschaltung: $grad Grad (Grenze $ABSCHALTUNG_C). Fahre herunter."
    date '+%Y-%m-%d %H:%M' > "$MARKER"
    schreibe_status "$grad" "abschaltung"
    sync
    poweroff
    exit 0
  fi

  if [ "$ganz" -ge "$WARNUNG_C" ]; then
    neu="warnung"
  else
    neu="ok"
  fi
  [ "$neu" = "warnung" ] && [ "$zustand" != "warnung" ] && \
    echo "Warnschwelle erreicht: $grad Grad (Abschaltung bei $ABSCHALTUNG_C)"
  [ "$neu" = "ok" ] && [ "$zustand" = "warnung" ] && \
    echo "Temperatur wieder normal: $grad Grad"

  # Die SD-Karte schonen: nur bei Aenderung schreiben, sonst alle 5 Minuten
  # zur Auffrischung des Zeitstempels - im Warnzustand jede Runde, damit die
  # Seite die aktuelle Temperatur zeigt.
  zaehler=$((zaehler + 1))
  if [ "$neu" != "$zustand" ] || [ "$neu" = "warnung" ] || [ "$zaehler" -ge 10 ]; then
    schreibe_status "$grad" "$neu"
    zaehler=0
  fi
  zustand="$neu"
  sleep 30
done
