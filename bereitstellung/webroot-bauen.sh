#!/bin/sh
# Erzeugt die index.html des Webroots aus dem Original plus unseren Zusaetzen.
#
# WARUM NICHT EINFACH DIE ORIGINALDATEI AENDERN:
# Die Oberflaeche stammt aus einem fremden Projekt, das weiterentwickelt wird.
# Wer dessen Dateien anfasst, kann Updates nicht mehr einspielen - der
# Aktualisierer bricht bei veraenderten Dateien ab. Deshalb bleibt das Original
# unberuehrt, und im Webroot steht eine erzeugte Kopie mit einer zusaetzlichen
# Zeile. Nach einem Update einfach dieses Skript erneut laufen lassen.
set -eu

ORIGINAL="/srv/avian/BirdNET-Pi/avian/frontend/index.html"
ZIEL="/srv/avian/webroot/index.html"

[ -f "$ORIGINAL" ] || { echo "Original nicht gefunden: $ORIGINAL" >&2; exit 1; }

# Falls dort noch der Symlink aufs Original liegt: wegnehmen
[ -L "$ZIEL" ] && rm -f "$ZIEL"

# Unser Skript vor </body> einhaengen
python3 - "$ORIGINAL" "$ZIEL" <<'PY'
import sys, pathlib
quelle, ziel = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
html = quelle.read_text(encoding="utf-8")
marke = '<script src="/eigenes/waechter.js" defer></script>'
if marke not in html:
    if "</body>" in html:
        html = html.replace("</body>", f"  {marke}\n</body>", 1)
    else:
        html += f"\n{marke}\n"
ziel.write_text(html, encoding="utf-8")
print(f"  {ziel} erzeugt ({len(html)} Zeichen)")
PY
chmod 644 "$ZIEL"
