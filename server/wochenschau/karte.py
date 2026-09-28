#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Baut eine Zugweg-Tafel: zwei Orte, ein gestrichelter Bogen dazwischen.

Bewusst schematisch und nicht als Landkarte gezeichnet. Eine echte Karte muesste
geografisch stimmen; eine Skizze darf abstrahieren und kann nicht falsch liegen.
Genau so machen es die Erklaerbilder, die man von NotebookLM kennt.
"""
import json, math, re, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

FONT  = '/srv/avian/BirdNET-Pi/avian/frontend/fonts/Caveat.ttf'
GRAIN = '/srv/avian/BirdNET-Pi/avian/frontend/grain.png'
B, H  = 1280, 720
PAPIER, TINTE, MATT = (250, 249, 246), (42, 36, 30), (140, 130, 118)

VON, NACH = (940, 205), (300, 560)        # Zweitstandort  ->  Westafrika
BAUCH_X, BAUCH_Y = -70, 45                # wie weit sich der Bogen woelbt


def punkt(s):
    """Ein Punkt auf dem Bogen, s von 0 bis 1. Dieselbe Formel benutzt spaeter
    ffmpeg fuer die Schwalbe - sonst fliegt sie neben der Linie her."""
    w = 4 * s * (1 - s)
    return (VON[0] + (NACH[0] - VON[0]) * s + BAUCH_X * w,
            VON[1] + (NACH[1] - VON[1]) * s + BAUCH_Y * w)


def tafel(ziel, entfernung=None):
    bild = Image.new('RGB', (B, H), PAPIER)
    korn = Image.open(GRAIN).convert('RGB').resize((B, H))
    bild = Image.blend(bild, korn, 0.10)
    d = ImageDraw.Draw(bild)
    gross = ImageFont.truetype(FONT, 46)
    klein = ImageFont.truetype(FONT, 34)

    # Gestrichelter Bogen
    an = True
    for i in range(0, 200):
        a, b = punkt(i / 200), punkt((i + 1) / 200)
        if an:
            d.line([a, b], fill=MATT, width=3)
        if i % 5 == 0:
            an = not an

    for (x, y), name, oben in ((VON, 'Zweitstandort', True), (NACH, 'Westafrika', False)):
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=TINTE)
        d.ellipse([x - 18, y - 18, x + 18, y + 18], outline=MATT, width=2)
        b = d.textbbox((0, 0), name, font=gross)
        d.text((x - (b[2] - b[0]) / 2, y - 78 if oben else y + 30), name,
               font=gross, fill=TINTE)

    d.text((60, 54), 'Wohin sie jetzt fliegt', font=gross, fill=TINTE)
    if entfernung:
        m = punkt(0.5)
        b = d.textbbox((0, 0), entfernung, font=klein)
        d.text((m[0] - (b[2] - b[0]) / 2, m[1] + 26), entfernung, font=klein, fill=MATT)
    bild.save(ziel)
    return ziel


def ffmpeg_ausdruck(dauer, breite_platzhalter='w', hoehe_platzhalter='h'):
    """Dieselbe Kurve als ffmpeg-Ausdruck, damit die Schwalbe der Linie folgt."""
    s = f'(t/{dauer})'
    w = f'(4*{s}*(1-{s}))'
    x = f'({VON[0]}+({NACH[0]-VON[0]})*{s}+{BAUCH_X}*{w}-{breite_platzhalter}/2)'
    y = f'({VON[1]}+({NACH[1]-VON[1]})*{s}+{BAUCH_Y}*{w}-{hoehe_platzhalter}/2)'
    return x, y


if __name__ == '__main__':
    # Ist eine Entfernung ueberhaupt belegt?
    q = json.loads(subprocess.run(['python3', '/srv/avian/wochenschau/quellen.py',
                                   'Rauchschwalbe'], capture_output=True, text=True).stdout)
    t = q['Rauchschwalbe']['text']
    km = re.findall(r'(\d[\d\.\s]{2,6})\s*(?:km|Kilometer)', t)
    print('  Entfernungsangaben in der Quelle:', km or 'KEINE', file=sys.stderr)
    beleg = None
    if km:
        beleg = f"{km[0].strip()} km"
    tafel('/tmp/tafel.png', beleg)
    x, y = ffmpeg_ausdruck(7.0)
    print(json.dumps({'tafel': '/tmp/tafel.png', 'x': x, 'y': y, 'entfernung': beleg}))
