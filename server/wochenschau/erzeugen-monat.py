#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Erzeugt das lange Monatsvideo und veroeffentlicht es.

Gleicher Aufbau wie die Wochenschau, nur mit dem Monatsdrehbuch und dem
Vormonat als Zeitraum. Laeuft am Monatsersten und blickt zurueck.
"""
import html, json, os, re, shutil, subprocess, sys, traceback
from datetime import date

BASIS   = '/srv/avian/wochenschau'
ZIEL    = '/srv/avian/eigenes/wochenschau'
STIMME  = os.environ.get('VOGEL_STIMME', 'thorsten')
PY_VENV = f'{BASIS}/venv/bin/python'


def entschaerfen(text):
    """Wie in der Wochenschau: Auszeichnungen aus Modelltext entfernen, bevor
    er gespeichert und spaeter angezeigt wird."""
    if not isinstance(text, str):
        return text
    return html.unescape(re.sub(r'<[^>]*>', '', text)).replace('<', '').replace('>', '').strip()


def lauf(befehl, name, ausgabe=None):
    print(f'--- {name}', flush=True)
    r = subprocess.run(befehl, capture_output=True, text=True, timeout=3600)
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
    heute = date.today()
    # Am Monatsersten blicken wir auf den Vormonat zurueck
    ziel_monat = sys.argv[1] if len(sys.argv) > 1 else (
        date(heute.year - (heute.month == 1), (heute.month - 2) % 12 + 1, 1).strftime('%Y-%m')
        if heute.day <= 3 else heute.strftime('%Y-%m'))
    print(f'=== Vogelmonat {ziel_monat} ===', flush=True)

    datei = f'{BASIS}/drehbuch-monat-aktuell.json'
    lauf(['python3', f'{BASIS}/drehbuch-monat.py', ziel_monat], 'Drehbuch', datei)
    drehbuch = json.load(open(datei, encoding='utf-8'))
    if not drehbuch.get('szenen'):
        raise RuntimeError('Drehbuch ohne Szenen')
    dl = drehbuch.get('datenlage', {})
    print(f'    {len(drehbuch["szenen"])} Szenen, Bogen belastbar: '
          f'{dl.get("bogen_belastbar")}', flush=True)

    for alt in os.listdir(f'{BASIS}/ton'):
        if alt.endswith('.wav'):
            os.remove(f'{BASIS}/ton/{alt}')
    lauf([PY_VENV, f'{BASIS}/sprecher2.py', datei, STIMME, f'{BASIS}/ton'], 'Stimme')

    # --- Hoehepunkt-Einstellung bei Veo bestellen, falls gewuenscht ---
    # Kostenpflichtig, deshalb ausdruecklich einzuschalten. Ohne die Umgebungs-
    # variable entsteht das Video vollstaendig aus den eigenen Zeichnungen.
    veo_clip = f'{BASIS}/veo-held.mp4'
    if os.environ.get('VOGEL_VEO') == '1':
        spec = next((s['bild'] for s in drehbuch['szenen']
                     if s.get('bild', '').startswith('veo:')), None)
        if spec:
            rest = spec.split(':', 1)[1]
            sci, _, motion = rest.partition('|')
            slug = sci.lower().strip().replace(' ', '-')
            print(f'--- Veo: {sci.strip()}', flush=True)
            r = subprocess.run(['python3', f'{BASIS}/veo-held.py', slug,
                                motion.strip() or 'the bird moves gently', '8'],
                               capture_output=True, text=True, timeout=1200)
            for zeile in r.stderr.strip().splitlines()[-4:]:
                print(f'    {zeile}', flush=True)
            if r.returncode != 0:
                print('    Veo fehlgeschlagen - die Szene wird gezeichnet', flush=True)
    elif os.environ.get('VOGEL_VEO') == 'behalten' and os.path.isfile(veo_clip):
        # Erneuter Lauf mit dem schon bezahlten Clip - aber nur, wenn er
        # wirklich dieselbe Art zeigt. Sonst redet die Stimme ueber den einen
        # Vogel, waehrend ein anderer im Bild steht.
        spec = next((s['bild'] for s in drehbuch['szenen']
                     if s.get('bild', '').startswith('veo:')), '')
        will = spec.split(':', 1)[1].split('|')[0].lower().strip().replace(' ', '-')
        try:
            hat = json.load(open(f'{BASIS}/veo-held.json', encoding='utf-8'))['slug']
        except Exception:
            hat = None
        if hat and hat == will:
            print(f'--- Veo: vorhandenen Clip weiterverwenden ({hat})', flush=True)
        else:
            print(f'--- Veo: Clip ({hat}) passt nicht zur Art ({will}) - wird '
                  'gezeichnet statt falsch gezeigt', flush=True)
            os.remove(veo_clip)
    elif os.path.isfile(veo_clip):
        # Sonst nicht versehentlich einen alten Clip einer anderen Art nehmen
        os.remove(veo_clip)

    # film2.py liest drehbuch-aktuell.json - fuer den Monat zeigen wir es dorthin
    shutil.copy2(datei, f'{BASIS}/drehbuch-aktuell.json')
    lauf(['python3', f'{BASIS}/film2.py', STIMME], 'Bild')

    quelle = f'{BASIS}/vogelwoche.mp4'
    if not os.path.isfile(quelle) or os.path.getsize(quelle) < 500_000:
        raise RuntimeError('Video fehlt oder ist verdaechtig klein')

    os.makedirs(ZIEL, exist_ok=True)
    name = f'monat-{ziel_monat}.mp4'
    shutil.copy2(quelle, f'{ZIEL}/{name}')
    os.chmod(f'{ZIEL}/{name}', 0o644)

    verzeichnis = f'{ZIEL}/folgen.json'
    folgen = []
    if os.path.isfile(verzeichnis):
        try:
            folgen = json.load(open(verzeichnis, encoding='utf-8'))
        except Exception:
            folgen = []
    folgen = [f for f in folgen if f.get('datei') != name]
    folgen.insert(0, {
        'kw': f'M{ziel_monat}', 'typ': 'monat', 'datum': str(heute),
        'datei': name,
        'stand': int(os.path.getmtime(f'{ZIEL}/{name}')),
        'titel': entschaerfen(drehbuch.get('titel', 'Der Vogelmonat')),
        'kernsatz': entschaerfen(drehbuch.get('kernsatz', '')),
        'text': entschaerfen(' '.join(s['text'] for s in drehbuch['szenen'])),
        'szenen': len(drehbuch['szenen']),
        'quellen': {entschaerfen(k): v for k, v in (drehbuch.get('quellen') or {}).items()
                    if isinstance(v, str) and v.startswith('https://de.wikipedia.org/')},
    })
    with open(verzeichnis, 'w', encoding='utf-8') as f:
        json.dump(folgen[:64], f, ensure_ascii=False, indent=1)
    os.chmod(verzeichnis, 0o644)
    print(f'    veroeffentlicht: {ZIEL}/{name}', flush=True)

    lauf(['python3', f'{BASIS}/monat.py', ziel_monat, '--merken'], 'Verlauf')
    print('=== fertig ===', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'FEHLER: {e}', file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)
