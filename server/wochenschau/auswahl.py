#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sucht die Besetzung fuer die woechentliche Vogelschau aus.

AUFNAHMEREGEL (vom Nutzer festgelegt):
  Weg A: mindestens 10 Erkennungen ab 75 % Konfidenz
  Weg B: mindestens eine Erkennung ab 80 %
  Und nie etwas, das auf der Falsch-Liste steht - weder ganze Arten noch
  einzeln als falsch markierte Aufnahmen. Die Liste ist damit die Redaktion.

ABWECHSLUNG:
  Wer ein Portraet hatte, ist acht Wochen gesperrt; die Besonderheit vier.
  Gesperrt heisst gesperrt, nicht "bekommt Abzug" - sonst gewinnt die Amsel
  auf Dauer doch wieder. Sperrt die Karenz alles, wird sie gelockert, statt
  sich zu wiederholen.
"""
import json, os, random, sqlite3, sys
from datetime import date, timedelta

DB       = '/srv/pi-daten/birds.db'
HIDE     = '/srv/avian/verstecken/garten.json'
VERLAUF  = '/srv/avian/wochenschau/verlauf.json'
VERLAUF_MONAT = '/srv/avian/wochenschau/verlauf-monat.json'

MIN_KONF_A, MIN_ANZ_A = 0.75, 10      # Weg A
MIN_KONF_B            = 0.80          # Weg B
KARENZ_PORTRAET       = 8             # Wochen
KARENZ_BESONDERHEIT   = 4
DURCHZUG_MAX_TAGE     = 2             # so wenige Tage -> Verdacht auf Durchreise


def lade(pfad, alt):
    try:
        with open(pfad, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return alt


def main(tage=7):
    hide = lade(HIDE, {}) or {}
    versteckte_arten = set(hide.get('arten') or [])
    versteckte_aufn  = set(hide.get('aufnahmen') or [])
    verlauf = lade(VERLAUF, {})          # sci -> {"portraet": "2026-W36", ...}
    # Wer gerade das grosse Monatsportraet hatte, bekommt nicht auch noch das
    # Wochenportraet - sonst steht derselbe Vogel zweimal kurz nacheinander
    # im Mittelpunkt.
    monatsstars = {k for k, w in (lade(VERLAUF_MONAT, {}) or {}).items()
                   if w.get('portraet_monat')}

    con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    bis = date.today()
    von = bis - timedelta(days=tage - 1)

    alle = con.execute(
        "SELECT Sci_Name, Com_Name, Confidence, File_Name, Date, Time FROM detections").fetchall()
    alle = [r for r in alle if r[3] not in versteckte_aufn and r[0] not in versteckte_arten]

    gesamt, woche = {}, {}
    for sci, com, konf, datei, datum, zeit in alle:
        g = gesamt.setdefault(sci, {'com': com, 'n': 0, 'ab75': 0, 'best': 0.0, 'tage': set()})
        g['n'] += 1; g['best'] = max(g['best'], konf); g['tage'].add(datum)
        if konf >= MIN_KONF_A: g['ab75'] += 1
        if str(von) <= datum <= str(bis):
            w = woche.setdefault(sci, {'com': com, 'n': 0, 'best': 0.0, 'zeiten': []})
            w['n'] += 1; w['best'] = max(w['best'], konf); w['zeiten'].append((datum, zeit))

    def zugelassen(sci):
        g = gesamt.get(sci)
        return bool(g) and (g['ab75'] >= MIN_ANZ_A or g['best'] >= MIN_KONF_B)

    kandidaten = {s: w for s, w in woche.items() if zugelassen(s)}
    if not kandidaten:
        print(json.dumps({'ok': False, 'grund': 'keine zugelassene Art in diesem Zeitraum'}))
        return 1

    kw = bis.strftime('%Y-W%W')
    # Der Zufall wird an die Kalenderwoche gebunden. Ohne das liefert jeder
    # Aufruf eine andere Besetzung - drehbuch.py ruft diese Auswahl intern
    # erneut auf, und der Verlauf haette sich eine Besetzung gemerkt, die gar
    # nicht gesendet wurde. So ist der Lauf innerhalb einer Woche reproduzierbar.
    random.seed(kw)

    def gesperrt(sci, rolle, karenz):
        stand = (verlauf.get(sci) or {}).get(rolle)
        if not stand: return False
        try:
            jahr, w = stand.split('-W'); alt = int(jahr) * 53 + int(w)
            jahr2, w2 = kw.split('-W');  neu = int(jahr2) * 53 + int(w2)
            # Abstand 0 = dieselbe Woche wird neu gebaut, dann keine Sperre.
            return 0 < neu - alt < karenz
        except Exception:
            return False

    def waehle(pool, rolle, karenz, schluessel):
        frei = [s for s in pool if not gesperrt(s, rolle, karenz)]
        if not frei: frei = list(pool)          # lieber lockern als wiederholen
        if not frei: return None
        besten = sorted(frei, key=schluessel)
        # Aus den drei Besten zufaellig - sonst wirkt es mechanisch
        return random.choice(besten[:3]) if len(besten) >= 3 else besten[0]

    # Chor: was die Woche getragen hat. Keine Karenz, das ist Kulisse.
    chor = sorted(kandidaten, key=lambda s: -kandidaten[s]['n'])[:4]

    # Besonderheit: seltenste Art dieser Woche, gemessen am Gesamtbestand
    besonderheit = waehle(list(kandidaten), 'besonderheit', KARENZ_BESONDERHEIT,
                          lambda s: gesamt[s]['n'])

    # Durchreise: war nur an ein, zwei Tagen ueberhaupt da
    durchzug_pool = [s for s in kandidaten
                     if len(gesamt[s]['tage']) <= DURCHZUG_MAX_TAGE and s != besonderheit]
    durchreise = waehle(durchzug_pool, 'durchreise', KARENZ_PORTRAET,
                        lambda s: gesamt[s]['n']) if durchzug_pool else None

    # Portraet: seltenheitsgewichtet, nie eine der anderen Rollen
    portraet_pool = [s for s in kandidaten
                     if s not in (besonderheit, durchreise) and s not in monatsstars]
    if not portraet_pool:
        portraet_pool = [s for s in kandidaten if s not in (besonderheit, durchreise)]
    portraet = waehle(portraet_pool, 'portraet', KARENZ_PORTRAET,
                      lambda s: gesamt[s]['n'])

    zeiten = [z for w in kandidaten.values() for z in w['zeiten']]
    bester_morgen = None
    if zeiten:
        proTag = {}
        for d, t in zeiten: proTag[d] = proTag.get(d, 0) + 1
        bester_morgen = max(proTag.items(), key=lambda x: x[1])

    def steckbrief(sci):
        if not sci: return None
        g, w = gesamt[sci], kandidaten[sci]
        return {'sci': sci, 'name': g['com'], 'woche_n': w['n'], 'gesamt_n': g['n'],
                'tage_gesamt': len(g['tage']), 'beste_konfidenz': round(g['best'], 3),
                'erstes_mal': min(g['tage']), 'zuletzt': max(g['tage'])}

    ergebnis = {
        'ok': True, 'kalenderwoche': kw, 'von': str(von), 'bis': str(bis),
        'arten_diese_woche': len(kandidaten),
        'erkennungen_diese_woche': sum(w['n'] for w in kandidaten.values()),
        'lautester_tag': {'datum': bester_morgen[0], 'anzahl': bester_morgen[1]} if bester_morgen else None,
        'chor': [steckbrief(s) for s in chor],
        'besonderheit': steckbrief(besonderheit),
        'durchreise': steckbrief(durchreise),
        'portraet': steckbrief(portraet),
        'ausgeschlossen_versteckt': len(versteckte_arten),
    }
    print(json.dumps(ergebnis, ensure_ascii=False, indent=1))

    if '--merken' in sys.argv:
        for rolle, sci in (('besonderheit', besonderheit), ('durchreise', durchreise),
                           ('portraet', portraet)):
            if sci: verlauf.setdefault(sci, {})[rolle] = kw
        os.makedirs(os.path.dirname(VERLAUF), exist_ok=True)
        with open(VERLAUF, 'w', encoding='utf-8') as f:
            json.dump(verlauf, f, ensure_ascii=False, indent=1)
        print('\nVerlauf fortgeschrieben.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
