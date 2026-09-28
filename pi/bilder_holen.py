#!/usr/bin/env python3
"""Holt fehlende Vogel-Illustrationen vom Server auf den Pi.

Wenn BirdNET eine Art zum ersten Mal hoert, hat der Pi noch kein Bild dafuer
und zeigt in der Collage das leere Nest. Dieses Skript schliesst die Luecke:
Es vergleicht die gehoerten Arten mit den vorhandenen Bildern und laedt fehlende
vom Server nach.

Der Server entscheidet dabei selbst, woher das Bild kommt - erst die
mitgelieferte Sammlung, dann eine erzeugte Illustration, sonst ein Platzhalter.
Der Pi muss davon nichts wissen.

Nur Python-Standardbibliothek, damit auf dem Pi nichts nachzuinstallieren ist.

Aufruf:
    ./bilder_holen.py                 einmal durchlaufen
    ./bilder_holen.py --dry-run       nur zeigen, was fehlt
    ./bilder_holen.py --alle          auch vorhandene Bilder erneuern
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HEIM = os.path.expanduser("~")
DB = os.path.join(HEIM, "BirdNET-Pi", "scripts", "birds.db")
BILDER = os.path.join(HEIM, "BirdNET-Pi", "avian", "assets", "illustrations")
FRONTEND = os.path.join(HEIM, "BirdNET-Pi", "avian", "frontend")
MASKEN = os.path.join(HEIM, "BirdNET-Pi", "avian", "scripts", "build_masks.py")
PYTHON = os.path.join(HEIM, "BirdNET-Pi", "birdnet", "bin", "python3")


def konfig(pfad: str) -> dict:
    """Server-Adresse aus der Uplink-Konfiguration lesen."""
    werte = {}
    if not os.path.exists(pfad):
        return werte
    with open(pfad, encoding="utf-8") as fh:
        for zeile in fh:
            zeile = zeile.split("#", 1)[0].strip()
            if "=" not in zeile:
                continue
            k, _, v = zeile.partition("=")
            werte[k.strip()] = v.strip().strip('"').strip("'")
    return werte


def slug(sci_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sci_name.lower()).strip("-")


def gehoerte_arten() -> list[str]:
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5) as con:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT Sci_Name FROM detections ORDER BY Sci_Name")]


def hat_bild(s: str) -> bool:
    return os.path.exists(os.path.join(BILDER, f"{s}.png"))


def hole(server: str, sci: str, ziel: str, timeout: int = 120) -> bool:
    """Bild beim Server anfordern. Der erzeugt es bei Bedarf selbst."""
    url = f"{server.rstrip('/')}/api/v1/species/{slug(sci)}/image?pose=1"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as antwort:
            if antwort.status != 200:
                return False
            daten = antwort.read()
    except (urllib.error.URLError, OSError) as fehler:
        print(f"    nicht erreichbar: {fehler}")
        return False
    if len(daten) < 500 or not daten.startswith(b"\x89PNG"):
        print("    keine gueltige Bilddatei erhalten")
        return False
    # Erst vollstaendig danebenschreiben, dann umbenennen - sonst sieht die
    # Collage kurzzeitig eine halbe Datei.
    vorlaeufig = ziel + ".teil"
    with open(vorlaeufig, "wb") as fh:
        fh.write(daten)
    os.replace(vorlaeufig, ziel)
    return True


def masken_neu() -> None:
    """Silhouetten neu berechnen und die Anzeigeversion hochzaehlen."""
    if os.path.exists(MASKEN) and os.path.exists(PYTHON):
        subprocess.run([PYTHON, MASKEN], capture_output=True, timeout=600)
    apt = os.path.join(FRONTEND, "apt.js")
    if not os.path.exists(apt):
        return
    with open(apt, encoding="utf-8") as fh:
        inhalt = fh.read()
    neu, anzahl = re.subn(
        r"((?:SKETCH_VERSION|IMG_VERSION)\s*=\s*['\"])r(\d+)(['\"])",
        lambda m: f"{m.group(1)}r{int(m.group(2)) + 1}{m.group(3)}",
        inhalt,
    )
    if anzahl:
        with open(apt, "w", encoding="utf-8") as fh:
            fh.write(neu)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(HEIM, "avian-uplink", "config.toml"))
    ap.add_argument("--server", help="Server-Adresse, sonst aus der Konfiguration")
    ap.add_argument("--dry-run", action="store_true", help="nur zeigen, was fehlt")
    ap.add_argument("--alle", action="store_true", help="auch vorhandene erneuern")
    args = ap.parse_args()

    server = args.server or konfig(args.config).get("server_url", "")
    if not server:
        print("Keine Server-Adresse gefunden (server_url in config.toml).")
        return 1

    if not os.path.exists(DB):
        print(f"Datenbank nicht gefunden: {DB}")
        return 1
    os.makedirs(BILDER, exist_ok=True)

    arten = gehoerte_arten()
    offen = [a for a in arten if args.alle or not hat_bild(slug(a))]

    print(f"{len(arten)} Arten gehoert, {len(offen)} ohne Bild")
    if not offen:
        return 0
    if args.dry_run:
        for a in offen:
            print(f"  fehlt: {a}")
        return 0

    geholt = 0
    for a in offen:
        print(f"  {a} ... ", end="", flush=True)
        if hole(server, a, os.path.join(BILDER, f"{slug(a)}.png")):
            print("geholt")
            geholt += 1
        else:
            print("fehlgeschlagen")

    if geholt:
        print(f"{geholt} Bild(er) geholt, berechne Silhouetten neu ...")
        masken_neu()
        print("fertig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
