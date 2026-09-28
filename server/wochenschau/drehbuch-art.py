#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drehbuch fuer ein Artenportraet - ein kurzes Video je Vogelart.

Wird gebaut, wenn eine Art zum ersten Mal erkannt wurde. Beantwortet die
Fragen, die man sich beim Anklicken eines Vogels stellt: Ist die selten?
Wie oft war sie da? Wann? Was macht sie aus? Was frisst sie?

Wikipedia wird ueber den WISSENSCHAFTLICHEN Namen abgefragt. Deutsche Namen
fuehren teils auf Begriffsklaerungsseiten ("Bienenfresser", "Brachvogel") -
dort stehen nur Verweise, keine Fakten.
"""
import json, sqlite3, subprocess, sys, urllib.request
from collections import defaultdict

BASIS  = '/srv/avian/wochenschau'
DB     = '/srv/pi-daten/birds.db'
MODELL = 'gemini-3.5-flash'

ANWEISUNG = """Du schreibst das Drehbuch fuer ein kurzes Vogelportraet, das auf einer
Wandanzeige im Wohnzimmer laeuft. Publikum: zwei aeltere Menschen in Zweitstandort,
in deren Garten ein Mikrofon Voegel am Gesang erkennt. Sie haben gerade auf
diesen Vogel getippt und wollen wissen, mit wem sie es zu tun haben.

TON
Warm und ruhig erzaehlt, kein Werbeton, keine Superlative. Hoechstens einmal ein
bairischer Anklang. Sprich von "Ihrem Garten".

WAHRHEIT
- Zahlen zum Garten nur aus MESSDATEN, jede Artaussage nur aus QUELLEN.
- Zu jeder Szene den Beleg unter "beleg", woertlich aus den QUELLEN.
- MESSDATEN-Zahlen sind ERKENNUNGEN, also gezaehlte Rufe - niemals die Anzahl
  der Voegel. "hundertmal zu hoeren", nicht "hundert Voegel".
- "selten" heisst selten IN DIESEM GARTEN. Ob die Art allgemein selten oder
  haeufig ist, steht in den QUELLEN - beides darf vorkommen, aber nicht
  verwechselt werden.
- Grosse Zahlen im Sprechtext runden, als Woerter schreiben.

ZEITLOS - wichtig
Dieses Video liegt dauerhaft in der Vogelkarte und wird angeschaut, wann immer
jemand auf die Art tippt. Es ist KEINE Neuigkeit.
- Schreibe NICHTS von "erstmals", "zum ersten Mal", "neu im Garten", "heute",
  "diese Woche" oder "gerade eben". In drei Monaten waere das schlicht falsch.
- Nenne keine Datumsangaben aus den MESSDATEN.
- KEINE ZAEHLUNGEN aus dem Garten. Nicht "viermal", nicht "sechshundertmal",
  auch nicht "bisher viermal". Wer das Video in einem Jahr sieht, liest sonst
  eine Zahl, die laengst nicht mehr stimmt. Ob eine Art hier haeufig oder
  selten ist, darfst du sagen - aber ohne Zahl.
- Die TAGESZEIT dagegen bleibt und ist der interessanteste Teil: Nenne, wann
  die Art in DIESEM Garten zu hoeren war (aus MESSDATEN, "lieblingsstunde"),
  und wann sie laut QUELLEN ueblicherweise ruft oder singt. Sage ausdruecklich,
  ob beides zusammenpasst oder auseinandergeht - das ist die schoenste
  Beobachtung, die dieses Video machen kann. Steht in den QUELLEN nichts zur
  Tageszeit, nenne nur die eigene Beobachtung und behaupte keinen Vergleich.

WAS BEANTWORTET WERDEN SOLL, in dieser Reihenfolge
1. Wer ist das - ein Satz zur Einordnung.
2. Wie haeufig ist sie in diesem Garten - viel, wenig, ein seltener Gast?
   Rein qualitativ, ohne jede Zahl.
3. Wann ist sie da - die Stunde aus den MESSDATEN, dazu aus den QUELLEN, wann
   sie ueblicherweise aktiv ist, und ob das zusammenpasst. Dann die Jahreszeit.
4. Woran man sie erkennt - Aussehen oder Stimme, aus den QUELLEN.
5. Was sie frisst, aus den QUELLEN.
6. Eine Besonderheit, die man sich merkt.
7. Ein ruhiger Schlusssatz.

UMFANG
10 bis 14 Szenen, zusammen 110 bis 150 Woerter. Das ergibt knapp eine Minute.
Je Szene EIN Gedanke, 6 bis 16 Woerter. Satzlaenge wechseln; hoechstens zwei
von drei Saetzen mit dem Subjekt beginnen.

BILDER - genau eine Form je Szene
  titel:<Text>                     Schrifttafel
  vogel:<wissenschaftlicher Name>   die Zeichnung, ruhig bewegt
  flug:<wissenschaftlicher Name>    die Art im Flug
  zahl:<Zahl>|<Beschriftung>        eine grosse Zahl
  liste:<Name>=<Zahl>,...           Rangliste
Nutze den wissenschaftlichen Namen genau so, wie er in den MESSDATEN steht.
Hoechstens EINE Zahlentafel - das ist ein Portraet, kein Kontoauszug.

Antworte NUR mit JSON:
{"titel":"<deutscher Artname>","kernsatz":"...","szenen":[{"text":"...","bild":"...","beleg":"..."}]}"""


def daten(sci):
    con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    hide = {}
    try:
        hide = json.load(open('/srv/avian/verstecken/garten.json', encoding='utf-8'))
    except Exception:
        pass
    weg = set(hide.get('aufnahmen') or [])

    zeilen = con.execute('SELECT Com_Name, Confidence, File_Name, Date, Time '
                         'FROM detections WHERE Sci_Name = ?', (sci,)).fetchall()
    zeilen = [z for z in zeilen if z[2] not in weg]
    if not zeilen:
        return None
    stunden = defaultdict(int)
    tage = set()
    for com, k, datei, d, t in zeilen:
        tage.add(d)
        try:
            stunden[int(t[:2])] += 1
        except Exception:
            pass
    alle = dict(con.execute(
        'SELECT Sci_Name, COUNT(*) FROM detections GROUP BY Sci_Name').fetchall())
    rang = sorted(alle.values(), reverse=True).index(alle.get(sci, 0)) + 1
    return {
        'sci': sci, 'name': zeilen[0][0],
        'erkennungen': len(zeilen),
        'tage': len(tage),
        'erstmals': min(tage), 'zuletzt': max(tage),
        'lieblingsstunde': max(stunden, key=stunden.get) if stunden else None,
        'stunden_von_bis': [min(stunden), max(stunden)] if stunden else None,
        'platz_im_garten': rang, 'arten_insgesamt': len(alle),
        'haeufigste_art_hat': max(alle.values()) if alle else 0,
    }


def main():
    sci = sys.argv[1] if len(sys.argv) > 1 else None
    if not sci:
        print('Aufruf: drehbuch-art.py "Sci Name"', file=sys.stderr); return 1
    d = daten(sci)
    if not d:
        print('keine Erkennungen fuer', sci, file=sys.stderr); return 1

    # Wikipedia ueber den wissenschaftlichen Namen - der leitet zuverlaessig
    # auf den Artartikel um, der deutsche Name manchmal nicht.
    q = subprocess.run(['python3', f'{BASIS}/quellen.py', sci],
                       capture_output=True, text=True, timeout=180)
    quellen = json.loads(q.stdout) if q.stdout.strip() else {}
    if not quellen:
        print('keine Quelle fuer', sci, file=sys.stderr); return 1

    prompt = (ANWEISUNG + '\n\n=== MESSDATEN AUS DEM GARTEN ===\n'
              + json.dumps(d, ensure_ascii=False, indent=1)
              + '\n\n=== QUELLEN ===\n'
              + '\n\n'.join(f"--- QUELLE: {k} ({v['quelle']}) ---\n{v['text']}"
                            for k, v in quellen.items()))

    key = open(f'{BASIS}/.gemini-key').read().strip()
    url = (f'https://generativelanguage.googleapis.com/v1beta/models/{MODELL}'
           f':generateContent?key={key}')
    koerper = json.dumps({
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'temperature': 0.8, 'maxOutputTokens': 24000,
                             'responseMimeType': 'application/json'},
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(
            url, data=koerper, headers={'Content-Type': 'application/json'}),
            timeout=180) as r:
        antwort = json.load(r)

    k = (antwort.get('candidates') or [{}])[0]
    roh = ''.join(t.get('text', '') for t in (k.get('content') or {}).get('parts', [])
                  if not t.get('thought')).strip()
    if roh.startswith('```'):
        roh = roh.split('\n', 1)[1].rsplit('```', 1)[0]
    try:
        drehbuch = json.loads(roh)
    except json.JSONDecodeError as e:
        print(f'Kein gueltiges JSON ({e}):\n{roh[:600]}', file=sys.stderr); return 1
    if not drehbuch.get('szenen'):
        print('Drehbuch ohne Szenen', file=sys.stderr); return 1

    drehbuch['sci'] = sci
    drehbuch['messdaten'] = d
    drehbuch['quellen'] = {kk: v['quelle'] for kk, v in quellen.items()}
    print(json.dumps(drehbuch, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
