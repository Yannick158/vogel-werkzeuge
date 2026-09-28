#!/usr/bin/env bash
# Flasht Raspberry Pi OS auf die microSD und legt die Erstkonfiguration
# (Rechnername, Benutzer, SSH-Schluessel, WLAN, Sprache) in die Boot-Partition.
#
# Format: cloud-init (user-data + network-config). Das fruehere custom.toml gibt
# es in Raspberry Pi OS Trixie NICHT mehr: raspberrypi-sys-mods enthaelt kein
# init_config/firstboot mehr, stattdessen liest cloud-init ueber die
# NoCloud-Datasource aus /boot/firmware. Am Image selbst verifiziert.
#
# AUFRUF IM TERMINAL (sudo fragt nach dem Mac-Passwort):
#     cd "$PROJEKT/bereitstellung"
#     ./flash-sd.sh
#
# Ohne Argument sucht das Skript die SD-Karte selbst. Optional: ./flash-sd.sh disk10
#
# Probelauf ohne jede Schreiboperation (prueft Karte, Hash und Konfiguration):
#     ./flash-sd.sh --dry-run
#
# Sicherheit: schreibt ausschliesslich auf auswerfbare Wechseldatentraeger
# plausibler Groesse und verlangt eine ausdrueckliche Bestaetigung.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
ARGS=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    *) ARGS="$ARGS $a" ;;
  esac
done
# shellcheck disable=SC2086
set -- $ARGS

IMG_SHA256="acff736ca7945e3b305f07cda4abdb870910e12634991da69783611756e381b3"
IMG_URL="https://downloads.raspberrypi.org/raspios_lite_arm64/images/raspios_lite_arm64-2026-06-19/2026-06-18-raspios-trixie-arm64-lite.img.xz"
IMG_CACHED="${IMG_CACHED:-$HOME/.cache/vogel/raspios.img.xz}"
IMG="${IMG:-}"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\033[31mFEHLER: %s\033[0m\n' "$*" >&2; exit 1; }

# YAML-sicheres Zitieren: einfache Anfuehrungszeichen, enthaltene verdoppeln.
# Wichtig fuer rein numerische WLAN-Passwoerter (sonst als Zahl gelesen) und
# fuer Sonderzeichen in Passwoertern und Hashes.
yq() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"; }

# ---------------------------------------------------------------- Secrets ---
SECRETS="$DIR/secrets.env"
[ -f "$SECRETS" ] || fail "$SECRETS fehlt (Vorlage: secrets.env.example)"
# shellcheck disable=SC1090
source "$SECRETS"
for v in WIFI_SSID WIFI_PASS PI_HOSTNAME PI_USER PI_PASS; do
  [ -n "${!v:-}" ] || fail "$v ist in secrets.env leer"
done
WIFI_COUNTRY="${WIFI_COUNTRY:-DE}"
case "$PI_USER" in
  [a-z]*) : ;;
  *) fail "PI_USER muss mit einem Kleinbuchstaben beginnen (cloud-init/userconf)" ;;
esac

# ------------------------------------------------- Passwort-Hash-Werkzeug ---
# LibreSSL (/usr/bin/openssl auf macOS) kann kein -6 und meldet den Fehler
# NICHT ueber den Exit-Code. Deshalb: Kandidaten testen und Ergebnis pruefen.
hash_password() {
  local pw="$1" out="" ossl
  local candidates="/opt/homebrew/opt/openssl@3/bin/openssl /opt/homebrew/opt/openssl/bin/openssl /opt/anaconda3/bin/openssl $(command -v openssl 2>/dev/null || true)"
  for ossl in $candidates; do
    [ -n "$ossl" ] && [ -x "$ossl" ] || continue
    out=$(printf '%s' "$pw" | "$ossl" passwd -6 -stdin 2>/dev/null || true)
    case "$out" in '$6$'*) printf '%s' "$out"; return 0 ;; esac
  done
  if command -v docker >/dev/null 2>&1; then
    out=$(docker run --rm python:3.12-slim python3 -c \
      "import crypt,os; print(crypt.crypt(os.environ['P'], crypt.mksalt(crypt.METHOD_SHA512)))" \
      2>/dev/null || true)
    case "$out" in '$6$'*) printf '%s' "$out"; return 0 ;; esac
  fi
  return 1
}

# ------------------------------------------------------------------ Image ---
if [ -z "$IMG" ]; then
  if [ -f "$IMG_CACHED" ]; then IMG="$IMG_CACHED"; else IMG="$DIR/$(basename "$IMG_URL")"; fi
fi
if [ ! -f "$IMG" ]; then
  say "Lade Raspberry Pi OS herunter (rund 500 MB) ..."
  curl -L --retry 5 --retry-all-errors -o "$IMG" "$IMG_URL"
fi
say "Pruefe Image-Signatur ..."
ACTUAL=$(shasum -a 256 "$IMG" | awk '{print $1}')
[ "$ACTUAL" = "$IMG_SHA256" ] || fail "SHA256 stimmt nicht ($ACTUAL). Image loeschen und neu laden."
echo "SHA256 in Ordnung: $(basename "$IMG")"

# ------------------------------------------------------- Ziel-Disk finden ---
prop() { /usr/libexec/PlistBuddy -c "Print :$2" "$1" 2>/dev/null || echo ""; }

candidates_list() {
  # Alle ganzen, auswerfbaren, physischen Wechseldatentraeger 4-256 GB.
  # Achtung: Der eingebaute SD-Slot meldet "Internal = true" - deshalb ist
  # Ejectable/RemovableMedia das entscheidende Merkmal, nicht Internal.
  local d plist eject rem whole virt size
  for d in $(diskutil list | awk '/^\/dev\/disk[0-9]+ \(/{gsub("/dev/","");print $1}'); do
    plist=$(mktemp); diskutil info -plist "$d" > "$plist" 2>/dev/null || { rm -f "$plist"; continue; }
    eject=$(prop "$plist" Ejectable); rem=$(prop "$plist" RemovableMedia)
    whole=$(prop "$plist" WholeDisk); virt=$(prop "$plist" VirtualOrPhysical)
    size=$(prop "$plist" TotalSize); rm -f "$plist"
    [ "$whole" = "true" ] && [ "$virt" = "Physical" ] || continue
    [ "$eject" = "true" ] && [ "$rem" = "true" ] || continue
    [ -n "$size" ] && [ "$size" -ge 4000000000 ] && [ "$size" -le 256000000000 ] || continue
    echo "$d"
  done
}

DISK="${1:-}"
if [ -z "$DISK" ]; then
  # bash 3.2 auf macOS kennt kein mapfile - deshalb ueber eine Zeilenliste.
  FOUND=$(candidates_list)
  COUNT=$(printf '%s' "$FOUND" | grep -c . || true)
  if [ "$COUNT" -eq 0 ]; then
    diskutil list; fail "Keine SD-Karte gefunden. Steckt die Karte im Slot?"
  elif [ "$COUNT" -eq 1 ]; then
    DISK="$FOUND"; echo "SD-Karte automatisch erkannt: /dev/$DISK"
  else
    printf 'Mehrere Wechseldatentraeger gefunden:\n%s\n' "$FOUND"
    fail "Bitte die richtige explizit angeben, z. B.: ./flash-sd.sh $(printf '%s' "$FOUND" | head -1)"
  fi
fi
DISK="${DISK#/dev/}"

PLIST=$(mktemp); diskutil info -plist "/dev/$DISK" > "$PLIST" 2>/dev/null || fail "/dev/$DISK existiert nicht"
EJECT=$(prop "$PLIST" Ejectable); REM=$(prop "$PLIST" RemovableMedia)
WHOLE=$(prop "$PLIST" WholeDisk); VIRT=$(prop "$PLIST" VirtualOrPhysical)
SIZE=$(prop "$PLIST" TotalSize);  MEDIA=$(prop "$PLIST" MediaName); rm -f "$PLIST"
SIZE_GB=$(( ${SIZE:-0} / 1000000000 ))

[ "$WHOLE" = "true" ]    || fail "$DISK ist keine ganze Disk (Partition angegeben?)"
[ "$VIRT" = "Physical" ] || fail "$DISK ist virtuell (Disk-Image). Abbruch."
[ "$EJECT" = "true" ] && [ "$REM" = "true" ] || fail "$DISK ist kein auswerfbarer Wechseldatentraeger. Abbruch."
[ "$SIZE_GB" -ge 4 ] && [ "$SIZE_GB" -le 256 ] || fail "Groesse ${SIZE_GB} GB unplausibel fuer eine microSD."

say "Ziel: /dev/$DISK - ${SIZE_GB} GB - $MEDIA"
diskutil list "/dev/$DISK"

# ------------------------------------------------------------ Bestaetigung --
if [ "$DRY_RUN" = "1" ]; then
  say "PROBELAUF - es wird nichts geschrieben."
elif [ "${2:-}" != "--yes" ] && [ "${FLASH_YES:-}" != "1" ]; then
  printf '\n\033[31mALLE DATEN auf /dev/%s (%s GB) werden geloescht.\033[0m\n' "$DISK" "$SIZE_GB"
  printf 'Zum Fortfahren YES eintippen: '
  read -r ANSWER
  [ "$ANSWER" = "YES" ] || fail "Abgebrochen."
fi

# --------------------------------------------------------------- SSH-Key ----
KEY="$DIR/id_birdnet"
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N "" -C "vogel-app@mac" -f "$KEY" >/dev/null
  chmod 600 "$KEY"
  echo "SSH-Schluessel erzeugt: $KEY"
fi
PUBKEY=$(tr -d '\n' < "$KEY.pub")

say "Erzeuge Passwort-Hash ..."
PI_PASS_HASH=$(P="$PI_PASS" hash_password "$PI_PASS") || fail "Kein Werkzeug fuer sha512-Hashes gefunden (openssl 3 oder Docker noetig)."
echo "Hash erzeugt (${PI_PASS_HASH:0:8}...)"

XZCAT=$(command -v xzcat || echo "")
[ -n "$XZCAT" ] || fail "xzcat fehlt (brew install xz)"

if [ "$DRY_RUN" = "1" ]; then
  BOOT=$(mktemp -d)
  printf 'console=serial0,115200 root=PARTUUID=test rootfstype=ext4 fsck.repair=yes rootwait\n' > "$BOOT/cmdline.txt"
  echo "Probelauf: Konfiguration wird nach $BOOT geschrieben statt auf die Karte."
else
  # --------------------------------------------------------------- sudo -----
  say "Fuer das Schreiben auf die Karte werden Administratorrechte gebraucht."
  sudo -v || fail "sudo-Authentifizierung fehlgeschlagen."

  # -------------------------------------------------------------- Flashen ---
  say "Haenge /dev/$DISK aus ..."
  diskutil unmountDisk "/dev/$DISK"

  say "Schreibe Image auf die Karte (mehrere Minuten; Fortschritt mit Strg+T) ..."
  "$XZCAT" "$IMG" | sudo dd of="/dev/r$DISK" bs=1m
  sync

  say "Warte auf die Boot-Partition ..."
  diskutil mountDisk "/dev/$DISK" >/dev/null 2>&1 || true
  BOOT=""
  for _ in $(seq 1 30); do
    for cand in /Volumes/bootfs /Volumes/boot /Volumes/firmware; do
      [ -d "$cand" ] && BOOT="$cand" && break
    done
    [ -n "$BOOT" ] && break
    sleep 1
    diskutil mountDisk "/dev/$DISK" >/dev/null 2>&1 || true
  done
  [ -n "$BOOT" ] || fail "Boot-Partition nicht gefunden. Karte neu einstecken und Konfiguration von Hand kopieren."
  [ -f "$BOOT/meta-data" ] || say "Hinweis: meta-data fehlt auf der Boot-Partition - cloud-init koennte die Konfiguration ignorieren."
fi

# ------------------------------------------------------- cloud-init-Dateien --
# user-data: Rechnername, Benutzer, SSH. network-config: WLAN (netplan v2).
# meta-data liefert das Image bereits mit und wird NICHT angefasst.
say "Schreibe Erstkonfiguration (cloud-init) ..."

cat > "$BOOT/user-data" <<EOF
#cloud-config
# Erstkonfiguration fuer den Vogel-App-Pi. Wird beim ersten Start ausgewertet.

hostname: $(yq "$PI_HOSTNAME")
manage_etc_hosts: true

users:
  - name: $(yq "$PI_USER")
    gecos: "Vogel App"
    primary_group: $(yq "$PI_USER")
    groups: [adm, dialout, cdrom, audio, users, sudo, video, games,
             plugdev, input, gpio, spi, i2c, netdev, render, lpadmin]
    shell: /bin/bash
    lock_passwd: false
    hashed_passwd: $(yq "$PI_PASS_HASH")
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    ssh_authorized_keys:
      - $(yq "$PUBKEY")

enable_ssh: true
ssh_pwauth: true

timezone: Europe/Beispielstadt
locale: de_DE.UTF-8
keyboard:
  model: pc105
  layout: de

# Rueckfallebene: Sollte die netplan-Konfiguration beim ersten Start nicht
# greifen, traegt nmcli das WLAN nach. Ohne Bildschirm gaebe es sonst keinen
# Weg mehr auf das Geraet.
runcmd:
  - [ sh, -c, "sleep 45; if ! nmcli -t -f STATE general | grep -q '^connected'; then nmcli device wifi connect $(yq "$WIFI_SSID") password $(yq "$WIFI_PASS") ifname wlan0 || nmcli connection add type wifi con-name vogel-wlan ifname wlan0 ssid $(yq "$WIFI_SSID") wifi-sec.key-mgmt wpa-psk wifi-sec.psk $(yq "$WIFI_PASS"); fi" ]
EOF

cat > "$BOOT/network-config" <<EOF
version: 2
wifis:
  wlan0:
    dhcp4: true
    optional: true
    access-points:
      $(yq "$WIFI_SSID"):
        password: $(yq "$WIFI_PASS")
EOF

# WLAN-Regulierungsdomaene: netplans regulatory-domain wirkt beim ersten Start
# noch nicht, deshalb wie raspi-config ueber die Kernel-Kommandozeile setzen.
if [ -f "$BOOT/cmdline.txt" ]; then
  CMDLINE=$(tr -d '\n' < "$BOOT/cmdline.txt" | sed "s/[[:space:]]*cfg80211.ieee80211_regdom=[^ ]*//g")
  printf '%s cfg80211.ieee80211_regdom=%s\n' "$CMDLINE" "$WIFI_COUNTRY" > "$BOOT/cmdline.txt"
fi

# Zweite Absicherung fuer SSH ueber sshswitch, falls cloud-init scheitert.
touch "$BOOT/ssh"

# Gegenprobe: gueltiges YAML und alle Pflichtfelder korrekt gesetzt?
python3 "$DIR/check-cloudinit.py" "$BOOT/user-data" "$BOOT/network-config" \
  || fail "Erstkonfiguration ist fehlerhaft - Karte NICHT verwenden."

if [ "$DRY_RUN" = "1" ]; then
  say "Probelauf erfolgreich - alle Pruefungen bestanden, nichts geschrieben."
  if [ "${KEEP_TMP:-}" = "1" ]; then
    echo "Erzeugte Dateien bleiben zur Ansicht in: $BOOT"
  else
    echo "Erzeugte Dateien lagen in: $BOOT"
    rm -rf "$BOOT"
  fi
  exit 0
fi

sync
diskutil eject "/dev/$DISK" >/dev/null 2>&1 || diskutil unmountDisk "/dev/$DISK" >/dev/null 2>&1 || true

say "Fertig."
cat <<EOF
Naechste Schritte:
  1. Karte in den Raspberry Pi stecken, Strom anschliessen.
  2. Erster Start dauert 2-4 Minuten (cloud-init richtet alles ein).
  3. Danach uebernimmt Claude: ./setup-pi.sh wait && ./setup-pi.sh check

  Zugang:  ssh -i $KEY $PI_USER@$PI_HOSTNAME.local
  WLAN:    $WIFI_SSID
EOF
