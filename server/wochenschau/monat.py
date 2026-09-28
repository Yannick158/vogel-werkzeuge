#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monatsauswertung fuer das lange Video.

Liefert alles, was der Nutzer sehen wollte:
  bogen       - wer neu kam, wer sich verabschiedet hat
  zugbilanz   - Abschiede, spaeter mit Ziel aus den Quellen
  kalender    - wann welche Art vorbeigeschaut hat
  vergleich   - dieser Monat gegen den vorigen
  verhaeltnis - seltene gegen haeufige Arten
  portraet    - die Art fuer das lange Portraet

Es gilt dieselbe Aufnahmeregel wie in der Wochenschau (10x ab 75 % oder einmal
ab 80 %) und dieselbe Falsch-Liste als Redaktion.
"""
import json, os, sqlite3, sys
from collections import defaultdict
from datetime import date, timedelta

DB      = '/srv/pi-daten/birds.db'
HIDE    = '/srv/avian/verstecken/garten.json'
VERLAUF = '/srv/avian/wochenschau/verlauf-monat.json'
VERLAUF_WOCHE = '/srv/avian/wochenschau/verlauf.json'
KARENZ_AUS_WOCHE = 8                # Wochen: wer dort vorkam, ist hier gesperrt
MIN_KONF_A, MIN_ANZ_A, MIN_KONF_B = 0.75, 10, 0.80
KARENZ_PORTRAET = 6                 # Monate
SELTEN_UNTER = 5                    # Erkennungen gesamt

# Vor diesem Tag war das Mikrofon defekt - die Aufnahmen davor sind zwar echt,
# taugen aber nicht fuer Aussagen wie "neu angekommen" oder "hat sich
# verabschiedet". Sonst haette das Video behauptet, die Amsel sei am 8.
# September neu in den Garten gezogen. Sie war immer da; das Mikrofon war weg.
VERLAESSLICH_AB = '2026-09-08'
MIN_TAGE_FUER_BOGEN = 21            # so viele verlaessliche Tage braucht ein Bogen
MIN_TAGE_FUER_VERGLEICH = 14


def lade(p, alt):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return alt


def main():
    heute = date.today()
    monat = sys.argv[1] if len(sys.argv) > 1 else heute.strftime('%Y-%m')
    jahr, mon = int(monat[:4]), int(monat[5:7])
    erster = date(jahr, mon, 1)
    letzter = date(jahr + (mon == 12), mon % 12 + 1, 1) - timedelta(days=1)
    vor_erster = date(jahr - (mon == 1), (mon - 2) % 12 + 1, 1)
    vor_letzter = erster - timedelta(days=1)

    hide = lade(HIDE, {}) or {}
    weg_arten = set(hide.get('arten') or [])
    weg_aufn = set(hide.get('aufnahmen') or [])
    verlauf = lade(VERLAUF, {})

    con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    roh = con.execute('SELECT Sci_Name, Com_Name, Confidence, File_Name, Date, Time '
                      'FROM detections').fetchall()
    roh = [r for r in roh if r[3] not in weg_aufn and r[0] not in weg_arten]

    gesamt = defaultdict(lambda: {'n': 0, 'ab75': 0, 'best': 0.0, 'tage': set()})
    for sci, com, k, _, d, _ in roh:
        g = gesamt[sci]
        g['n'] += 1; g['best'] = max(g['best'], k); g['tage'].add(d)
        g['com'] = com
        if k >= MIN_KONF_A:
            g['ab75'] += 1

    def zugelassen(sci):
        g = gesamt.get(sci)
        return bool(g) and (g['ab75'] >= MIN_ANZ_A or g['best'] >= MIN_KONF_B)

    def im_zeitraum(a, b):
        raus = defaultdict(lambda: {'n': 0, 'tage': set(), 'stunden': defaultdict(int)})
        for sci, com, k, _, d, t in roh:
            if str(a) <= d <= str(b) and zugelassen(sci):
                x = raus[sci]
                x['n'] += 1; x['tage'].add(d); x['com'] = com
                try:
                    x['stunden'][int(t[:2])] += 1
                except Exception:
                    pass
        return raus

    jetzt = im_zeitraum(erster, letzter)
    vorher = im_zeitraum(vor_erster, vor_letzter)

    # --- Datenlage zuerst: worueber duerfen wir ueberhaupt reden? ---
    alle_tage = sorted({d for _, _, _, _, d, _ in roh})
    gute_tage = [d for d in alle_tage if d >= VERLAESSLICH_AB]
    luecken = []
    for a, b in zip(alle_tage, alle_tage[1:]):
        d1, d2 = date.fromisoformat(a), date.fromisoformat(b)
        if (d2 - d1).days > 1:
            luecken.append({'von': str(d1 + timedelta(days=1)),
                            'bis': str(d2 - timedelta(days=1)),
                            'tage': (d2 - d1).days - 1})
    bogen_belastbar = len(gute_tage) >= MIN_TAGE_FUER_BOGEN

    # --- Bogen: wer kam neu, wer verabschiedete sich ---
    neu, abschied = [], []
    for sci, x in jetzt.items():
        erst = min(gesamt[sci]['tage'])
        if str(erster) <= erst <= str(letzter):
            neu.append({'sci': sci, 'name': x['com'], 'seit': erst, 'n': x['n']})
        zuletzt = max(gesamt[sci]['tage'])
        # seit ueber einer Woche nicht mehr gehoert und der Monat neigt sich
        if (heute - date.fromisoformat(zuletzt)).days >= 7:
            abschied.append({'sci': sci, 'name': x['com'], 'zuletzt': zuletzt, 'n': x['n']})
    neu.sort(key=lambda a: a['seit'])
    abschied.sort(key=lambda a: a['zuletzt'])

    # --- Kalender: wann war wer da ---
    kalender = []
    for sci, x in sorted(jetzt.items(), key=lambda kv: -kv[1]['n'])[:14]:
        st = x['stunden']
        kalender.append({
            'sci': sci, 'name': x['com'], 'n': x['n'],
            'tage': sorted(x['tage']),
            'lieblingsstunde': max(st, key=st.get) if st else None,
        })

    # --- Selten gegen haeufig ---
    selten = [{'sci': s, 'name': x['com'], 'n': gesamt[s]['n']}
              for s, x in jetzt.items() if gesamt[s]['n'] < SELTEN_UNTER]
    haeufig = [{'sci': s, 'name': x['com'], 'n': x['n']}
               for s, x in jetzt.items() if gesamt[s]['n'] >= 20]
    selten.sort(key=lambda a: a['n']); haeufig.sort(key=lambda a: -a['n'])

    # --- Vergleich mit dem Vormonat ---
    vergleich = {
        'monat': {'arten': len(jetzt), 'erkennungen': sum(x['n'] for x in jetzt.values()),
                  'tage_mit_daten': len({d for x in jetzt.values() for d in x['tage']})},
        'vormonat': {'arten': len(vorher), 'erkennungen': sum(x['n'] for x in vorher.values()),
                     'tage_mit_daten': len({d for x in vorher.values() for d in x['tage']})},
    }
    v = vergleich['vormonat']
    vergleich['belastbar'] = v['tage_mit_daten'] >= MIN_TAGE_FUER_VERGLEICH

    # --- Portraet: seltenste zugelassene Art ohne Karenz ---
    # Gesperrt ist, wer in den letzten Monaten schon ein Monatsportraet hatte -
    # UND wer kuerzlich in der Wochenschau eine Hauptrolle spielte. Ohne das
    # zweite waere derselbe Vogel binnen Tagen zweimal der Star gewesen.
    # Abstand 0 heisst: dieselbe Folge wird noch einmal gebaut. Dann darf die
    # Karenz NICHT greifen, sonst waehlt jeder Neubau einen anderen Vogel - und
    # ein schon bezahlter Veo-Clip zeigte ploetzlich die falsche Art.
    aus_monat = {k for k, w in verlauf.items()
                 if w.get('portraet_monat')
                 and 0 < _monatsabstand(w['portraet_monat'], monat) < KARENZ_PORTRAET}
    verlauf_w = lade(VERLAUF_WOCHE, {})
    aus_woche = set()
    for sci, rollen in verlauf_w.items():
        for kw_wert in rollen.values():
            if _wochenabstand(kw_wert, heute) < KARENZ_AUS_WOCHE:
                aus_woche.add(sci)
                break
    kandidaten = [s for s in jetzt if s not in aus_monat and s not in aus_woche]
    if not kandidaten:                      # lieber lockern als wiederholen
        kandidaten = [s for s in jetzt if s not in aus_monat] or list(jetzt)
    portraet = min(kandidaten, key=lambda s: gesamt[s]['n']) if kandidaten else None

    if not bogen_belastbar:
        # Lieber gar nichts behaupten als etwas Falsches. Die Zahlen bleiben,
        # die Ankunfts- und Abschiedsdeutung faellt weg.
        neu, abschied = [], []

    # --- Zugziele fuer die grosse Karte ---
    # Wohin die Verabschiedeten fliegen, steht in den Quellen. Wir holen sie
    # nur fuer die Arten, die sich tatsaechlich verabschiedet haben - und nur,
    # wenn der Bogen ueberhaupt belastbar ist. Ohne Beleg kein Ziel.
    zugziele = []
    if abschied:
        import re as _re, subprocess as _sp
        namen = [a['name'] for a in abschied[:5]]
        try:
            q = json.loads(_sp.run(['python3', os.path.dirname(os.path.abspath(__file__))
                                    + '/quellen.py'] + namen,
                                   capture_output=True, text=True, timeout=120).stdout or '{}')
        except Exception:
            q = {}
        for a in abschied[:5]:
            t = (q.get(a['name']) or {}).get('text', '')
            ziel = None
            for muster in (r'ueberwintert?[^.]{0,80}?in ([A-ZÄÖÜ][\wäöüß\- ]{3,30})',
                           r'überwintert?[^.]{0,80}?in ([A-ZÄÖÜ][\wäöüß\- ]{3,30})',
                           r'Winterquartiere?[^.]{0,60}?in ([A-ZÄÖÜ][\wäöüß\- ]{3,30})'):
                m = _re.search(muster, t)
                if m:
                    ziel = m.group(1).strip(' ,.;')
                    break
            if ziel:
                zugziele.append({'name': a['name'], 'sci': a['sci'], 'ziel': ziel,
                                 'zuletzt': a['zuletzt']})

    raus = {
        'ok': bool(jetzt), 'monat': monat,
        'zugziele': zugziele,
        'datenlage': {
            'tage_gesamt': len(alle_tage),
            'verlaessliche_tage': len(gute_tage),
            'verlaesslich_ab': VERLAESSLICH_AB,
            'luecken': luecken,
            'bogen_belastbar': bogen_belastbar,
            'braucht_noch_tage': max(0, MIN_TAGE_FUER_BOGEN - len(gute_tage)),
        },
        'von': str(erster), 'bis': str(min(letzter, heute)),
        'arten': len(jetzt), 'erkennungen': sum(x['n'] for x in jetzt.values()),
        'bogen': {'angekommen': neu[:6], 'verabschiedet': abschied[:6]},
        'kalender': kalender,
        # Alle seltenen Arten, nicht nur eine Auswahl - sie sollen im Video
        # vollstaendig aufgerufen werden. 24 ist die Grenze der Lesbarkeit.
        'verhaeltnis': {'selten': selten[:24], 'haeufig': haeufig[:6],
                        'anzahl_selten': len(selten), 'anzahl_haeufig': len(haeufig)},
        'vergleich': vergleich,
        'portraet': ({'sci': portraet, 'name': jetzt[portraet]['com'],
                      'n': jetzt[portraet]['n'], 'gesamt_n': gesamt[portraet]['n'],
                      'tage': sorted(jetzt[portraet]['tage'])} if portraet else None),
    }
    print(json.dumps(raus, ensure_ascii=False, indent=1))

    if '--merken' in sys.argv and portraet:
        verlauf.setdefault(portraet, {})['portraet_monat'] = monat
        os.makedirs(os.path.dirname(VERLAUF), exist_ok=True)
        with open(VERLAUF, 'w', encoding='utf-8') as f:
            json.dump(verlauf, f, ensure_ascii=False, indent=1)


def _wochenabstand(kw, bezug):
    """Abstand in Wochen zwischen einem Eintrag wie "2026-W36" und heute."""
    try:
        jahr, w = kw.split('-W')
        return (bezug.isocalendar()[0] - int(jahr)) * 53 + (bezug.isocalendar()[1] - int(w))
    except Exception:
        return 999


def _monatsabstand(a, b):
    ja, ma = int(a[:4]), int(a[5:7]); jb, mb = int(b[:4]), int(b[5:7])
    return (jb - ja) * 12 + (mb - ma)


if __name__ == '__main__':
    main()
