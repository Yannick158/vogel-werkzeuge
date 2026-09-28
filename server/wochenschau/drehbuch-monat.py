#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drehbuch fuer das lange Monatsvideo (3 bis 5 Minuten).

Wie beim Wochendrehbuch sieht das Sprachmodell nur Messdaten und Quelltexte.
Neu ist die DATENLAGE: sagt sie, dass der Monatsbogen nicht belastbar ist,
darf ueber Ankunft, Abschied und Vergleich kein Wort fallen. Lieber ein
kuerzeres Video als eine erfundene Entwicklung.
"""
import json, os, subprocess, sys, urllib.request

BASIS  = '/srv/avian/wochenschau'
MODELL = 'gemini-3.5-flash'

ANWEISUNG = """Du schreibst das Drehbuch fuer den MONATSRUECKBLICK der "Vogelwoche" - ein
laengeres Video, das einmal im Monat auf einer Wandanzeige im Wohnzimmer laeuft.
Publikum: zwei aeltere Menschen in Zweitstandort mit einem Vogelmikrofon im Garten.

Alle Regeln des Wochenrueckblicks gelten weiter:
- Warm und ruhig erzaehlt, kein Werbeton, keine Superlative, hoechstens einmal
  ein bairischer Anklang. Sprich von "Ihrem Garten".
- Zahlen zum Garten nur aus MESSDATEN, jede Artaussage nur aus QUELLEN.
  Zu jeder Szene der Beleg unter "beleg", woertlich.
- MESSDATEN-Zahlen sind ERKENNUNGEN, niemals die Anzahl der Voegel.
- "selten" heisst selten IN DIESEM GARTEN, nie selten als Art.
- Grosse Zahlen im Sprechtext runden, als Woerter schreiben.
- Je Szene EIN Gedanke, 6 bis 16 Woerter. Satzlaenge wechseln, hoechstens zwei
  von drei Saetzen mit dem Subjekt beginnen.

WAS DIE DATENLAGE ERLAUBT - vor allem anderen pruefen
Im Abschnitt MESSDATEN steht "datenlage". Ist dort "bogen_belastbar": false,
dann darfst du NICHTS ueber Ankunft, Abschied, Verschiebung des Zuges oder
Entwicklung sagen - es gibt schlicht zu wenige Tage. Auch kein "erstmals" und
kein "seit". Ist "vergleich.belastbar": false, faellt jeder Vergleich mit dem
Vormonat weg. Erfinde in diesem Fall keinen Ersatz, sondern mache das Video
kuerzer und erzaehle mehr ueber die einzelnen Voegel.

UMFANG
40 bis 60 Szenen, zusammen 450 bis 700 Woerter. Das ergibt drei bis fuenf Minuten.
Sind Bogen und Vergleich gesperrt, nimm die untere Grenze.

AUFBAU
1. Ankunft im Monat, ruhiger Einstieg
2. Wer den Monat getragen hat - der Alltag im Garten
3. Wann welche Art zu hoeren war (Tagesrhythmus)
4. Seltene gegen haeufige Arten
5. Der Monatsbogen: wer kam, wer ging - NUR wenn erlaubt
6. Die Zugbilanz mit Zielen - NUR wenn erlaubt
7. Das grosse Portraet: eine Art ueber mehrere Minuten, mit vielen Einzelheiten
   aus den QUELLEN - Aussehen, Nahrung, Brut, Zug, Bestand
8. Vergleich mit dem Vormonat - NUR wenn erlaubt
9. Ruhiger Ausklang mit Ausblick auf den kommenden Monat

BILDER - genau eine Form je Szene
  titel:<Text>                     Schrifttafel
  vogel:<wissenschaftlicher Name>   Zeichnung der Art, ruhig
  flug:<wissenschaftlicher Name>    die Art im Flug
  zahl:<Zahl>|<Beschriftung>        grosse Zahl, zaehlt hoch
  liste:<Name>=<Zahl>,<Name>=<Zahl> Rangliste
  karte:<Ort>|<Ziel>                ein Zugweg
  kalender:                         Tafel: wann welche Art zu hoeren war
  seltene:                          Tafel: ALLE seltenen Arten mit Bild und Anzahl
  veo:<wiss. Name>|<motion>         bewegte Aufnahme der Portraet-Art (siehe unten)
  zugkarte:                         Tafel: alle Abschiede mit ihren Zielen
  verhaeltnis:                      Tafel: seltene gegen haeufige Arten
kalender:, seltene: und verhaeltnis: haben keinen Zusatz - sie holen ihre
Angaben selbst.

`seltene:` MUSS genau einmal vorkommen. Die Tafel ruft alle seltenen Arten des
Monats mit Bild und Anzahl auf; dein Satz dazu fasst sie zusammen, ohne alle
Namen aufzuzaehlen - die Tafel zeigt sie ja.

`veo:` kommt GENAU EINMAL vor, als Hoehepunkt des grossen Portraets. Hinter dem
Strich beschreibst du auf ENGLISCH in einem halben Satz, was der Vogel tut -
klein, ruhig, aus den QUELLEN abgeleitet und fuer eine Zeichnung machbar.
Gut: "the wagtail pumps its long tail slowly up and down". Schlecht: alles mit
Wasser, Beute, anderen Tieren oder Ortswechsel. Der Satz zu dieser Szene darf
laenger sein, 14 bis 18 Woerter.
Nutze `kalender:` genau einmal, `verhaeltnis:` genau einmal, `zugkarte:` nur
wenn die Zugbilanz erlaubt ist. Statistik-Tafeln (zahl, liste) hoechstens drei.

KERNSATZ
Ein Satz, der den Monat traegt. Alle Szenen dienen ihm.

Antworte NUR mit JSON:
{"titel":"Der Vogelmonat","kernsatz":"...","szenen":[{"text":"...","bild":"...","beleg":"..."}]}"""


def main():
    monat = sys.argv[1] if len(sys.argv) > 1 else None
    befehl = ['python3', f'{BASIS}/monat.py'] + ([monat] if monat else [])
    a = subprocess.run(befehl, capture_output=True, text=True, timeout=300)
    if a.returncode != 0:
        print('Monatsauswertung fehlgeschlagen:', a.stderr[:400], file=sys.stderr); return 1
    daten = json.loads(a.stdout)
    if not daten.get('ok'):
        print('Keine Monatsdaten', file=sys.stderr); return 1
    with open(f'{BASIS}/monat-aktuell.json', 'w', encoding='utf-8') as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)

    namen = []
    if daten.get('portraet'):
        namen.append(daten['portraet']['name'])
    namen += [x['name'] for x in (daten['verhaeltnis'].get('selten') or [])[:4]]
    namen += [x['name'] for x in (daten['bogen'].get('verabschiedet') or [])[:3]]
    namen = list(dict.fromkeys(namen))[:8]
    q = subprocess.run(['python3', f'{BASIS}/quellen.py'] + namen,
                       capture_output=True, text=True, timeout=300)
    quellen = json.loads(q.stdout) if q.stdout.strip() else {}

    prompt = (ANWEISUNG + '\n\n=== MESSDATEN AUS DEM GARTEN ===\n'
              + json.dumps(daten, ensure_ascii=False, indent=1)
              + '\n\n=== QUELLEN ===\n'
              + '\n\n'.join(f"--- QUELLE: {k} ({v['quelle']}) ---\n{v['text']}"
                            for k, v in quellen.items()))

    key = open(f'{BASIS}/.gemini-key').read().strip()
    url = (f'https://generativelanguage.googleapis.com/v1beta/models/{MODELL}'
           f':generateContent?key={key}')
    koerper = json.dumps({
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'temperature': 0.8, 'maxOutputTokens': 40000,
                             'responseMimeType': 'application/json'},
    }).encode()
    req = urllib.request.Request(url, data=koerper,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=300) as r:
        antwort = json.load(r)

    k = (antwort.get('candidates') or [{}])[0]
    roh = ''.join(t.get('text', '') for t in (k.get('content') or {}).get('parts', [])
                  if not t.get('thought')).strip()
    if k.get('finishReason') != 'STOP':
        print(f"Abbruchgrund {k.get('finishReason')}", file=sys.stderr)
    if roh.startswith('```'):
        roh = roh.split('\n', 1)[1].rsplit('```', 1)[0]
    try:
        drehbuch = json.loads(roh)
    except json.JSONDecodeError as e:
        print(f'Kein gueltiges JSON ({e}):\n{roh[:900]}', file=sys.stderr); return 1
    if not drehbuch.get('szenen'):
        print('Drehbuch ohne Szenen', file=sys.stderr); return 1

    drehbuch['monat'] = daten['monat']
    drehbuch['datenlage'] = daten['datenlage']
    drehbuch['quellen'] = {k2: v['quelle'] for k2, v in quellen.items()}
    print(json.dumps(drehbuch, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
