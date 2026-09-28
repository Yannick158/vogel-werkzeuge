#!/bin/sh
# Taegliche Sicherung der Vogeldaten auf dem Server.
#
# Gesichert wird, was sich nicht wiederbeschaffen laesst:
#   - birds.db          die Erkennungen (der Pi hat sie zwar auch, aber wenn
#                       dessen SD-Karte stirbt, ist sie hier die letzte Kopie)
#   - die Postgres-Datenbank des Servers
# NICHT gesichert werden die Illustrationen - die liegen im Projekt auf dem
# Mac und sind jederzeit neu einspielbar.
#
# Die Sicherungen bleiben auf derselben Maschine. Das schuetzt gegen
# Verseh"en und Softwarefehler, nicht gegen den Verlust des Servers -
# dafuer gibt es das taegliche Hetzner-Backup und den woechentlichen Abzug
# auf den Mac (siehe docs/SERVER-BETRIEB.md).
set -eu

ZIEL="/srv/sicherung"
HEUTE=$(date +%Y%m%d)
BEHALTEN=14

mkdir -p "$ZIEL"

# SQLite stimmig kopieren, auch waehrend geschrieben wird
if [ -f /srv/pi-daten/birds.db ]; then
  sqlite3 /srv/pi-daten/birds.db ".backup '$ZIEL/birds_$HEUTE.db'"
  gzip -f "$ZIEL/birds_$HEUTE.db"
fi

# Postgres des Servers
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q vogel-db; then
  docker exec -i vogel-db-1 pg_dump -U vogel vogel 2>/dev/null | gzip > "$ZIEL/postgres_$HEUTE.sql.gz" || true
fi

# Alte Staende wegraeumen
find "$ZIEL" -name '*.gz' -mtime "+$BEHALTEN" -delete 2>/dev/null || true

echo "$(date '+%Y-%m-%d %H:%M') gesichert: $(ls -1 $ZIEL/*_$HEUTE.* 2>/dev/null | wc -l) Datei(en), $(du -sh $ZIEL 2>/dev/null | cut -f1) gesamt"
