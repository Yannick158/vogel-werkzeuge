#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Erzeugt die Wochenschau und veroeffentlicht sie. Laeuft sonntags per Timer.

Reihenfolge: Drehbuch -> Stimme -> Bild -> veroeffentlichen -> Verlauf merken.
Der Verlauf wird ZULETZT fortgeschrieben - erst wenn das Video wirklich fertig
ist. Sonst waere eine Art als "schon gezeigt" vermerkt, obwohl der Lauf
abgebrochen ist, und sie kaeme monatelang nicht mehr vor.

Schlaegt ein Schritt fehl, bleibt die letzte Sendung stehen. Lieber die Ausgabe
der Vorwoche als eine kaputte Datei auf der Wohnzimmerwand.
"""
import html, json, os, re, shutil, subprocess, sys, traceback
from datetime import date


def entschaerfen(text):
    """Der Drehbuchtext stammt von einem Sprachmodell, das Wikipedia-Text im
    Prompt hatte. Ein manipulierter Artikel koennte darueber Auszeichnungen
    einschleusen, die die Anzeigeseite spaeter einbauen wuerde. Hier wird alles
    entfernt, was wie Auszeichnung aussieht - die Seite selbst schuetzt sich
    zusaetzlich, aber gespeichert wird gar nicht erst etwas Gefaehrliches."""
    if not isinstance(text, str):
        return text
    ohne = re.sub(r'<[^>]*>', '', text)
    return html.unescape(ohne).replace('<', '').replace('>', '').strip()

BASIS   = '/srv/avian/wochenschau'
ZIEL    = '/srv/avian/eigenes/wochenschau'
STIMME  = os.environ.get('VOGEL_STIMME', 'thorsten')
PY_VENV = f'{BASIS}/venv/bin/python'


def lauf(befehl, name, ausgabe=None):
    print(f'--- {name}', flush=True)
    r = subprocess.run(befehl, capture_output=True, text=True, timeout=1800)
    if r.stderr.strip():
        for z in r.stderr.strip().splitlines()[-6:]:
            print(f'    {z}', flush=True)
    if r.returncode != 0:
        raise RuntimeError(f'{name} fehlgeschlagen (Code {r.returncode})')
    if ausgabe:
        with open(ausgabe, 'w', encoding='utf-8') as f:
            f.write(r.stdout)
    return r.stdout


def main():
    kw = date.today().strftime('%Y-W%W')
    print(f'=== Vogelwoche {kw} ===', flush=True)

    drehbuch_datei = f'{BASIS}/drehbuch-aktuell.json'
    lauf(['python3', f'{BASIS}/drehbuch.py'], 'Drehbuch', drehbuch_datei)
    drehbuch = json.load(open(drehbuch_datei, encoding='utf-8'))
    if not drehbuch.get('szenen'):
        raise RuntimeError('Drehbuch ohne Szenen')
    print(f'    {len(drehbuch["szenen"])} Szenen', flush=True)

    for alt in os.listdir(f'{BASIS}/ton'):
        if alt.endswith('.wav'):
            os.remove(f'{BASIS}/ton/{alt}')
    lauf([PY_VENV, f'{BASIS}/sprecher2.py', drehbuch_datei, STIMME, f'{BASIS}/ton'], 'Stimme')
    lauf(['python3', f'{BASIS}/film2.py', STIMME], 'Bild')

    quelle = f'{BASIS}/vogelwoche.mp4'
    if not os.path.isfile(quelle) or os.path.getsize(quelle) < 200_000:
        raise RuntimeError('Video fehlt oder ist verdaechtig klein')

    # --- Veroeffentlichen ---
    os.makedirs(ZIEL, exist_ok=True)
    folge = f'{ZIEL}/{kw}.mp4'
    shutil.copy2(quelle, folge)
    tmp = f'{ZIEL}/.aktuell.tmp.mp4'
    shutil.copy2(quelle, tmp)
    os.replace(tmp, f'{ZIEL}/aktuell.mp4')          # atomar tauschen

    verzeichnis = f'{ZIEL}/folgen.json'
    folgen = []
    if os.path.isfile(verzeichnis):
        try:
            folgen = json.load(open(verzeichnis, encoding='utf-8'))
        except Exception:
            folgen = []
    folgen = [f for f in folgen if f.get('kw') != kw]
    folgen.insert(0, {
        'kw': kw, 'datum': str(date.today()),
        'datei': f'{kw}.mp4',
        'stand': int(os.path.getmtime(folge)),
        'titel': entschaerfen(drehbuch.get('titel', 'Die Vogelwoche')),
        'kernsatz': entschaerfen(drehbuch.get('kernsatz', '')),
        'text': entschaerfen(' '.join(s['text'] for s in drehbuch['szenen'])),
        'szenen': len(drehbuch['szenen']),
        # Nur echte Wikipedia-Adressen durchlassen - sonst koennte eine
        # erfundene Quelle zu einem javascript: Verweis werden.
        'quellen': {entschaerfen(k): v for k, v in (drehbuch.get('quellen') or {}).items()
                    if isinstance(v, str) and v.startswith('https://de.wikipedia.org/')},
        'sekunden': None,
    })
    with open(verzeichnis, 'w', encoding='utf-8') as f:
        json.dump(folgen[:52], f, ensure_ascii=False, indent=1)

    for p in (folge, f'{ZIEL}/aktuell.mp4', verzeichnis):
        os.chmod(p, 0o644)

    # Alte Folgen aufraeumen - ein Jahr reicht, die Platte ist nicht riesig.
    # Verglichen wird gegen die DATEINAMEN im Verzeichnis, nicht gegen die
    # Kalenderwochen-Kennung: die Monatsfolgen heissen "monat-2026-09.mp4" bei
    # der Kennung "M2026-09", die Namen passen also nie zusammen. Vorher hat
    # dieser Lauf jedes Monatsvideo geloescht.
    behalten = {f.get('datei') for f in folgen[:52]} | {'aktuell.mp4'}
    for datei in sorted(os.listdir(ZIEL)):
        if datei.endswith('.mp4') and datei not in behalten:
            os.remove(f'{ZIEL}/{datei}')

    print(f'    veroeffentlicht: {folge}', flush=True)

    # --- Verlauf ZULETZT, erst wenn wirklich alles stand ---
    lauf(['python3', f'{BASIS}/auswahl.py', '--merken'], 'Verlauf')

    # --- Bescheid geben ---
    try:
        # pywebpush liegt in der PUSH-Umgebung, nicht in unserer.
        subprocess.run(['/srv/avian/push/venv/bin/python', f'{BASIS}/melden.py', kw],
                       timeout=120, check=False)
    except Exception as e:
        print(f'    Benachrichtigung fehlgeschlagen: {e}', flush=True)

    print('=== fertig ===', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'FEHLER: {e}', file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)
