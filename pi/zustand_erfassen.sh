#!/bin/sh
# Erfasst den Gesundheitszustand des Pi und legt ihn als JSON ab.
#
# Die Datei wird vom Abgleich mit hochgeladen; der Server liest sie und zeigt
# bei Bedarf eine Warnung auf der Wandanzeige.
#
# WAS EINEM PI SCHADET - und was hier deshalb erfasst wird:
#   Unterspannung    Das haeufigste Problem. Ein zu schwaches Netzteil oder
#                    ein duennes Kabel laesst die Spannung unter 4,63 V fallen.
#                    Folge: stille Datenfehler, kaputte SD-Karten, Abstuerze.
#   Drosselung       Der Pi nimmt bei Hitze oder Unterspannung Takt heraus.
#                    Dann verpasst BirdNET Erkennungen, ohne dass es auffaellt.
#   Temperatur       Ab 80 Grad drosselt der Pi, ab 85 hart.
#   Speicherplatz    Ist die Karte voll, schreibt BirdNET nichts mehr.
#   SD-Karten-Fehler Eine sterbende Karte ist die haeufigste Ausfallursache
#                    ueberhaupt. Sie kuendigt sich im Kernel-Protokoll an.
#
# Gezaehlt wird ueber die Zeit, nicht nur der Momentwert: Der Nutzer will
# wissen, ob etwas "zu oft" passiert - eine einzelne Spitze ist normal.
set -eu

ZIEL="${ZIEL:-$HOME/BirdNET-Pi/zustand.json}"
ZAEHLER="${ZAEHLER:-$HOME/.vogel-zustand-zaehler}"

# --- Spannung und Drosselung -------------------------------------------
# vcgencmd liefert ein Bitfeld. Untere Bits = jetzt gerade,
# Bits ab 16 = seit dem letzten Start irgendwann aufgetreten.
ROH=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
WERT=$(printf '%d' "$ROH" 2>/dev/null || echo 0)

bit() { [ $(( (WERT >> $1) & 1 )) -eq 1 ] && echo true || echo false; }

UNTERSPANNUNG_JETZT=$(bit 0)
GEDROSSELT_JETZT=$(bit 2)
TEMPGRENZE_JETZT=$(bit 3)
UNTERSPANNUNG_JE=$(bit 16)
GEDROSSELT_JE=$(bit 18)
TEMPGRENZE_JE=$(bit 19)

# --- Haeufigkeit zaehlen ------------------------------------------------
# Bei jedem Lauf mit Unterspannung wird hochgezaehlt. So laesst sich "zu oft"
# ueberhaupt beantworten - ein Momentwert allein sagt darueber nichts.
mkdir -p "$(dirname "$ZAEHLER")"
[ -f "$ZAEHLER" ] || echo "0 0 0" > "$ZAEHLER"
read -r N_SPANNUNG N_DROSSEL N_LAEUFE < "$ZAEHLER" 2>/dev/null || { N_SPANNUNG=0; N_DROSSEL=0; N_LAEUFE=0; }
N_LAEUFE=$((N_LAEUFE + 1))
[ "$UNTERSPANNUNG_JETZT" = "true" ] && N_SPANNUNG=$((N_SPANNUNG + 1))
[ "$GEDROSSELT_JETZT" = "true" ] && N_DROSSEL=$((N_DROSSEL + 1))
echo "$N_SPANNUNG $N_DROSSEL $N_LAEUFE" > "$ZAEHLER"

# --- Temperatur ---------------------------------------------------------
TEMP=$(vcgencmd measure_temp 2>/dev/null | sed 's/[^0-9.]//g' | cut -d. -f1)
[ -n "$TEMP" ] || TEMP=0

# --- Speicherplatz ------------------------------------------------------
PLATTE_PROZENT=$(df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
PLATTE_FREI_MB=$(df -Pm / | awk 'NR==2 {print $4}')

# --- SD-Karten-Fehler ---------------------------------------------------
# Eine sterbende Karte meldet sich hier lange bevor sie ganz ausfaellt.
# grep -c gibt bei null Treffern schon "0" aus, endet aber mit Fehlercode.
# Ein angehaengtes "|| echo 0" haengt deshalb eine ZWEITE Null an und macht
# das JSON kaputt - der Fehler steckte hier zuerst drin.
SD_FEHLER=$(dmesg 2>/dev/null | grep -icE "mmc[0-9]+: (error|timeout)|I/O error.*mmcblk|EXT4-fs error") || true
[ -n "$SD_FEHLER" ] || SD_FEHLER=0

# --- BirdNET laeuft? ----------------------------------------------------
DIENSTE_OK=true
for d in birdnet_recording birdnet_analysis; do
  [ "$(systemctl is-active $d 2>/dev/null)" = "active" ] || DIENSTE_OK=false
done

# --- Letzte Erkennung ---------------------------------------------------
DB="$HOME/BirdNET-Pi/scripts/birds.db"
LETZTE=""
[ -f "$DB" ] && LETZTE=$(sqlite3 "$DB" "SELECT Date || ' ' || Time FROM detections ORDER BY Date DESC, Time DESC LIMIT 1;" 2>/dev/null || echo "")

LAUFZEIT=$(cut -d. -f1 /proc/uptime)

cat > "$ZIEL" <<JSON
{
  "erstellt": "$(date '+%Y-%m-%d %H:%M:%S')",
  "erstellt_epoche": $(date +%s),
  "rechner": "$(hostname)",
  "laufzeit_sekunden": $LAUFZEIT,
  "spannung": {
    "unterspannung_jetzt": $UNTERSPANNUNG_JETZT,
    "unterspannung_je": $UNTERSPANNUNG_JE,
    "gedrosselt_jetzt": $GEDROSSELT_JETZT,
    "gedrosselt_je": $GEDROSSELT_JE,
    "temperaturgrenze_jetzt": $TEMPGRENZE_JETZT,
    "temperaturgrenze_je": $TEMPGRENZE_JE,
    "rohwert": "$ROH"
  },
  "haeufigkeit": {
    "laeufe": $N_LAEUFE,
    "mit_unterspannung": $N_SPANNUNG,
    "mit_drosselung": $N_DROSSEL
  },
  "temperatur_celsius": $TEMP,
  "platte": { "belegt_prozent": $PLATTE_PROZENT, "frei_mb": $PLATTE_FREI_MB },
  "sd_fehler": $SD_FEHLER,
  "dienste_laufen": $DIENSTE_OK,
  "letzte_erkennung": "$LETZTE"
}
JSON
echo "Zustand geschrieben: $ZIEL"
