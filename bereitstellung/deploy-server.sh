#!/usr/bin/env bash
# Bringt den Vogel-Server auf den Hetzner-Server und haelt ihn aktuell.
#
# Beim ersten Aufruf richtet er alles ein, danach spielt derselbe Aufruf
# Aenderungen ein. Gefahrlos wiederholbar.
#
#     ./deploy-server.sh 203.0.113.10
set -euo pipefail

IP="${1:?Aufruf: $0 <server-ip>}"
NUTZER="${2:-admin}"
KEY="${SSH_KEY:-$HOME/.ssh/server_key}"
KH="$HOME/.ssh/known_hosts_vogel"
ZIEL="/opt/vogel-app"
HIER="$(cd "$(dirname "$0")/.." && pwd)"

sshs() { ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KH" \
             -o ConnectTimeout=15 "$NUTZER@$IP" "$@"; }

echo "== 1/5  Docker =="
sshs 'if command -v docker >/dev/null 2>&1; then
        echo "  schon da: $(docker --version)"
      else
        sudo install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
          sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        sudo chmod a+r /etc/apt/keyrings/docker.gpg
        . /etc/os-release
        # Docker kennt fuer sehr frische Ubuntu-Ausgaben manchmal noch kein
        # eigenes Verzeichnis. Dann die letzte LTS verwenden - die Pakete sind
        # dieselben.
        ZWEIG="$VERSION_CODENAME"
        curl -fsSI "https://download.docker.com/linux/ubuntu/dists/$ZWEIG/Release" \
          | head -1 | grep -q "200" || ZWEIG="noble"
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $ZWEIG stable" | \
          sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
        sudo apt-get update -qq
        sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
             docker-buildx-plugin docker-compose-plugin
        sudo usermod -aG docker '"$NUTZER"'
        echo "  installiert: $(docker --version)"
      fi'

echo "== 2/5  Dateien uebertragen =="
sshs "sudo mkdir -p $ZIEL && sudo chown -R $NUTZER:$NUTZER $ZIEL"
# Nur was der Server braucht. Ausdruecklich NICHT: .env (Geheimnisse werden
# getrennt gesetzt), data/ (kommt per eigenem Abgleich), Zwischenstaende.
rsync -az --delete \
  --exclude '.env' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude 'data/' --exclude '.pytest_cache' --exclude '.DS_Store' \
  -e "ssh -i $KEY -o UserKnownHostsFile=$KH -o StrictHostKeyChecking=accept-new" \
  "$HIER/server/" "$NUTZER@$IP:$ZIEL/server/"
echo "  uebertragen"

echo "== 3/5  Zugangsdaten anlegen (nur beim ersten Mal) =="
sshs "cd $ZIEL/server
  if [ -f .env ]; then
    echo '  .env liegt schon vor - unveraendert gelassen'
  else
    PGPW=\$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
    TOKEN=\$(openssl rand -base64 32 | tr -d '/+=' | head -c 43)
    cat > .env <<CONF
POSTGRES_PASSWORD=\$PGPW
DATABASE_URL=postgresql+psycopg://vogel:\$PGPW@db:5432/vogel
DEVICE_TOKEN=\$TOKEN
LOG_LEVEL=info
CONF
    chmod 600 .env
    echo '  .env mit frischen Zufallswerten angelegt (Rechte 600)'
  fi"

echo "== 4/5  Starten =="
sshs "cd $ZIEL/server
  sg docker -c 'docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build' 2>&1 | tail -5"

echo "== 5/5  Pruefen =="
sleep 8
sshs "curl -fsS http://127.0.0.1:8090/healthz | head -c 200; echo"
echo
echo "Fertig. Von aussen ist noch nichts erreichbar - dafuer fehlt Caddy."
