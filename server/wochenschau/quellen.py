#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Holt Belegtexte aus der deutschen Wikipedia. Nur daraus darf spaeter
formuliert werden - das Sprachmodell bekommt nichts anderes zu sehen."""
import json, sys, urllib.parse, urllib.request

API = 'https://de.wikipedia.org/w/api.php'
KOPF = {'User-Agent': 'AvianVisitors/1.0 (privates Vogelprojekt; Kontakt ueber die Seite)'}

# Frueher stand hier eine Liste erwuenschter Abschnitte - das war zu eng
# gefasst und lieferte fuer die Rauchschwalbe ganze 1020 Zeichen. Jetzt
# andersherum: alles nehmen ausser dem, was fuer eine Erzaehlung nichts hergibt.
UNINTERESSANT = ('literatur', 'weblinks', 'einzelnachweise', 'quellen', 'belege',
                 'siehe auch', 'anmerkungen', 'fußnoten')


def hole(titel):
    p = {'action': 'query', 'format': 'json', 'prop': 'extracts',
         'explaintext': 1, 'redirects': 1, 'titles': titel}
    req = urllib.request.Request(API + '?' + urllib.parse.urlencode(p), headers=KOPF)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    seiten = (d.get('query') or {}).get('pages') or {}
    for _, s in seiten.items():
        if 'extract' in s:
            return s['title'], s['extract']
    return None, None


def kuerzen(text, max_zeichen=14000):
    """Einleitung immer, dazu jeden Abschnitt ausser Literatur- und
    Nachweislisten. Je mehr belegter Stoff im Prompt liegt, desto mehr echte
    Fakten kann die Erzaehlung bringen, ohne etwas zu erfinden."""
    teile = text.split('\n\n\n== ')
    aus = [teile[0].strip()]
    for t in teile[1:]:
        kopf = t.split('==')[0].strip().lower()
        if not any(k in kopf for k in UNINTERESSANT):
            aus.append('== ' + t.strip())
    ganz = '\n\n'.join(aus)
    return ganz[:max_zeichen]


def ist_begriffsklaerung(text):
    """Deutsche Namen wie "Bienenfresser" oder "Brachvogel" fuehren auf
    Begriffsklaerungsseiten - die Art heisst dort "Bienenfresser (Art)" bzw.
    "Grosser Brachvogel". Solche Seiten enthalten keine Fakten, nur Verweise."""
    t = (text or '')[:220].lower()
    return 'bezeichnet:' in t or 'steht fuer:' in t or 'steht für:' in t


if __name__ == '__main__':
    raus = {}
    for titel in sys.argv[1:]:
        t, text = hole(titel)
        if text and ist_begriffsklaerung(text):
            # Zweiter Versuch ueber den wissenschaftlichen Namen, falls einer
            # mitgegeben wurde (Format "Deutscher Name|Wissenschaftlicher Name")
            print(f'  {titel}: Begriffsklaerung, kein Artartikel', file=sys.stderr)
            t, text = None, None
        if text:
            raus[titel] = {'wikipedia_titel': t, 'text': kuerzen(text),
                           'quelle': 'https://de.wikipedia.org/wiki/' + urllib.parse.quote(t.replace(' ', '_'))}
            print(f'  {titel}: {len(raus[titel]["text"])} Zeichen aus "{t}"', file=sys.stderr)
        else:
            print(f'  {titel}: NICHT GEFUNDEN', file=sys.stderr)
    print(json.dumps(raus, ensure_ascii=False))
