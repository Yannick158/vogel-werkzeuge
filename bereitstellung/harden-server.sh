#!/usr/bin/env bash
# Grundabsicherung eines frischen Hetzner-Servers.
#
# Laesst sich gefahrlos mehrfach ausfuehren.
#
# REIHENFOLGE IST WICHTIG: Der Zugang als neuer Benutzer wird GEPRUEFT, bevor
# der Root-Zugang zufaellt. Andersherum sperrt man sich bei einem Fehler
# selbst aus - und kommt dann nur noch ueber die Web-Konsole von Hetzner
# hinein, wofuer man das Notfall-Passwort braucht.
#
# Aufruf vom Mac:
#     ./harden-server.sh 203.0.113.10
set -euo pipefail

IP="${1:?Aufruf: $0 <server-ip> [benutzer]}"
NUTZER="${2:-admin}"
# fail2ban fuer sshd - Beispielwerte, an den eigenen Server anpassen
F2B_VERSUCHE="${F2B_VERSUCHE:-5}"
F2B_FENSTER="${F2B_FENSTER:-600}"
F2B_SPERRE="${F2B_SPERRE:-3600}"
KEY="${SSH_KEY:-$HOME/.ssh/server_key}"
KH="$HOME/.ssh/known_hosts_vogel"

ssh_nutzer() { ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KH" \
                   -o ConnectTimeout=15 -o BatchMode=yes "$NUTZER"@"$IP" "$@"; }
_ssh_root_direkt() { ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KH" \
                 -o ConnectTimeout=15 -o BatchMode=yes root@"$IP" "$@"; }

# Einmal zu Beginn feststellen, welcher Weg offensteht - und ihn danach
# beibehalten. Ein zweiter Lauf darf NICHT erneut als root anklopfen: Nach der
# Haertung ist der Zugang gesperrt, und jeder Fehlversuch zaehlt bei fail2ban.
# Nach ein paar davon sperrt der Server die eigene Arbeitsmaschine aus.
if _ssh_root_direkt true 2>/dev/null; then
  WEG="root"
  echo "   (Zugang als root - Server noch ungehaertet)"
elif ssh_nutzer true 2>/dev/null; then
  WEG="nutzer"
  echo "   (Zugang als $NUTZER - Haertung lief hier schon)"
else
  echo "Kein Zugang - weder als root noch als $NUTZER." >&2
  echo "Moeglich: fail2ban hat diese Adresse gesperrt (Standard 10 Minuten)." >&2
  echo "Pruefen mit:  nc -z $IP 22  - und dann abwarten, NICHT weiter anmelden." >&2
  exit 1
fi

# Fuehrt als root aus, solange das geht - danach als Benutzer mit sudo.
ssh_root() {
  if [ "$WEG" = "root" ]; then _ssh_root_direkt "$@"
  else ssh_nutzer "sudo bash -c $(printf %q "$*")"; fi
}

echo "== 1/6  System aktualisieren =="
ssh_root 'export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get upgrade -y -qq
  apt-get install -y -qq ca-certificates curl gnupg ufw fail2ban unattended-upgrades apt-listchanges
  echo "  fertig"'

echo "== 2/6  Benutzer $NUTZER anlegen =="
ssh_root "
  if ! id -u $NUTZER >/dev/null 2>&1; then
    adduser --disabled-password --gecos '' $NUTZER
    usermod -aG sudo $NUTZER
  fi
  # Schluessel vom Root uebernehmen, damit der Zugang sofort steht
  mkdir -p /home/$NUTZER/.ssh
  cp /root/.ssh/authorized_keys /home/$NUTZER/.ssh/authorized_keys
  chown -R $NUTZER:$NUTZER /home/$NUTZER/.ssh
  chmod 700 /home/$NUTZER/.ssh; chmod 600 /home/$NUTZER/.ssh/authorized_keys
  # sudo ohne Passwortabfrage: der Zugang haengt ohnehin am Schluessel, und
  # das Konto hat gar kein Passwort, mit dem man sich ausweisen koennte.
  echo '$NUTZER ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-$NUTZER
  chmod 440 /etc/sudoers.d/90-$NUTZER
  echo '  angelegt'"

echo "== 3/6  Zugang als $NUTZER PRUEFEN (vor dem Sperren von root) =="
if ssh_nutzer 'sudo -n true && echo "  Anmeldung und sudo funktionieren"'; then
  echo "  Pruefung bestanden"
else
  echo "  FEHLGESCHLAGEN - root bleibt offen, damit du nicht ausgesperrt wirst." >&2
  exit 1
fi

echo "== 4/6  SSH haerten =="
ssh_root 'cat > /etc/ssh/sshd_config.d/99-haertung.conf <<CONF
# Nur noch Schluessel, kein Passwort, kein direkter Root-Zugang.
PasswordAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
CONF
  sshd -t && systemctl restart ssh && echo "  SSH neu gestartet"'

# Ab hier ist der Root-Zugang gesperrt (Schritt 4). Alles Weitere laeuft
# ueber den neuen Benutzer mit sudo.
echo "== 5/6  Firewall und fail2ban =="
# Die eigene Adresse so ermitteln, WIE DER SERVER SIE SIEHT. Ein Dienst wie
# ifconfig.me liefert die Adresse der HTTP-Verbindung - und die laeuft auf
# einem Mac gern ueber IPv6, waehrend SSH zu einer IPv4-Adresse ueber IPv4
# geht. Die Ausnahme haette dann die falsche Adresse enthalten und waere
# wirkungslos gewesen.
EIGENE_IP="$(ssh_nutzer 'echo "$SSH_CLIENT"' 2>/dev/null | cut -d" " -f1 | tr -d "\r\n")"
if [ -n "$EIGENE_IP" ]; then
  echo "  der Server sieht dich als $EIGENE_IP (wird von fail2ban verschont)"
else
  echo "  eigene Adresse nicht ermittelbar - fail2ban kann dich aussperren"
fi
ssh_nutzer 'sudo ufw --force reset >/dev/null 2>&1
  sudo ufw default deny incoming >/dev/null
  sudo ufw default allow outgoing >/dev/null
  sudo ufw allow 22/tcp  >/dev/null
  sudo ufw allow 80/tcp  >/dev/null
  sudo ufw allow 443/tcp >/dev/null
  sudo ufw --force enable >/dev/null
  sudo tee /etc/fail2ban/jail.local >/dev/null <<CONF
[DEFAULT]
# Die eigene Arbeitsadresse nie sperren. Ohne das sperrt ein misslungener
# Anmeldeversuch die Maschine aus, von der aus man den Server verwaltet -
# genau das ist beim ersten Aufsetzen passiert.
ignoreip = 127.0.0.1/8 ::1 '"$EIGENE_IP"'

[sshd]
enabled = true
maxretry = $F2B_VERSUCHE
findtime = $F2B_FENSTER
bantime = $F2B_SPERRE
CONF
  sudo systemctl enable --now fail2ban >/dev/null 2>&1
  sudo systemctl restart fail2ban
  echo "  Firewall aktiv, fail2ban laeuft"'

echo "== 6/6  Automatische Sicherheitsupdates =="
ssh_nutzer 'sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<CONF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
  sudo tee /etc/apt/apt.conf.d/51unattended-upgrades-local >/dev/null <<CONF
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
CONF
  sudo systemctl enable --now unattended-upgrades >/dev/null 2>&1
  echo "  eingerichtet (Neustart NICHT automatisch - der bleibt deine Entscheidung)"'

echo
echo "Fertig. Ab jetzt: ssh -i $KEY $NUTZER@$IP"

# ---------------------------------------------------------------------------
# WENN DU DICH AUSGESPERRT HAST
#
# Symptom: "Connection refused" auf Port 22, aber der Server antwortet auf ping.
# Ursache: fail2ban hat die eigene Adresse gesperrt (Standard: 10 Minuten).
#
# Erst abwarten und dabei NICHT weiter anmelden - jeder Versuch verlaengert die
# Sperre. Ob der Port wieder offen ist, prueft man ohne Anmeldung:
#     nc -z <ip> 22
#
# Hilft das Warten nicht, geht es nur ueber die Web-Konsole von Hetzner
# (Cloud Console -> Server -> "Console"). Dort anmelden mit root und dem
# Notfall-Passwort aus "Rescue -> Reset root password", dann:
#     fail2ban-client status sshd          # zeigt gesperrte Adressen
#     fail2ban-client unban --all          # alle entsperren
#     systemctl status ssh                 # laeuft der Dienst ueberhaupt?
# ---------------------------------------------------------------------------
