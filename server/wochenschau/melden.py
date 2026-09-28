#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sagt per Push Bescheid, dass die neue Wochenschau da ist.
Geht an alle angemeldeten Geraete, unabhaengig von deren Modus - eine Sendung
pro Woche ist keine Belaestigung.

NEU (ARCHITEKTUR.md 3.7): Nach dem Web-Push-Teil bekommen auch die
App-Geraete mit 'berichte': true eine Meldung (ziel 'folge', kw). Fehlt die
native Push-Konfiguration, gibt es eine Logzeile und sonst nichts (P-4);
der Web-Teil bleibt davon unberuehrt.

Aufruf: melden.py <kw>   -  'M' am Anfang der kw = Monat statt Woche.
Pfade lassen sich fuer Tests ueber Umgebungsvariablen umlenken.
"""
import json, os, sys
from pywebpush import webpush, WebPushException

# PUSH_BASIS zeigt auf das Verzeichnis mit vapid_private.pem und
# native_push.py. Standard ist der Live-Pfad.
BASIS = os.environ.get('PUSH_BASIS') or '/srv/avian/push'
URL = 'https://garten.example.org/eigenes/wochenschau.html'
ABOS = os.environ.get('PUSH_ABOS') or (BASIS + '/abos-garten.json')
GERAETE = os.environ.get('PUSH_GERAETE') or '/srv/avian/app/geraete-garten.json'
STANDORT = 'garten'


def speichere(pfad, daten):
    """Atomar schreiben, Rechte der bestehenden Datei behalten."""
    tmp = pfad + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(tmp, os.stat(pfad).st_mode & 0o777)
    except FileNotFoundError:
        pass
    os.replace(tmp, pfad)


def titel_fuer(kw):
    """Monatsfolgen tragen ein 'M' am Anfang der kw (z. B. M2026-09)."""
    return 'Der Vogelmonat ist da' if str(kw).startswith('M') else 'Die Vogelwoche ist da'


def app_geraete_melden(kw, titel, text):
    """Native Meldung an alle App-Geraete mit berichte: true."""
    sys.path.insert(0, BASIS)
    try:
        import native_push
    except Exception as e:
        print(f'  nativer Push nicht verfuegbar ({e}) - uebersprungen')
        return
    try:
        with open(GERAETE, encoding='utf-8') as f:
            geraete = json.load(f)
    except Exception as e:
        print(f'  keine App-Geraete lesbar ({e}) - uebersprungen')
        return
    if not isinstance(geraete, dict) or not geraete:
        print('  keine App-Geraete - uebersprungen')
        return
    konf = native_push.lade_konfig()
    if not konf:
        print('  nativer Push: keine Konfiguration - uebersprungen')
        return

    daten = {'titel': titel, 'text': text, 'ziel': 'folge', 'kw': str(kw),
             'standort': STANDORT, 'tag': 'vogelfolge'}
    gesendet, tot, fehler = 0, 0, 0
    geaendert = False
    for gid, g in list(geraete.items()):
        if not isinstance(g, dict) or not g.get('berichte'):
            continue
        ergebnis = native_push.sende(g, titel, text, daten, konfig=konf)
        if ergebnis == 'ok':
            gesendet += 1
        elif ergebnis == 'tot':
            geraete[gid]['push'] = None   # Eintrag bleibt (P-2)
            tot += 1
            geaendert = True
        elif ergebnis == 'fehler':
            fehler += 1
    if geaendert:
        try:
            speichere(GERAETE, geraete)
        except Exception as e:
            print(f'  Geraete-Datei nicht schreibbar: {e}')
    print(f'  App-Push: {gesendet} gesendet | {tot} Token abgemeldet | {fehler} Fehler')


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else ''

    # --- Web-Push: unveraendert gegenueber dem Ist-Stand ---
    try:
        abos = json.load(open(ABOS, encoding='utf-8'))
    except Exception as e:
        # Frueher endete das Skript hier. Jetzt geht es weiter, damit ein
        # fehlendes Abo-Verzeichnis nicht die App-Geraete mit abschaltet.
        print(f'keine Abos lesbar: {e}')
        abos = {}
    daten = json.dumps({'titel': 'Die Vogelwoche ist da',
                        'text': 'Ihr Rückblick auf die vergangene Woche im Garten.',
                        'url': URL, 'tag': 'vogelwoche'}, ensure_ascii=False)
    gesendet = 0
    for schl, abo in list(abos.items()):
        try:
            webpush(subscription_info=abo['abo'], data=daten,
                    vapid_private_key=BASIS + '/vapid_private.pem',
                    vapid_claims={'sub': 'mailto:you@example.org'}, ttl=86400)
            gesendet += 1
        except WebPushException as e:
            code = getattr(getattr(e, 'response', None), 'status_code', None)
            print(f'  Abo {schl[:8]}: HTTP {code}')
    print(f'  Benachrichtigt: {gesendet} Geraet(e) fuer {kw}')

    # --- App-Geraete: Titel richtet sich nach Woche/Monat ---
    titel = titel_fuer(kw)
    text = ('Ihr Rückblick auf den vergangenen Monat im Garten.'
            if str(kw).startswith('M')
            else 'Ihr Rückblick auf die vergangene Woche im Garten.')
    try:
        app_geraete_melden(kw, titel, text)
    except Exception as e:
        print(f'  App-Push uebersprungen: {e}')


if __name__ == '__main__':
    main()
