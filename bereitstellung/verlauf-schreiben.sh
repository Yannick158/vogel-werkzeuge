#!/bin/sh
# Schreibt alle paar Minuten eine Zeile über den Zustand der Anlage.
# Daraus wird in den Einstellungen ein Diagramm der Online-Zeit.
#
# Der Server urteilt SELBST, ohne den Pi zu fragen: "online" heisst, dass der
# letzte Datenabgleich nicht zu lange her ist. So faellt ein stummer Pi auf,
# auch wenn er selbst nichts mehr melden kann.
set -eu

VERLAUF="/srv/pi-daten/verlauf.csv"
DB="/srv/pi-daten/birds.db"
ZUSTAND="/srv/pi-daten/zustand.json"
GRENZE=1800   # bis 30 Minuten ohne Abgleich gilt als online

JETZT=$(date +%s)

if [ -f "$DB" ]; then
  ALTER=$(( JETZT - $(stat -c %Y "$DB") ))
  [ "$ALTER" -le "$GRENZE" ] && ONLINE=1 || ONLINE=0
else
  ONLINE=0
fi

LAUFZEIT=0; TEMP=0
if [ -f "$ZUSTAND" ]; then
  LAUFZEIT=$(grep -o '"laufzeit_sekunden": *[0-9]*' "$ZUSTAND" | grep -o '[0-9]*' || echo 0)
  TEMP=$(grep -o '"temperatur_celsius": *[0-9]*' "$ZUSTAND" | grep -o '[0-9]*' || echo 0)
fi

# Kopfzeile einmal anlegen
[ -f "$VERLAUF" ] || echo "epoche,online,pi_laufzeit_s,temperatur" > "$VERLAUF"
echo "$JETZT,$ONLINE,$LAUFZEIT,$TEMP" >> "$VERLAUF"

# Nicht endlos wachsen lassen: 90 Tage bei alle 10 Minuten sind ~13000 Zeilen.
# Aeltere wegschneiden (Kopfzeile behalten).
GRENZ_EPOCHE=$(( JETZT - 90*24*3600 ))
if [ "$(wc -l < "$VERLAUF")" -gt 14000 ]; then
  KOPF=$(head -1 "$VERLAUF")
  { echo "$KOPF"; awk -F, -v g="$GRENZ_EPOCHE" 'NR>1 && $1>=g' "$VERLAUF"; } > "$VERLAUF.neu"
  mv "$VERLAUF.neu" "$VERLAUF"
fi
