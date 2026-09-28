#!/bin/sh
# Stellt ein Geräte-Token für den Bild-Endpunkt aus (z.B. fürs Bigme-Handy).
# Gibt das Klartext-Token EINMALIG aus - danach ist nur der Hash gespeichert.
#   ./token-ausstellen.sh "Bigme HiBreak"
set -eu
NAME="${1:?Aufruf: $0 <geraetename>}"
DATEI="/srv/login/geraete.json"

TOKEN=$(openssl rand -base64 32 | tr -d '/+=' | head -c 43)
HASH=$(printf '%s' "$TOKEN" | sha256sum | cut -d' ' -f1)

sudo test -f "$DATEI" || echo "[]" | sudo tee "$DATEI" >/dev/null
# Eintrag anhängen (per python, um das JSON sauber zu halten)
sudo python3 - "$DATEI" "$NAME" "$HASH" <<'PY'
import json, sys
datei, name, h = sys.argv[1], sys.argv[2], sys.argv[3]
liste = json.load(open(datei))
liste = [g for g in liste if g.get("name") != name]  # gleichen Namen ersetzen
liste.append({"name": name, "token_hash": h})
json.dump(liste, open(datei, "w"), ensure_ascii=False, indent=2)
PY
sudo chown www-data:www-data "$DATEI"; sudo chmod 640 "$DATEI"
echo "Token für '$NAME' ausgestellt. EINMALIG - jetzt notieren:"
echo
echo "  $TOKEN"
echo
echo "Im Handy (App) unter Token eintragen. Widerrufen: Eintrag aus $DATEI löschen."
