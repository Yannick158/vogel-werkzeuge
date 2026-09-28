#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Schreibt das Drehbuch fuer die Vogelwoche.

Das Sprachmodell bekommt AUSSCHLIESSLICH zwei Dinge zu sehen: die gemessenen
Zahlen aus dem Garten und woertliche Auszuege aus der Wikipedia. Alles andere
waere Erfindung. Zu jedem Abschnitt muss es den Quellsatz nennen, auf den es
sich stuetzt - daran laesst sich hinterher stichprobenartig pruefen.
"""
import json, os, subprocess, sys, urllib.request

BASIS   = '/srv/avian/wochenschau'
MODELL  = 'gemini-3.5-flash'
SCHLUESSEL = BASIS + '/.gemini-key'

ANWEISUNG = """Du schreibst das Drehbuch fuer "Die Vogelwoche" - einen Wochenrueckblick, der
sonntagabends auf einer Wandanzeige im Wohnzimmer laeuft. Das Publikum sind zwei aeltere
Menschen in Zweitstandort, in deren Garten ein Mikrofon steht, das Voegel am Gesang erkennt.

TON
Warm und ruhig, wie jemand, der abends erzaehlt, was er beobachtet hat. Kein Werbeton,
keine Ausrufezeichen, keine rhetorischen Fragen. Ein bairischer Anklang ist erlaubt,
aber hoechstens einmal in der ganzen Sendung. Sprich von "Ihrem Garten".
Vermeide bewertende Fuellwoerter wie faszinierend, beeindruckend, erstaunlich.

WAHRHEIT - die wichtigste Vorgabe
- Zahlen zum Garten stammen ausschliesslich aus MESSDATEN.
- Jede Aussage ueber eine Vogelart muss durch QUELLEN gedeckt sein.
- Was dort nicht steht, wird nicht gesagt. Lieber eine Szene weniger.
- Nenne zu jeder Szene unter "beleg" den Satz aus den QUELLEN, auf den du dich
  stuetzt - woertlich. Kommt es aus dem Garten, schreibe "Messdaten".

ZAEHLUNG - haeufigster Fehler
Die Zahlen aus MESSDATEN sind ERKENNUNGEN: einzelne Rufe, die das Mikrofon gezaehlt
hat. Sie sind NIEMALS die Anzahl der Voegel. "einhundert Buntspechte" waere falsch -
richtig ist "hundertmal war der Buntspecht zu hoeren".

SELTEN heisst immer: selten IN DIESEM GARTEN, nie selten als Art.

ZAHLEN
Runde grosse Zahlen im gesprochenen Text - "gut fuenfzehnhundert Rufe" statt
"eintausendfuenfhundertneun". Bis hundert bleibt es genau. Zahlen als Woerter, nicht
als Ziffern - der Text wird vorgelesen. Fakten aus den QUELLEN bleiben exakt.

STATISTIK SPARSAM - wichtig
Hoechstens ZWEI Szenen mit nackten Zahlen: die Anzahl der Arten und die Rangliste
der haeufigsten. Das reicht. KEINE Gesamtzahl aller Rufe, KEIN lautester Tag,
keine weiteren Kennzahlen - das wirkt wie ein Kontoauszug.
Der frei gewordene Platz gehoert den Voegeln: stelle MINDESTENS VIER verschiedene
Arten vor, jede mit einer eigenen Szene und einer Angabe aus den QUELLEN. Auch die
haeufigen Gartenvoegel aus dem Chor duerfen einen kurzen Auftritt bekommen.
Zahlen duerfen im Erzaehltext weiter vorkommen ("hundertmal war der Buntspecht zu
hoeren") - nur nicht als eigene Zahlentafel.

AUFBAU IN SZENEN - das ist neu und wichtig
Die Sendung besteht aus 16 bis 20 kurzen SZENEN. Jede Szene ist EIN Gedanke, EIN
gesprochener Satz und EIN Bild. Kein Bild steht laenger als fuenf Sekunden.

Je Szene 6 bis 16 Woerter. Das entspricht bei ruhigem Sprechtempo drei bis sieben
Sekunden. Laengere Saetze teilst du auf zwei Szenen auf.

REIHENFOLGE - steigende Wirkung
Ordne so, dass es sich steigert. Beginne NICHT mit der groessten Zahl - danach kann
nichts mehr kommen. Erst der Alltag im Garten, dann die Zahlen, dann der seltene
Gast als Hoehepunkt, dann sein Weg in den Sueden, zum Schluss ein ruhiger Ausklang.

RHYTHMUS
Wechsle die Satzlaenge. Manche Szene ist ein Vierwortsatz. Hoechstens ZWEI VON DREI
Saetzen duerfen mit dem Subjekt beginnen - stell sonst eine Zeitangabe, einen Ort
oder einen Nebensatz voran.

FAKTENDICHTE
Fuer jeden vorgestellten Vogel mindestens ZWEI konkrete Angaben aus den QUELLEN:
Zahlen, Entfernungen, Groessen, Zeitraeume oder ein genau beschriebenes Verhalten.
Nimm die ueberraschende Einzelheit, nicht den allgemeinen Satz.

BILDER - waehle je Szene genau eine Form aus dieser Liste
  titel:<Text>                    Schrifttafel, fuer Anfang und Ende
  vogel:<wissenschaftlicher Name>  die Zeichnung der Art, ruhig bewegt
  flug:<wissenschaftlicher Name>   die Art im Flug, quer durchs Bild
  zahl:<Zahl>|<Beschriftung>       eine grosse Zahl, die hochzaehlt
  liste:<Name>=<Zahl>,<Name>=<Zahl>  Rangliste, nacheinander eingeblendet
  karte:<Ort>|<Ziel>               Zugweg als gestrichelter Bogen
Nutze wissenschaftliche Namen genau so, wie sie in den MESSDATEN stehen.
Das Bild muss zu dem passen, was in DIESER Szene gesagt wird - nicht dekorieren.

KERNSATZ
Gib ausserdem einen "kernsatz" an: der eine Gedanke, den diese Woche traegt, in
einem Satz. Alle Szenen sollen ihm dienen.

Antworte NUR mit JSON, ohne Vorrede und ohne Code-Zaun:
{"titel":"Die Vogelwoche","kernsatz":"...","szenen":[
  {"text":"...","bild":"titel:Ihre Vogelwoche","beleg":"Messdaten"}, ...]}"""


def lauf(befehl):
    return subprocess.run(befehl, capture_output=True, text=True, timeout=120)


def main():
    a = lauf(['python3', BASIS + '/auswahl.py'])
    if a.returncode != 0:
        print('Auswahl fehlgeschlagen:', a.stderr, file=sys.stderr); return 1
    besetzung = json.loads(a.stdout)
    if not besetzung.get('ok'):
        print('Keine Besetzung:', besetzung, file=sys.stderr); return 1

    rollen = [besetzung.get(r) for r in ('besonderheit', 'durchreise', 'portraet')]
    namen = [r['name'] for r in rollen if r]
    q = lauf(['python3', BASIS + '/quellen.py'] + namen)
    quellen = json.loads(q.stdout) if q.stdout.strip() else {}

    messdaten = {
        'zeitraum': f"{besetzung['von']} bis {besetzung['bis']}",
        'arten_diese_woche': besetzung['arten_diese_woche'],
        'erkennungen_diese_woche': besetzung['erkennungen_diese_woche'],
        'lautester_tag': besetzung.get('lautester_tag'),
        'chor': [{'name': c['name'], 'erkennungen': c['woche_n']} for c in besetzung['chor']],
        'besonderheit': besetzung.get('besonderheit'),
        'durchreise': besetzung.get('durchreise'),
        'portraet': besetzung.get('portraet'),
    }
    quelltext = '\n\n'.join(
        f"--- QUELLE: {k} ({v['quelle']}) ---\n{v['text']}" for k, v in quellen.items())

    prompt = (ANWEISUNG
              + '\n\n=== MESSDATEN AUS DEM GARTEN ===\n'
              + json.dumps(messdaten, ensure_ascii=False, indent=1)
              + '\n\n=== QUELLEN ===\n' + quelltext)

    with open(SCHLUESSEL) as f:
        key = f.read().strip()
    url = (f'https://generativelanguage.googleapis.com/v1beta/models/{MODELL}'
           f':generateContent?key={key}')
    koerper = json.dumps({
        'contents': [{'parts': [{'text': prompt}]}],
        # Die 3er-Modelle denken vor dem Antworten, und dieses Nachdenken zaehlt
        # gegen maxOutputTokens. Bei 2000 brach das Drehbuch mitten im JSON ab.
        'generationConfig': {'temperature': 0.8, 'maxOutputTokens': 24000,
                             'responseMimeType': 'application/json'},
    }).encode()
    req = urllib.request.Request(url, data=koerper, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=90) as r:
        antwort = json.load(r)

    kandidat = (antwort.get('candidates') or [{}])[0]
    grund = kandidat.get('finishReason')
    teile = (kandidat.get('content') or {}).get('parts') or []
    # Gedanken-Teile enthalten keinen Antworttext und werden uebersprungen
    roh = ''.join(t.get('text', '') for t in teile if not t.get('thought'))
    if grund != 'STOP':
        print(f'Abbruchgrund {grund} - Antwort unvollstaendig', file=sys.stderr)
    if not roh.strip():
        print('Leere Antwort:', json.dumps(antwort)[:600], file=sys.stderr); return 1
    roh = roh.strip()
    if roh.startswith('```'):                      # Code-Zaun abstreifen
        roh = roh.split('\n', 1)[1].rsplit('```', 1)[0]

    try:
        drehbuch = json.loads(roh)
    except json.JSONDecodeError as e:
        print(f'Kein gueltiges JSON ({e}). Antwort war:\n{roh[:1200]}', file=sys.stderr)
        return 1
    if 'szenen' not in drehbuch:
        print('Antwort ohne Szenen:', json.dumps(drehbuch)[:400], file=sys.stderr)
        return 1
    drehbuch['kalenderwoche'] = besetzung['kalenderwoche']
    drehbuch['besetzung'] = {r: besetzung.get(r) for r in
                             ('chor', 'besonderheit', 'durchreise', 'portraet')}
    drehbuch['quellen'] = {k: v['quelle'] for k, v in quellen.items()}
    print(json.dumps(drehbuch, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
