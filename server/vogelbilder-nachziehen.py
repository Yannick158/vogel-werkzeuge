#!/usr/bin/env python3
"""Zieht fehlende Vogel-Illustrationen nach.

Sucht Arten, die der Pi gehoert hat, fuer die aber keine Zeichnung vorliegt,
und laesst generate_one.py sie erzeugen. Gedacht fuer zwei Ausloeser:

  * vogel-vogelbild.path   feuert, sobald der Pi birds.db abgelegt hat
                           -> eine neue Art hat ihr Bild binnen Minuten
  * vogel-vogelbild.timer  alle vier Stunden als Sicherheitsnetz, falls der
                           Pi laenger weg war oder ein Lauf ausfiel

Der Knopf in der Oberflaeche bleibt bewusst gesperrt (Caddy @gesperrt):
generate.php gibt sci/com auf eine Shell-Befehlszeile, das gehoert nicht
ins Netz. Hier laeuft dasselbe Werkzeug, aber nur vom Server aus.

Der Gemini-Zugang wird aus birdnet.conf gelesen und ausschliesslich ueber die
Umgebung an den Unterprozess gereicht - nie als Befehlszeilen-Argument, wo er
in der Prozessliste und im Journal landen wuerde. Ausgegeben wird er nie.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BIRDNET = Path("/srv/avian/BirdNET-Pi")
SKRIPTE = BIRDNET / "avian" / "scripts"
ILLUS = BIRDNET / "avian" / "assets" / "illustrations"
WEBROOT = Path("/srv/avian/webroot")     # dort liegen dims.json und masks.json
ERZEUGER = SKRIPTE / "generate_one.py"
KONF = BIRDNET / "birdnet.conf"
DB = "/srv/pi-daten/birds.db"
VERSTECKT = Path("/srv/avian/verstecken/garten.json")
SPERRE = Path("/run/lock/avian-generation.lock")
FEHLERBUCH = ILLUS / ".nachziehen-fehler.json"

MAX_JE_LAUF = 3          # so bleibt ein Rueckstand nach einem Ausfall kurz
ZEITGRENZE = 600         # Sekunden je Art

# Wie lange nach einem Fehlschlag Ruhe ist. Der haeufigste Fehler ist das
# Freistellen ("cutout flood failed") - ein neuer Entwurf sieht anders aus und
# klappt oft beim zweiten Mal, deshalb zuerst kurz warten. Danach immer
# laenger, damit eine Art, die sich dauerhaft nicht zeichnen laesst, nicht
# endlos Geld kostet.
RUHEZEITEN = [30 * 60, 2 * 3600, 6 * 3600, 24 * 3600]
RUHE_DANACH = 7 * 24 * 3600

sys.path.insert(0, str(SKRIPTE))
import pregen  # noqa: E402  - liefert dieselbe slugify wie der Erzeuger


def sag(text: str) -> None:
    print(text, flush=True)


def zugang_lesen() -> str:
    """Liest den Gemini-Zugang aus birdnet.conf. Wird nie ausgegeben."""
    text = KONF.read_text(encoding="utf-8", errors="ignore")
    treffer = re.search(r'^GEMINI_API_KEY=(.*)$', text, re.M)
    return treffer.group(1).strip().strip('"').strip("'") if treffer else ""


def versteckte_arten() -> set:
    try:
        return set(json.loads(VERSTECKT.read_text(encoding="utf-8")).get("arten") or [])
    except Exception:
        return set()


def fehlerbuch_lesen() -> dict:
    try:
        return json.loads(FEHLERBUCH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fehlerbuch_schreiben(buch: dict) -> None:
    try:
        FEHLERBUCH.write_text(json.dumps(buch, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
    except OSError as e:
        sag("  Fehlerbuch nicht schreibbar: %s" % e)


def gesperrt_durch_fehler(buch: dict, slug: str) -> bool:
    e = buch.get(slug)
    if not e:
        return False
    versuche = e.get("versuche", 0)
    ruhe = RUHEZEITEN[versuche - 1] if 1 <= versuche <= len(RUHEZEITEN) else RUHE_DANACH
    return (time.time() - e.get("zuletzt", 0)) < ruhe


def fehlende_arten() -> list:
    db = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    arten = db.execute(
        "select Sci_Name, Com_Name, count(*) n from detections "
        "group by Sci_Name order by n desc").fetchall()
    db.close()
    aus = versteckte_arten()
    buch = fehlerbuch_lesen()
    offen = []
    for sci, com, n in arten:
        if sci in aus:
            continue
        slug = pregen.slugify(sci)
        vollstaendig = ((ILLUS / ("%s.png" % slug)).exists()
                        and (ILLUS / ("%s-2.png" % slug)).exists())
        if vollstaendig:
            continue
        if gesperrt_durch_fehler(buch, slug):
            continue
        offen.append((sci, com or sci, n))
    return offen


def erzeugen(sci: str, com: str, zugang: str) -> bool:
    # Die Sperrdatei liegt in /run/lock und ist nach einem Neustart weg;
    # generate_one.py oeffnet sie mit "r+" und scheitert dann.
    try:
        SPERRE.parent.mkdir(parents=True, exist_ok=True)
        SPERRE.touch(exist_ok=True)
    except OSError as e:
        sag("  Sperrdatei nicht anlegbar: %s" % e)
        return False

    umgebung = dict(os.environ)
    umgebung["GEMINI_API_KEY"] = zugang          # nur hier, nie auf der Befehlszeile
    umgebung["PYTHONUNBUFFERED"] = "1"
    try:
        lauf = subprocess.run(
            [sys.executable, str(ERZEUGER), "--sci", sci, "--com", com],
            env=umgebung, cwd=str(SKRIPTE), timeout=ZEITGRENZE,
            capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        sag("  Zeitgrenze von %ds ueberschritten" % ZEITGRENZE)
        return False

    def sauber(zeile: str) -> str:
        # Falls der Zugang je in einer Meldung auftauchen sollte.
        return zeile.replace(zugang, "<AUSGEBLENDET>") if zugang else zeile

    for zeile in (lauf.stdout or "").splitlines()[-6:]:
        sag("    %s" % sauber(zeile))
    if lauf.returncode != 0:
        for zeile in (lauf.stderr or "").splitlines()[-6:]:
            sag("    ! %s" % sauber(zeile))
        silhouetten_nachtragen(pregen.slugify(sci))
    return lauf.returncode == 0


def silhouetten_nachtragen(slug: str) -> None:
    """Traegt Silhouetten fertiger Haltungen nach, wenn der Lauf abbrach.

    generate_one.py registriert die Masken erst NACH beiden Haltungen. Scheitert
    die zweite - bei dunklen Voegeln scheitert das Freistellen regelmaessig -,
    springt die Ausnahme an der Registrierung vorbei. Die fertige erste Haltung
    liegt dann als Datei da, fehlt aber in masks.json, und die Collage kann den
    Vogel nicht setzen: er ist unsichtbar. Wiederholversuche heilen das nicht,
    sie brechen jedes Mal an derselben Stelle ab.
    (Genau so war die Dohle am 15.09.2026 online nicht zu sehen.)
    """
    try:
        eingetragen = json.loads((WEBROOT / "masks.json").read_text(encoding="utf-8"))
    except Exception as e:
        sag("    masks.json nicht lesbar: %s" % e)
        return
    offen = [n for n in (slug, slug + "-2")
             if (ILLUS / ("%s.png" % n)).exists() and n not in eingetragen]
    if not offen:
        return
    sag("    Silhouette fehlt noch fuer: %s - trage nach" % ", ".join(offen))
    lauf = subprocess.run([sys.executable, str(SKRIPTE / "build_masks.py"), "--add"] + offen,
                          cwd=str(SKRIPTE), capture_output=True, text=True, timeout=300)
    for zeile in (lauf.stdout or "").splitlines()[-3:]:
        sag("    %s" % zeile)
    if lauf.returncode != 0:
        sag("    ! Nachtragen gescheitert")


def main() -> int:
    offen = fehlende_arten()
    if not offen:
        # Der Path-Ausloeser feuert bei jedem Abgleich vom Pi, also etwa alle
        # 90 Sekunden. Im Normalfall schweigen, sonst laeuft das Journal voll.
        if os.environ.get("VOGEL_LAUT"):
            sag("nichts zu tun - jede gehoerte Art hat ihr Bild")
        return 0

    zugang = zugang_lesen()
    if not zugang:
        sag("FEHLER: kein Gemini-Zugang in birdnet.conf hinterlegt")
        return 2

    sag("%d Art(en) ohne Bild, bearbeite bis zu %d:" % (len(offen), MAX_JE_LAUF))
    buch = fehlerbuch_lesen()
    gemacht = 0
    for sci, com, n in offen[:MAX_JE_LAUF]:
        slug = pregen.slugify(sci)
        sag("  %s (%s) - %d Erkennungen" % (com, sci, n))
        begonnen = time.time()
        if erzeugen(sci, com, zugang):
            sag("  fertig in %.0fs" % (time.time() - begonnen))
            buch.pop(slug, None)
            gemacht += 1
        else:
            e = buch.get(slug, {"versuche": 0})
            e["versuche"] = e.get("versuche", 0) + 1
            e["zuletzt"] = int(time.time())
            e["art"] = sci
            buch[slug] = e
            sag("  gescheitert (Versuch %d)" % e["versuche"])
    fehlerbuch_schreiben(buch)
    sag("=== %d erzeugt, %d bleiben offen ===" % (gemacht, len(offen) - gemacht))
    return 0


if __name__ == "__main__":
    sys.exit(main())
