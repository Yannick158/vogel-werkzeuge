#!/bin/bash
# Holt die Artenportraets nach - eines nach dem anderen, mit Verschnaufpause.
#
# Der Server hat EINEN Kern. Ein Video braucht gut eine Minute Vollast. Damit
# die Weboberflaeche waehrenddessen fluessig bleibt, laeuft der Bau mit
# niedriger Prioritaet (nice) und legt zwischen zwei Videos eine Pause ein.
# Steigt die Last trotzdem, wird laenger gewartet statt weitergemacht.
set -u
PAUSE=45
LAST_GRENZE=2.5
LOG=/srv/avian/wochenschau/nachholen.log

echo "=== Nachholen gestartet $(date '+%F %T') ===" >> "$LOG"
while true; do
  offen=$(python3 - <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('e', '/srv/avian/wochenschau/erzeugen-art.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
z = m.zugelassene_arten(); v = m.verzeichnis_lesen()
print(len([s for s in z if s not in v]))
PY
)
  if [ "${offen:-0}" -le 0 ]; then
    echo "$(date '+%F %T') alle Arten haben ein Video - fertig" >> "$LOG"
    break
  fi

  last=$(cut -d' ' -f1 /proc/loadavg)
  if [ "$(echo "$last > $LAST_GRENZE" | bc -l)" = "1" ]; then
    echo "$(date '+%F %T') Last $last zu hoch, warte" >> "$LOG"
    sleep 120
    continue
  fi

  echo "$(date '+%F %T') noch $offen offen, baue naechstes (Last $last)" >> "$LOG"
  nice -n 15 ionice -c 3 python3 /srv/avian/wochenschau/erzeugen-art.py >> "$LOG" 2>&1
  if [ $? -ne 0 ]; then
    echo "$(date '+%F %T') Fehler - warte laenger und versuche weiter" >> "$LOG"
    sleep 300
  fi
  sleep "$PAUSE"
done
echo "=== Nachholen beendet $(date '+%F %T') ===" >> "$LOG"
