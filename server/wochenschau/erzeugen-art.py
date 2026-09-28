#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Baut das Portraetvideo einer Vogelart und veroeffentlicht es.

Ohne Argument sucht es sich selbst die naechste Art ohne Video - so kann ein
Zeitgeber nach und nach den Bestand aufarbeiten, ohne je lange zu blockieren.
Ein Video je Lauf reicht; neue Arten kommen ohnehin selten dazu.
"""
import html, json, os, re, shutil, sqlite3, subprocess, sys, traceback

BASIS  = '/srv/avian/wochenschau'
ZIEL   = '/srv/avian/eigenes/artvideos'
TON    = BASIS + '/ton-art'
STIMME = os.environ.get('VOGEL_STIMME', 'thorsten')
PY_VENV = BASIS + '/venv/bin/python'
# MIN_KONF_B liegt hier bei 0.75 und damit NIEDRIGER als in auswahl.py (0.80).
# Absicht: Ein Artenportraet holt man sich selbst - man tippt neben dem Foto
# auf den Knopf, weil man diese Art sehen will. Die Wochenschau dagegen legt
# einem ihre Auswahl von sich aus vor; dort waere eine Fehlerkennung
# aufdringlicher. Deshalb darf die Portraetliste grosszuegiger sein.
# Am 10.09.2026 von 0.80 auf 0.75 gesenkt - sonst fehlten Arten wie das
# Rotkehlchen, das mit 0.79 um einen Hundertstel scheiterte.
MIN_KONF_A, MIN_ANZ_A, MIN_KONF_B = 0.75, 10, 0.75


def entschaerfen(t):
    if not isinstance(t, str):
        return t
    return html.unescape(re.sub(r'<[^>]*>', '', t)).replace('<', '').replace('>', '').strip()


def slug(sci):
    return sci.lower().strip().replace(' ', '-')


def lauf(befehl, name, ausgabe=None):
    print(f'--- {name}', flush=True)
    r = subprocess.run(befehl, capture_output=True, text=True, timeout=1800)
    if r.stderr.strip():
        for z in r.stderr.strip().splitlines()[-4:]:
            print(f'    {z}', flush=True)
    if r.returncode != 0:
        raise RuntimeError(f'{name} fehlgeschlagen (Code {r.returncode})')
    if ausgabe:
        open(ausgabe, 'w', encoding='utf-8').write(r.stdout)
    return r.stdout


def zugelassene_arten():
    """10x ab 75 % oder einmal ab 75 %, und nichts, was als falsch markiert ist.

    Die zweite Grenze ist bewusst niedriger als in auswahl.py - siehe die
    Begruendung bei MIN_KONF_B oben.
    """
    try:
        hide = json.load(open('/srv/avian/verstecken/garten.json', encoding='utf-8'))
    except Exception:
        hide = {}
    weg_art = set(hide.get('arten') or [])
    weg_auf = set(hide.get('aufnahmen') or [])
    con = sqlite3.connect('file:/srv/pi-daten/birds.db?mode=ro', uri=True)
    daten = {}
    for sci, com, k, datei in con.execute(
            'SELECT Sci_Name, Com_Name, Confidence, File_Name FROM detections'):
        if datei in weg_auf or sci in weg_art:
            continue
        d = daten.setdefault(sci, {'com': com, 'ab75': 0, 'best': 0.0, 'n': 0})
        d['n'] += 1
        d['best'] = max(d['best'], k)
        if k >= MIN_KONF_A:
            d['ab75'] += 1
    return {s: d for s, d in daten.items()
            if d['ab75'] >= MIN_ANZ_A or d['best'] >= MIN_KONF_B}


def verzeichnis_lesen():
    p = f'{ZIEL}/videos.json'
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return {}


def main():
    os.makedirs(ZIEL, exist_ok=True)
    os.makedirs(TON, exist_ok=True)
    vorhanden = verzeichnis_lesen()

    sci = sys.argv[1] if len(sys.argv) > 1 else None
    if not sci:
        offen = [s for s in zugelassene_arten() if s not in vorhanden]
        if not offen:
            print('Alle zugelassenen Arten haben ein Video.', flush=True)
            return 0
        # Die haeufigste zuerst - die schaut man am ehesten an
        zug = zugelassene_arten()
        sci = max(offen, key=lambda s: zug[s]['n'])
    print(f'=== Artenportraet {sci} ===', flush=True)

    db = f'{BASIS}/drehbuch-art-aktuell.json'
    lauf(['python3', f'{BASIS}/drehbuch-art.py', sci], 'Drehbuch', db)
    drehbuch = json.load(open(db, encoding='utf-8'))
    if not drehbuch.get('szenen'):
        raise RuntimeError('Drehbuch ohne Szenen')
    print(f'    {len(drehbuch["szenen"])} Szenen', flush=True)

    for alt in os.listdir(TON):
        if alt.endswith('.wav'):
            os.remove(f'{TON}/{alt}')
    lauf([PY_VENV, f'{BASIS}/sprecher2.py', db, STIMME, TON], 'Stimme')

    name = slug(sci) + '.mp4'
    video = f'{ZIEL}/{name}'
    lauf(['python3', f'{BASIS}/film2.py', STIMME, db, video, TON], 'Bild')
    if not os.path.isfile(video) or os.path.getsize(video) < 100_000:
        raise RuntimeError('Video fehlt oder ist verdaechtig klein')
    os.chmod(video, 0o644)

    vorhanden[sci] = {
        'datei': name,
        # Baustand als Versionskennung. Der Dateiname bleibt beim Neubau
        # gleich - ohne diesen Stempel liefert der Browser ewig seine alte
        # Kopie aus, und Aenderungen am Drehbuch kommen nie an.
        'stand': int(os.path.getmtime(video)),
        'name': entschaerfen(drehbuch.get('titel', '')) or drehbuch['messdaten']['name'],
        'kernsatz': entschaerfen(drehbuch.get('kernsatz', '')),
        'szenen': len(drehbuch['szenen']),
        'quellen': {entschaerfen(k): v for k, v in (drehbuch.get('quellen') or {}).items()
                    if isinstance(v, str) and v.startswith('https://de.wikipedia.org/')},
    }
    tmp = f'{ZIEL}/.videos.tmp.json'
    json.dump(vorhanden, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    os.replace(tmp, f'{ZIEL}/videos.json')
    os.chmod(f'{ZIEL}/videos.json', 0o644)
    print(f'    veroeffentlicht: {video}', flush=True)
    print(f'=== fertig ({len(vorhanden)} Arten haben ein Video) ===', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f'FEHLER: {e}', file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)
