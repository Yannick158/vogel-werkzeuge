#!/usr/bin/env bash
# Richtet den frisch gestarteten Raspberry Pi ein - vom Mac aus per SSH.
#
#   ./setup-pi.sh wait        auf den ersten Start warten
#   ./setup-pi.sh check       System und Mikrofon pruefen (inkl. Testaufnahme)
#   ./setup-pi.sh install     BirdNET-Pi/AvianVisitors installieren (laeuft abbruchsicher weiter)
#   ./setup-pi.sh progress    Installationsfortschritt ansehen
#   ./setup-pi.sh configure   Standort zweitstandort + deutsche Artnamen setzen
#   ./setup-pi.sh uplink      Uplink-Dienst zum eigenen Server einrichten
#   ./setup-pi.sh status      Gesamtueberblick
#
# Pfade sind gegen das Fork-Repo verifiziert:
#   Konfiguration : $HOME/BirdNET-Pi/birdnet.conf  (Symlink /etc/birdnet/birdnet.conf)
#   Datenbank     : $HOME/BirdNET-Pi/scripts/birds.db
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$DIR")"
SECRETS="$DIR/secrets.env"
# shellcheck disable=SC1090
[ -f "$SECRETS" ] && source "$SECRETS"

PI_HOSTNAME="${PI_HOSTNAME:-birdnet}"
PI_USER="${PI_USER:-vogel}"
# Erst den Namen versuchen, dann die feste Adresse: In manchen Netzen
# unterdrueckt der Router die Namensaufloesung im lokalen Netz (mDNS).
# Geprueft wird mit einer Namensabfrage statt mit ping - ping meldet sein
# Zeitlimit als Signal, was die Shell als Stoerung ausgibt.
if [ -n "${PI_HOST_OVERRIDE:-}" ]; then
  HOST="$PI_HOST_OVERRIDE"
elif dscacheutil -q host -a name "$PI_HOSTNAME.local" 2>/dev/null | grep -q "ip_address"; then
  HOST="$PI_HOSTNAME.local"
elif [ -n "${PI_IP:-}" ]; then
  HOST="$PI_IP"
else
  HOST="$PI_HOSTNAME.local"
fi
KEY="$DIR/id_birdnet"
# ssh trennt UserKnownHostsFile an Leerzeichen, der Projektpfad enthaelt eins.
KNOWN_HOSTS="$HOME/.ssh/known_hosts_birdnet"
mkdir -p "$HOME/.ssh" 2>/dev/null || true
LAT="${SITE_LAT:?SITE_LAT in secrets.env setzen (Breitengrad des Standorts)}"
LON="${SITE_LON:?SITE_LON in secrets.env setzen (Laengengrad des Standorts)}"
SERVER_URL="${SERVER_URL:-}"
DEVICE_TOKEN="${DEVICE_TOKEN:-}"

# Pfade koennen Leerzeichen enthalten - deshalb jede Option einzeln zitieren
# statt sie in einer Zeichenkette zu sammeln.
sshpi() {
  ssh -i "$KEY" -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$KNOWN_HOSTS" -o ConnectTimeout=8 \
      "$PI_USER@$HOST" "$@"
}
scppi() {
  scp -i "$KEY" -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$KNOWN_HOSTS" -o ConnectTimeout=8 "$@"
}

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mFEHLER: %s\033[0m\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------- wait ---
phase_wait() {
  say "Warte auf $HOST"
  echo "Der erste Start dauert 2-4 Minuten; der Pi startet dabei einmal neu."
  local i
  for i in $(seq 1 90); do
    if sshpi true 2>/dev/null; then
      printf '\n'; say "Verbindung steht."
      sshpi 'echo "  Host: $(hostname)  |  IP: $(hostname -I | cut -d" " -f1)  |  Uptime:$(uptime -p | sed s/up//)"'
      return 0
    fi
    printf '.'; sleep 10
  done
  printf '\n'
  fail "$HOST nach 15 Minuten nicht erreichbar.
Pruefen: Leuchtet die gruene LED am Pi? Steht das Geraet in der Router-Geraeteliste?
Falls mDNS blockiert ist, IP im Router nachsehen und erneut versuchen mit:
  PI_HOST_OVERRIDE=<ip> ./setup-pi.sh wait"
}

# ------------------------------------------------------------------ check ---
phase_check() {
  say "System"
  sshpi 'echo "  OS      : $(. /etc/os-release; echo $PRETTY_NAME)"
         echo "  Kernel  : $(uname -r) ($(uname -m))"
         echo "  RAM     : $(free -h | sed -n "2s/[^ ]* *\([^ ]*\).*/\1/p")"
         echo "  Platte  : $(df -h / | awk "NR==2{print \$4\" frei von \"\$2}")"
         echo "  Temp    : $(vcgencmd measure_temp 2>/dev/null | cut -d= -f2 || echo n/a)"
         echo "  Drossel : $(vcgencmd get_throttled 2>/dev/null | cut -d= -f2 || echo n/a)  (0x0 = alles gut)"'

  say "Aufnahmegeraete"
  if ! sshpi 'LC_ALL=C arecord -l 2>/dev/null | grep -q ^card'; then
    printf '\033[31m  Kein Aufnahmegeraet gefunden - steckt das USB-Mikrofon am Pi?\033[0m\n'
    return 1
  fi
  sshpi 'LC_ALL=C arecord -l | sed "s/^/  /"'

  local card
  card=$(sshpi 'LC_ALL=C arecord -l | sed -n "s/^card \([0-9]\+\).*/\1/p" | head -1')
  say "Mikrofon-Faehigkeiten (card $card)"
  sshpi "arecord -D plughw:$card,0 --dump-hw-params -d 1 /dev/null 2>&1 | sed -n '/HW Params/,\$p' | grep -E 'RATE|CHANNELS|FORMAT' | sed 's/^/  /'" || true

  say "48-kHz-Testaufnahme (5 Sekunden)"
  if sshpi "arecord -D plughw:$card,0 -f S16_LE -r 48000 -c 1 -d 5 /tmp/mictest.wav 2>&1 | sed 's/^/  /'"; then
    echo "  Aufnahme gelungen."
    sshpi 'ls -la /tmp/mictest.wav | sed "s/^/  /"'
    say "Pegelmessung"
    sshpi 'command -v sox >/dev/null 2>&1 || sudo apt-get install -y sox >/dev/null 2>&1 || true
           if command -v sox >/dev/null 2>&1; then
             sox /tmp/mictest.wav -n stat 2>&1 | grep -Ei "Maximum amplitude|RMS.*amplitude" | sed "s/^/  /"
           else echo "  (sox nicht verfuegbar - Pegel nicht messbar)"; fi'
    echo
    echo "  Deutung: Maximum amplitude nahe 0 bedeutet Stille (Mikro stumm oder nicht angeschlossen)."
    echo "           Werte um 1.0 bedeuten Uebersteuerung."
  else
    printf '\033[31m  Testaufnahme fehlgeschlagen - Mikrofon pruefen.\033[0m\n'
    return 1
  fi
}

# ---------------------------------------------------------------- install ---
phase_install() {
  say "Installiere BirdNET-Pi (AvianVisitors-Fork)"
  echo "Dauer: 20-40 Minuten. Die Installation laeuft abbruchsicher auf dem Pi weiter,"
  echo "auch wenn diese Verbindung getrennt wird. Am Ende startet der Pi neu."
  sshpi 'if [ -d ~/BirdNET-Pi ]; then echo "BEREITS_INSTALLIERT"; fi' | grep -q BEREITS_INSTALLIERT && {
    say "BirdNET-Pi ist bereits vorhanden - Installation wird uebersprungen."
    return 0
  }
  # setsid + nohup: ueberlebt das Ende der SSH-Sitzung und den Reboot-Aufruf.
  sshpi 'rm -f ~/install.log
         setsid nohup bash -c "curl -s https://raw.githubusercontent.com/Twarner491/AvianVisitors/avian-visitors/newinstaller.sh | bash" \
           > ~/install.log 2>&1 < /dev/null &
         echo "Installation gestartet (Log: ~/install.log)"'
  echo
  echo "Fortschritt ansehen:  ./setup-pi.sh progress"
}

phase_progress() {
  say "Installationsfortschritt"
  sshpi 'if [ ! -f ~/install.log ]; then echo "Kein Installations-Log gefunden."; exit 0; fi
         echo "  Zeilen im Log: $(wc -l < ~/install.log)"
         echo "  Letzte Meldungen:"; tail -15 ~/install.log | sed "s/^/    /"
         echo
         if [ -d ~/BirdNET-Pi ]; then echo "  BirdNET-Pi Verzeichnis: vorhanden"; fi
         if [ -f ~/BirdNET-Pi/scripts/birds.db ]; then echo "  Datenbank: angelegt"; fi
         systemctl is-active birdnet_analysis.service 2>/dev/null | sed "s/^/  Analyse-Dienst: /" || true' || \
    echo "Pi nicht erreichbar - vermutlich laeuft gerade der Neustart. Erneut versuchen: ./setup-pi.sh wait"
}

# -------------------------------------------------------------- configure ---
phase_configure() {
  say "Setze Standort und Sprache"
  sshpi 'test -f ~/BirdNET-Pi/birdnet.conf' || fail "birdnet.conf nicht gefunden - ist die Installation fertig? (./setup-pi.sh progress)"

  sshpi "cp ~/BirdNET-Pi/birdnet.conf ~/BirdNET-Pi/birdnet.conf.bak.\$(date +%s)
         sed -i 's|^LATITUDE=.*|LATITUDE=$LAT|; s|^LONGITUDE=.*|LONGITUDE=$LON|; s|^DATABASE_LANG=.*|DATABASE_LANG=de|; s|^SITE_NAME=.*|SITE_NAME=\"zweitstandort\"|' ~/BirdNET-Pi/birdnet.conf
         grep -E '^(LATITUDE|LONGITUDE|DATABASE_LANG|SITE_NAME)=' ~/BirdNET-Pi/birdnet.conf | sed 's/^/  /'"

  say "Setze das Aufnahmegeraet"
  # ALSA-Kartennummern sind NICHT stabil: nach einem Neustart oder Umstecken
  # kann aus Karte 3 die Karte 1 werden, und die Aufnahme laeuft ins Leere.
  # Deshalb den Kartennamen verwenden, der bleibt gleich.
  local micname
  micname=$(sshpi 'LC_ALL=C arecord -L 2>/dev/null | grep "^plughw:CARD=" | grep -v -i "headphone\|hdmi\|vc4" | head -1')
  if [ -n "$micname" ]; then
    sshpi "sed -i 's|^REC_CARD=.*|REC_CARD=\"$micname\"|' ~/BirdNET-Pi/birdnet.conf
           grep -E '^REC_CARD=' ~/BirdNET-Pi/birdnet.conf | sed 's/^/  /'"
  else
    printf '\033[31m  Kein USB-Aufnahmegeraet gefunden - REC_CARD bleibt unveraendert.\033[0m\n'
  fi

  say "Schalte Livestream ab"
  # Der Livestream belegt das Mikrofon exklusiv; danach kann die Aufnahme
  # nicht mehr darauf zugreifen ("Device or resource busy").
  sshpi 'sudo systemctl disable --now livestream.service icecast2.service 2>/dev/null; echo "  livestream: $(systemctl is-active livestream 2>/dev/null)"' || true

  say "Lade deutsche Artnamen"
  sshpi '~/BirdNET-Pi/scripts/install_language_label.sh 2>&1 | tail -3 | sed "s/^/  /" || echo "  (Sprachumstellung meldete einen Fehler - spaeter in der Weboberflaeche pruefen)"'

  say "Starte Dienste neu"
  sshpi 'sudo /usr/local/bin/restart_services.sh 2>/dev/null || sudo ~/BirdNET-Pi/scripts/restart_services.sh 2>/dev/null || sudo systemctl restart birdnet_analysis.service birdnet_recording.service' || true
  sleep 5
  sshpi 'for s in birdnet_analysis birdnet_recording caddy; do
           printf "  %-22s %s\n" "$s" "$(systemctl is-active $s.service 2>/dev/null || echo unbekannt)"; done'
  say "Weboberflaeche: http://$HOST/"
}

# ----------------------------------------------------------------- uplink ---
phase_uplink() {
  [ -n "$SERVER_URL" ]   || fail "SERVER_URL fehlt in secrets.env (z. B. http://192.168.1.50:8090)"
  [ -n "$DEVICE_TOKEN" ] || fail "DEVICE_TOKEN fehlt in secrets.env (steht in server/.env)"
  [ -f "$ROOT/pi-client/avian_uplink.py" ] || fail "pi-client/avian_uplink.py fehlt"

  say "Richte Uplink zum Server ein ($SERVER_URL)"
  sshpi 'mkdir -p ~/avian-uplink'
  scppi "$ROOT/pi-client/avian_uplink.py" "$PI_USER@$HOST:~/avian-uplink/" >/dev/null
  sshpi "cat > ~/avian-uplink/config.toml" <<EOF
server_url = "$SERVER_URL"
device_id = "pi-zweitstandort-01"
device_token = "$DEVICE_TOKEN"
db_path = "/home/$PI_USER/BirdNET-Pi/scripts/birds.db"
tz = "Europe/Beispielstadt"
poll_interval_s = 60
EOF
  say "Probelauf (einmalige Uebertragung)"
  sshpi 'cd ~/avian-uplink && python3 avian_uplink.py --once --config ~/avian-uplink/config.toml 2>&1 | tail -12 | sed "s/^/  /"' || \
    echo "  Probelauf meldete einen Fehler - Ausgabe oben pruefen."

  if [ -f "$ROOT/pi-client/avian-uplink.service" ]; then
    say "Richte Dauerbetrieb ein"
    sed "s|__USER__|$PI_USER|g" "$ROOT/pi-client/avian-uplink.service" | sshpi 'sudo tee /etc/systemd/system/avian-uplink.service >/dev/null'
    sshpi 'sudo systemctl daemon-reload && sudo systemctl enable --now avian-uplink && sleep 3
           systemctl is-active avian-uplink.service | sed "s/^/  Dienst: /"'
  fi
}

# ----------------------------------------------------------------- status ---
phase_status() {
  say "Gesamtstatus"
  if ! sshpi true 2>/dev/null; then echo "  Pi nicht erreichbar ($HOST)"; return 1; fi
  sshpi 'printf "  %-24s %s\n" "Hostname" "$(hostname)"
         printf "  %-24s %s\n" "IP" "$(hostname -I | cut -d" " -f1)"
         printf "  %-24s %s\n" "BirdNET-Pi installiert" "$([ -d ~/BirdNET-Pi ] && echo ja || echo nein)"
         printf "  %-24s %s\n" "Datenbank" "$([ -f ~/BirdNET-Pi/scripts/birds.db ] && echo vorhanden || echo fehlt)"
         if [ -f ~/BirdNET-Pi/scripts/birds.db ]; then
           printf "  %-24s %s\n" "Erkennungen gesamt" "$(sqlite3 ~/BirdNET-Pi/scripts/birds.db "select count(*) from detections" 2>/dev/null || echo n/a)"
           printf "  %-24s %s\n" "Letzte Erkennung" "$(sqlite3 ~/BirdNET-Pi/scripts/birds.db "select Date||\" \"||Time||\" \"||Com_Name from detections order by Date desc, Time desc limit 1" 2>/dev/null || echo keine)"
         fi
         for s in birdnet_analysis birdnet_recording caddy avian-uplink; do
           printf "  %-24s %s\n" "$s" "$(systemctl is-active $s.service 2>/dev/null || echo -)"; done'
}


phase_temperatur() {
  echo "== Temperaturwaechter: Abschaltung bei Ueberhitzung =="
  scppi "$ROOT/pi-client/temperatur_waechter.sh" "$ROOT/pi-client/vogel-temperatur.service" \
        "$ROOT/pi-client/temperatur-banner.js" "$PI_USER@$HOST:/tmp/" >/dev/null
  sshpi '
    sudo install -m 755 /tmp/temperatur_waechter.sh /usr/local/bin/temperatur_waechter.sh
    sudo install -m 644 /tmp/vogel-temperatur.service /etc/systemd/system/vogel-temperatur.service
    install -m 644 /tmp/temperatur-banner.js ~/BirdNET-Pi/avian/frontend/temperatur-banner.js
    rm -f /tmp/temperatur_waechter.sh /tmp/vogel-temperatur.service /tmp/temperatur-banner.js
    ln -sf ~/BirdNET-Pi/avian/frontend/temperatur.json ~/BirdSongs/Extracted/temperatur.json
    ln -sf ~/BirdNET-Pi/avian/frontend/temperatur-banner.js ~/BirdSongs/Extracted/temperatur-banner.js
    cd ~/BirdNET-Pi/avian/frontend
    if ! grep -q "temperatur-banner.js" index.html; then
      cp index.html index.html.bak.temperatur
      sed -i "s|</body>|<script src=\"./temperatur-banner.js?v=1\"></script>\n</body>|" index.html
    fi
    sudo systemctl daemon-reload
    sudo systemctl enable --now vogel-temperatur.service
    sleep 2
    systemctl is-active vogel-temperatur.service | sed "s/^/  Dienst: /"
    cat ~/BirdNET-Pi/avian/frontend/temperatur.json | sed "s/^/  /"'
}

case "${1:-status}" in
  wait) phase_wait ;;
  check) phase_check ;;
  install) phase_install ;;
  progress) phase_progress ;;
  configure) phase_configure ;;
  uplink) phase_uplink ;;
  temperatur) phase_temperatur ;;
  status) phase_status ;;
  all) phase_wait; phase_check; phase_install ;;
  *) sed -n '2,18p' "$0"; exit 1 ;;
esac
