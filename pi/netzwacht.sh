#!/bin/bash
# Netzwacht - holt den Pi zurueck ins Netz, wenn er sich abgemeldet hat.
#
# WOFUER: Am 11.09.2026 war der Pi 142 Minuten nicht erreichbar. Er lief die
# ganze Zeit und hat Voegel erkannt - er hatte nur die WLAN-Verbindung
# verloren und fand allein nicht zurueck. Der Empfang liegt bei -67 bis
# -71 dBm, also an der Grenze; so etwas wird wieder vorkommen.
#
# WAS SIE TUT: Sie prueft, ob der Server erreichbar ist, und geht bei
# anhaltender Stille eine Leiter hoch - vom sanftesten Mittel zum haertesten.
# Jede Stufe wird protokolliert, damit man hinterher sieht, was geholfen hat.
#
# WAS SIE NICHT TUT: Sie fasst nichts an, solange die Verbindung steht. Ein
# Wachhund, der grundlos anschlaegt, ist schlimmer als keiner.
set -u

SERVER="${SERVER:-203.0.113.10}"
PORT="${PORT:-22}"
GERAET="${GERAET:-wlan0}"
ZAEHLER="${ZAEHLER:-/run/vogel-netzwacht.zaehler}"

# Stufen in Durchlaeufen (die Einheit laeuft alle 3 Minuten):
STUFE_VERBINDUNG=3     #  9 Minuten: Verbindung neu aufbauen
STUFE_DIENST=7         # 21 Minuten: NetworkManager neu starten
STUFE_NEUSTART=15      # 45 Minuten: Pi neu starten

melde() { logger -t vogel-netzwacht "$1"; echo "$1"; }

# Das Heimnetz selbst - wenn die Fritz!Box antwortet, steht das WLAN des Pi,
# und dann liegt es NICHT an ihm. Die Adresse wird bei jedem Lauf frisch
# ermittelt, damit sie einen Netzwechsel ueberlebt.
heimnetz_steht() {
  # TOR laesst sich von aussen setzen - nur damit sich dieser Zweig pruefen
  # laesst, ohne das echte Heimnetz abzuschalten. Im Betrieb wird die Adresse
  # bei jedem Lauf frisch ermittelt.
  local TOR="${TOR:-}"
  [ -n "$TOR" ] || TOR=$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')
  [ -n "$TOR" ] || return 1
  ping -c 2 -W 3 "$TOR" >/dev/null 2>&1 && return 0
  timeout 5 bash -c "exec 3<>/dev/tcp/$TOR/80" 2>/dev/null
}

erreichbar() {
  # Entscheidend ist, ob wir den Server wirklich sprechen koennen - genau das,
  # was der Abgleich braucht. Kein ping: ICMP kann unterwegs gefiltert sein.
  #
  # ACHTUNG, hier steckte ein gefaehrlicher Fehler: /dev/tcp gibt es NUR in
  # bash. Mit "#!/bin/sh" (auf Debian dash) schlug die Pruefung IMMER fehl -
  # der Wachhund haette den Pi also alle 45 Minuten grundlos neu gestartet.
  # Deshalb bash in der ersten Zeile, und zur Sicherheit ein zweiter Weg.
  if timeout 8 bash -c "exec 3<>/dev/tcp/$SERVER/$PORT" 2>/dev/null; then
    return 0
  fi
  # Rueckfall, falls /dev/tcp einmal nicht verfuegbar ist
  command -v nc >/dev/null 2>&1 && nc -z -w 6 "$SERVER" "$PORT" 2>/dev/null
}

# Bei jedem Durchlauf die Funkstaerke mitschreiben. Als der Pi am 11.09. zwei
# Stunden weg war, gab es keinerlei Verlauf - niemand konnte sagen, ob der
# Empfang schon vorher schlechter geworden war. Das soll nicht wieder passieren.
FUNK=$(awk '/wlan0/ {gsub(/\./,"",$3); gsub(/\./,"",$4); print "Qualitaet "$3", Signal "$4" dBm"}' /proc/net/wireless 2>/dev/null)
[ -n "$FUNK" ] && logger -t vogel-netzwacht "Funk: $FUNK"

N=0
[ -f "$ZAEHLER" ] && N=$(cat "$ZAEHLER" 2>/dev/null || echo 0)

if erreichbar; then
  if [ "$N" -gt 0 ]; then
    melde "wieder erreichbar nach $N Fehlversuchen"
  fi
  echo 0 > "$ZAEHLER"
  exit 0
fi

# Der Server ist nicht erreichbar. Aber liegt es am Pi?
#
# Ohne diese Unterscheidung repariert der Wachhund etwas, das nicht kaputt ist:
# Faellt der SERVER aus (Wartung, Stoerung), saehe er nur "nicht erreichbar"
# und wuerde am Ende den Pi alle 45 Minuten neu starten - waehrend in jeder
# Neustartminute niemand den Voegeln zuhoert.
if heimnetz_steht; then
  melde "Server nicht erreichbar, aber das Heimnetz steht - liegt nicht am Pi, nichts unternommen"
  echo 0 > "$ZAEHLER"
  exit 0
fi

N=$((N + 1))
echo "$N" > "$ZAEHLER"
melde "weder Server noch Heimnetz erreichbar (Fehlversuch $N)"

if [ "$N" -eq "$STUFE_VERBINDUNG" ]; then
  melde "Stufe 1: WLAN-Verbindung neu aufbauen"
  nmcli device disconnect "$GERAET" >/dev/null 2>&1
  sleep 3
  nmcli device connect "$GERAET" >/dev/null 2>&1
elif [ "$N" -eq "$STUFE_DIENST" ]; then
  melde "Stufe 2: NetworkManager neu starten"
  systemctl restart NetworkManager >/dev/null 2>&1
elif [ "$N" -ge "$STUFE_NEUSTART" ]; then
  # Letztes Mittel. Unbedenklich: BirdNET und alle eigenen Dienste starten
  # automatisch mit, und die Erkennungen liegen in der Datenbank auf der Karte.
  melde "Stufe 3: Neustart des Pi nach $N Fehlversuchen"
  echo 0 > "$ZAEHLER"
  sync
  systemctl reboot
fi
