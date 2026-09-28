#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Setzt die Wochenschau zusammen: je Abschnitt eine Einstellung, dann aneinander.

Bewegung entsteht aus den vorhandenen Zeichnungen, nicht aus einem Videomodell.
Die Illustrationen sind freigestellt (RGBA) und liegen je Art in zwei Posen -
sitzend und im Flug. Daraus laesst sich schwenken, driften und ein Fluegelschlag
bauen. Kostet nichts und sieht aus wie die Wand im Wohnzimmer, weil es dieselben
Zeichnungen sind.
"""
import glob, json, os, shutil, subprocess, sys, wave

ILL   = '/srv/avian/BirdNET-Pi/avian/assets/illustrations'
FONT  = '/srv/avian/BirdNET-Pi/avian/frontend/fonts/Caveat.ttf'
GRAIN = '/srv/avian/BirdNET-Pi/avian/frontend/grain.png'
AUF   = '/srv/pi-daten/Extracted'
PAPIER, TINTE = '0xFAF9F6', '0x2A241E'
B, H, FPS = 1280, 720, 25


def dauer_wav(p):
    with wave.open(p) as w:
        return round(w.getnframes() / w.getframerate(), 2)


def slug(sci):
    return sci.lower().replace(' ', '-')


def ruf_datei(sci, arbeit, marke):
    """Beste Aufnahme dieser Art holen. Die Originalnamen enthalten Umlaute und
    Doppelpunkte - unter einem schlichten Namen kopieren erspart Zitierkrieg."""
    import sqlite3
    con = sqlite3.connect('file:/srv/pi-daten/birds.db?mode=ro', uri=True)
    r = con.execute('SELECT File_Name FROM detections WHERE Sci_Name=? '
                    'ORDER BY Confidence DESC LIMIT 1', (sci,)).fetchone()
    if not r:
        return None
    treffer = glob.glob(f'{AUF}/**/{r[0]}', recursive=True)
    if not treffer:
        return None
    ziel = os.path.join(arbeit, f'ruf-{marke}.mp3')
    shutil.copy(treffer[0], ziel)
    return ziel


def txt(s):
    """drawtext frisst Doppelpunkte, Hochkommas und Prozentzeichen nicht roh."""
    return s.replace('\\', r'\\\\').replace(':', r'\:').replace("'", r"\'").replace('%', r'\%')


def baue(rolle, stimme_wav, bilder, text, arbeit, ruf=None, art='ruhe'):
    d = dauer_wav(stimme_wav)
    ein = ['-f', 'lavfi', '-i', f'color=c={PAPIER}:s={B}x{H}:d={d}:r={FPS}',
           '-loop', '1', '-i', GRAIN]
    for p in bilder:
        ein += ['-loop', '1', '-i', p]
    ein += ['-i', stimme_wav]
    if ruf:
        ein += ['-i', ruf]

    k = [f'[1:v]scale={B}:{H},format=rgba,colorchannelmixer=aa=0.14[korn]',
         '[0:v][korn]overlay=shortest=1[l0]']
    n = 2                                     # Eingangsnummer des ersten Bildes
    letzte = 'l0'

    if art == 'flug':                         # fliegt quer durchs Bild
        k.append(f'[{n}:v]scale=-1:400,format=rgba[v0]')
        x = f"'-w+(W+w)*t/{d}'"
        y = f"'(H-h)/2-40+30*sin(t*1.1)'"
        k.append(f'[{letzte}][v0]overlay=x={x}:y={y}[l1]'); letzte = 'l1'

    elif art == 'schlag' and len(bilder) >= 2:  # Fluegelschlag aus zwei Posen
        k.append(f'[{n}:v]scale=-1:430,format=rgba[v0]')
        k.append(f'[{n+1}:v]scale=-1:430,format=rgba[v1]')
        drift = f"'(W-w)/2-160+320*t/{d}'"
        hoehe = f"'(H-h)/2-30+18*sin(t*2.2)'"
        k.append(f"[{letzte}][v0]overlay=x={drift}:y={hoehe}:enable='lt(mod(t,0.44),0.22)'[a1]")
        k.append(f"[a1][v1]overlay=x={drift}:y={hoehe}:enable='gte(mod(t,0.44),0.22)'[l1]")
        letzte = 'l1'

    elif art == 'reigen':                     # mehrere Voegel nacheinander
        stueck = d / max(1, len(bilder))
        for i, _ in enumerate(bilder):
            von, bis = i * stueck, (i + 1) * stueck
            px = int(B * (0.22 + 0.19 * i)) - 120
            k.append(f'[{n+i}:v]scale=-1:330,format=rgba[v{i}]')
            k.append(f"[{letzte}][v{i}]overlay=x={px}:y='(H-h)/2-30+8*sin(t*1.3+{i})'"
                     f":enable='between(t,{von:.2f},{bis:.2f})'[l{i+1}]")
            letzte = f'l{i+1}'

    else:                                     # ruhig: leichtes Wiegen
        k.append(f'[{n}:v]scale=-1:470,format=rgba[v0]')
        k.append(f"[{letzte}][v0]overlay=x='(W-w)/2+40*sin(t/3)'"
                 f":y='(H-h)/2-20+10*sin(t*0.9)'[l1]")
        letzte = 'l1'

    if text:
        k.append(f"[{letzte}]drawtext=fontfile={FONT}:text='{txt(text)}'"
                 f":fontcolor={TINTE}cc:fontsize=54:x=(w-text_w)/2:y=h-108"
                 f":alpha='min(1,max(0,(t-0.5)/1.2))'[bild]")
    else:
        k.append(f'[{letzte}]null[bild]')

    ton_ein = n + len(bilder)
    if ruf:
        aus = max(0.5, d - 1.5)
        k.append(f'[{ton_ein}:a]volume=1.0[stimme]')
        k.append(f'[{ton_ein+1}:a]volume=0.28,afade=t=in:st=0:d=1,'
                 f'afade=t=out:st={aus:.2f}:d=1.5[ruf]')
        k.append('[stimme][ruf]amix=inputs=2:duration=first:dropout_transition=0[ton]')
    else:
        k.append(f'[{ton_ein}:a]volume=1.0[ton]')

    ziel = os.path.join(arbeit, f'{rolle}.mp4')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error'] + ein +
                   ['-filter_complex', ';'.join(k), '-map', '[bild]', '-map', '[ton]',
                    '-t', str(d), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                    '-pix_fmt', 'yuv420p', '-r', str(FPS), '-c:a', 'aac', '-b:a', '128k',
                    '-ar', '44100', ziel], check=True)
    print(f'  {rolle:14s} {d:5.1f}s  {art}', file=sys.stderr)
    return ziel


def main():
    drehbuch = json.load(open('/srv/avian/wochenschau/drehbuch-aktuell.json', encoding='utf-8'))
    stimme = sys.argv[1] if len(sys.argv) > 1 else 'thorsten'
    ton = f'/srv/avian/wochenschau/ton/{stimme}'
    arbeit = '/srv/avian/wochenschau/arbeit'
    os.makedirs(arbeit, exist_ok=True)
    bes = drehbuch['besetzung']

    plan = {
        'aufschlag':    (None,                  'Die Vogelwoche', 'reigen'),
        'chor':         (None,                  None,             'reigen'),
        'besonderheit': (bes.get('besonderheit'), None,            'ruhe'),
        'durchreise':   (bes.get('durchreise'),  None,             'flug'),
        'portraet':     (bes.get('portraet'),    None,             'schlag'),
        'ausblick':     (None,                  'Bis nächste Woche', 'reigen'),
    }
    teile = []
    for s in drehbuch['segmente']:
        rolle = s['rolle']
        wav = f'{ton}-{rolle}.wav'
        if not os.path.isfile(wav):
            continue
        art_info, text, art = plan.get(rolle, (None, None, 'ruhe'))
        if art_info:
            bilder = [f"{ILL}/{slug(art_info['sci'])}.png"]
            zweite = f"{ILL}/{slug(art_info['sci'])}-2.png"
            if art in ('flug', 'schlag') and os.path.isfile(zweite):
                bilder = [zweite, bilder[0]] if art == 'flug' else [bilder[0], zweite]
            ruf = ruf_datei(art_info['sci'], arbeit, rolle)
            text = text or art_info['name']
        else:
            bilder = [f"{ILL}/{slug(c['sci'])}.png" for c in bes['chor']][:4]
            ruf = ruf_datei(bes['chor'][0]['sci'], arbeit, rolle) if rolle == 'chor' else None
        teile.append(baue(rolle, wav, bilder, text, arbeit, ruf, art))

    liste = os.path.join(arbeit, 'liste.txt')
    with open(liste, 'w') as f:
        for t in teile:
            f.write(f"file '{t}'\n")
    ziel = '/srv/avian/wochenschau/vogelwoche.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
                    '-i', liste, '-c', 'copy', ziel], check=True)
    print(f'\n  FERTIG: {ziel}', file=sys.stderr)
    print(ziel)


if __name__ == '__main__':
    main()
