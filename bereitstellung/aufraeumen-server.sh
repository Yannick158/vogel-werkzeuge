#!/bin/sh
# Raeumt alte Aufnahmen auf dem Server weg.
#
# Der Pi darf auf dem Server nichts mehr loeschen (-no-del in seiner
# authorized_keys) - sonst koennte ein uebernommener Pi den ganzen
# Datenbestand leeren. Aufgeraeumt wird deshalb hier, auf dem Server selbst.
#
# Geloescht werden nur Aufnahmen und Spektrogramme aelter als die
# Aufbewahrungsfrist. birds.db bleibt IMMER unangetastet - sie ist die
# Datengrundlage der ganzen Anzeige.
set -eu

VERZEICHNIS="/srv/pi-daten/Extracted"
TAGE="${TAGE:-90}"

[ -d "$VERZEICHNIS" ] || exit 0

vorher=$(du -sm "$VERZEICHNIS" 2>/dev/null | cut -f1)

# Nur Mediendateien, nur aelter als die Frist. Ausdruecklich kein -delete auf
# Verzeichnisse und niemals ausserhalb von Extracted.
find "$VERZEICHNIS" -type f \( -name '*.mp3' -o -name '*.png' \) -mtime "+$TAGE" -delete 2>/dev/null || true
# Leere Tagesordner hinterher wegraeumen
find "$VERZEICHNIS" -mindepth 1 -type d -empty -delete 2>/dev/null || true

nachher=$(du -sm "$VERZEICHNIS" 2>/dev/null | cut -f1)
echo "$(date '+%Y-%m-%d %H:%M') Aufraeumen: ${vorher}MB -> ${nachher}MB (Frist ${TAGE} Tage)"
