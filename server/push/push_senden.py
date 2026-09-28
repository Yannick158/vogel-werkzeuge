#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Schickt Web-Push-Benachrichtigungen, wenn neue Erkennungen in der
# hochgeladenen birds.db auftauchen. Laeuft alle 2 Minuten per systemd-Timer.
# Erstlauf legt nur den Zustand an (kein Nachrichten-Schwall).
#
# NEU (ARCHITEKTUR.md 3.7): Nach dem Web-Push-Teil werden auch die App-Geraete
# aus geraete-garten.json bedient - dieselbe Modus-Logik (neu/alle), Nutzdaten
# nach Vertrag, Versand ueber native_push.py (FCM/APNs). Fehlt die
# Push-Konfiguration, laeuft der Web-Teil unveraendert weiter (P-4).
#
# Alle Pfade lassen sich fuer Tests ueber Umgebungsvariablen umlenken;
# Standard ist jeweils der bisherige Live-Pfad.
import json, os, re, sqlite3, subprocess, sys, tempfile, time, urllib.parse
from pywebpush import webpush, WebPushException

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_push

BASIS   = '/srv/avian/push'
DB      = os.environ.get('PUSH_DB')      or '/srv/pi-daten/birds.db'
ABOS    = os.environ.get('PUSH_ABOS')    or (BASIS + '/abos-garten.json')
ZUSTAND = os.environ.get('PUSH_ZUSTAND') or (BASIS + '/zustand-garten.json')
HIDE    = os.environ.get('PUSH_HIDE')    or '/srv/avian/verstecken/garten.json'
GERAETE = os.environ.get('PUSH_GERAETE') or '/srv/avian/app/geraete-garten.json'
SCHLUESSEL = BASIS + '/vapid_private.pem'
KONTAKT = {'sub': 'mailto:you@example.org'}
URL     = 'https://garten.example.org/'
BILD_URL = URL + 'eigenes/push-bild.php?sci='
PUFFER   = '/srv/avian/vogelbilder'
HOLER    = '/srv/avian/BirdNET-Pi/avian/api/vogelbilder.php'
STANDORT = 'garten'


def bild_vorwaermen(sci):
    """Sorgt dafuer, dass das Foto der Art im Puffer liegt, BEVOR die
    Benachrichtigung rausgeht. Chrome holt das Symbol selbst und bricht ab,
    wenn es zu lange dauert - ein kalter Puffer hiesse also: kein Bild.
    Neue Arten sind selten, der Aufwand faellt praktisch nie an."""
    slug = re.sub(r'[^a-z0-9]+', '-', sci.lower())
    fertig = os.path.join(PUFFER, slug, '0.jpg')
    if os.path.isfile(fertig):
        return True
    # Fuer manche Arten gibt es keine frei lizenzierten Fotos. Ohne Merker
    # wuerden wir das alle zwei Minuten erneut bei iNaturalist anfragen.
    leer = os.path.join(PUFFER, slug + '.kein-foto')
    if os.path.isfile(leer) and (time.time() - os.path.getmtime(leer)) < 86400:
        return False
    # Zwei Schritte: erst die Liste holen lassen, dann das erste Bild selbst.
    # Ohne 'bild' liefert der Endpunkt nur die JSON-Liste und laedt kein Foto.
    code = ('$_GET["sci"]=$argv[1];'
            ' if ($argv[3] !== "") { $_GET["bild"] = $argv[3]; }'
            ' ob_start(); include $argv[2]; ob_end_clean();')
    for schritt in ('', '0'):
        try:
            subprocess.run(['php', '-r', code, '--', sci, HOLER, schritt],
                           timeout=90, capture_output=True, check=False)
        except Exception as e:
            print('Bild-Vorwaermen fehlgeschlagen (%s): %s' % (sci, e), file=sys.stderr)
            break
        if os.path.isfile(fertig):
            break
    if not os.path.isfile(fertig):
        try:
            os.makedirs(PUFFER, exist_ok=True)
            open(leer, 'w').close()
        except Exception:
            pass
    return os.path.isfile(fertig)

def lade(p, alt):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except Exception:
        return alt

def speichere(p, d):
    # tmp-Datei IM Zielverzeichnis anlegen: os.replace darf keine
    # Dateisystemgrenze ueberschreiten (die Geraete-Datei liegt woanders).
    ziel_dir = os.path.dirname(os.path.abspath(p)) or BASIS
    fd, tmp = tempfile.mkstemp(dir=ziel_dir); os.close(fd)
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(d, f, ensure_ascii=False, indent=1)
    # Rechte der bestehenden Datei uebernehmen (mkstemp legt 600 an) - sonst
    # verlieren wir beim Schreiben die 640 der Geraete-Datei.
    try:
        os.chmod(tmp, os.stat(p).st_mode & 0o777)
    except FileNotFoundError:
        pass
    os.replace(tmp, p)

con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
max_ts = con.execute("SELECT MAX(Date||' '||Time) FROM detections").fetchone()[0] or ''

zustand = lade(ZUSTAND, None)
if zustand is None:
    arten = [r[0] for r in con.execute('SELECT DISTINCT Sci_Name FROM detections')]
    speichere(ZUSTAND, {'letzte': max_ts, 'arten': sorted(arten)})
    print('Erstlauf: Zustand angelegt (%d Arten, bis %s)' % (len(arten), max_ts))
    sys.exit(0)

letzte = zustand.get('letzte') or ''
if not max_ts or max_ts <= letzte:
    sys.exit(0)

rows = con.execute(
    "SELECT Sci_Name, Com_Name, Date||' '||Time FROM detections "
    "WHERE Date||' '||Time > ? ORDER BY Date, Time", (letzte,)).fetchall()

versteckt = set((lade(HIDE, {}) or {}).get('arten') or [])
sichtbar  = [r for r in rows if r[0] not in versteckt]
bekannt   = set(zustand.get('arten') or [])

neue, zaehl, zaehl_sci = {}, {}, {}
for sci, com, ts in sichtbar:
    zaehl[com] = zaehl.get(com, 0) + 1
    zaehl_sci[sci] = zaehl_sci.get(sci, 0) + 1
    if sci not in bekannt and sci not in neue:
        neue[sci] = com

# Zustand IMMER fortschreiben (auch ohne Abos), damit nichts doppelt gemeldet wird.
zustand['letzte'] = max_ts
zustand['arten']  = sorted(bekannt | {r[0] for r in rows})
speichere(ZUSTAND, zustand)

abos = lade(ABOS, {}) or {}
app_geraete = lade(GERAETE, {}) or {}
# Ohne sichtbare Erkennungen gibt es nichts zu melden. Fehlen nur die
# Web-Abos, laeuft der App-Teil trotzdem (und umgekehrt).
if not sichtbar or (not abos and not app_geraete):
    sys.exit(0)

def text_neu():
    namen = list(neue.values())
    if len(namen) == 1:
        return ('Neuer Vogel im Garten!', namen[0] + ' — zum ersten Mal gehört')
    return ('%d neue Vögel im Garten!' % len(namen), ', '.join(namen))

def text_alle():
    teile = [k + (' ×%d' % v if v > 1 else '') for k, v in sorted(zaehl.items(), key=lambda x: -x[1])]
    n = sum(zaehl.values())
    titel = '1 Vogel gehört' if n == 1 else '%d Vögel gehört' % n
    return (titel, ', '.join(teile[:6]) + (' …' if len(teile) > 6 else ''))

# Symbole einmal vorwaermen, nicht je Abo.
#   Modus 'neu'  -> Foto der neuen Art
#   Modus 'alle' -> Foto der meistgehoerten Art dieses Durchgangs
def symbol(sci):
    if not sci:
        return None
    if bild_vorwaermen(sci):
        print('Symbol bereit: %s' % sci)
        return BILD_URL + urllib.parse.quote(sci)
    print('Kein Foto fuer %s - Taube als Ersatz' % sci)
    return None

bild_neu  = symbol(next(iter(neue)) if neue else None)
bild_alle = symbol(max(zaehl_sci, key=zaehl_sci.get) if zaehl_sci else None)

tote, gesendet = [], 0
for schl, abo in list(abos.items()):
    modus = abo.get('modus', 'neu')
    if modus == 'neu':
        if not neue: continue
        titel, text = text_neu()
    else:
        titel, text = text_alle()
    inhalt = {'titel': titel, 'text': text, 'url': URL, 'tag': 'vogel-' + modus}
    b = bild_neu if modus == 'neu' else bild_alle
    if b:
        inhalt['bild'] = b             # Symbol UND grosses Bild in der Meldung
    daten = json.dumps(inhalt, ensure_ascii=False)
    try:
        webpush(subscription_info=abo['abo'], data=daten,
                vapid_private_key=SCHLUESSEL, vapid_claims=dict(KONTAKT), ttl=3600)
        gesendet += 1
    except WebPushException as e:
        code = getattr(getattr(e, 'response', None), 'status_code', None)
        if code in (404, 410):
            tote.append(schl)   # Abo existiert nicht mehr (Browser abgemeldet)
        else:
            print('Push-Fehler %s' % code, file=sys.stderr)
    except Exception as e:
        print('Push-Ausnahme: %s' % e, file=sys.stderr)

if tote:
    for t in tote: abos.pop(t, None)
    speichere(ABOS, abos)

print('gesendet: %d | tote Abos entfernt: %d | neue Arten: %s | Erkennungen: %d'
      % (gesendet, len(tote), list(neue.values()) or '-', sum(zaehl.values())))


# --------------------------------------------------------------------------
# App-Geraete (nativer Push, ARCHITEKTUR.md 3.7)
# --------------------------------------------------------------------------
# Dieselbe Modus-Logik wie oben. Was hier schiefgeht, darf den Web-Push-Lauf
# nicht nachtraeglich entwerten - deshalb steht der Block am Ende.

def app_geraete_bedienen():
    if not app_geraete:
        return
    konf = native_push.lade_konfig()
    if not konf:
        print('nativer Push: keine Konfiguration - uebersprungen')
        return

    # Nutzdaten nach Vertrag 3.7: ziel 'art' nur, wenn es GENAU eine neue Art
    # gibt (dann traegt die Meldung deren sci), sonst 'start'.
    neue_sci = list(neue.keys())
    ein_neuer = neue_sci[0] if len(neue_sci) == 1 else None

    app_gesendet, app_tot, app_fehler = 0, 0, 0
    geaendert = False
    for gid, g in list(app_geraete.items()):
        if not isinstance(g, dict):
            continue
        modus = str(g.get('modus') or 'neu')
        if modus == 'aus':
            continue
        if modus == 'neu':
            if not neue:
                continue
            titel, text = text_neu()
            bild = bild_neu
            sci = ein_neuer
        else:
            titel, text = text_alle()
            bild = bild_alle
            sci = max(zaehl_sci, key=zaehl_sci.get) if zaehl_sci else None

        daten = {'titel': titel, 'text': text,
                 'ziel': 'art' if (modus == 'neu' and ein_neuer) else 'start',
                 'standort': STANDORT, 'tag': 'vogel-' + modus}
        if sci:
            daten['sci'] = sci
        if bild:
            daten['bild'] = bild

        ergebnis = native_push.sende(g, titel, text, daten, konfig=konf)
        if ergebnis == 'ok':
            app_gesendet += 1
        elif ergebnis == 'tot':
            # P-2: nur das Push-Token faellt weg, der Geraeteeintrag bleibt -
            # Modus und Berichte-Schalter sollen nicht verloren gehen.
            app_geraete[gid]['push'] = None
            app_tot += 1
            geaendert = True
        elif ergebnis == 'fehler':
            app_fehler += 1

    if geaendert:
        try:
            speichere(GERAETE, app_geraete)
        except Exception as e:
            print('Geraete-Datei nicht schreibbar: %s' % e, file=sys.stderr)
    print('App-Push: %d gesendet | %d Token abgemeldet | %d Fehler'
          % (app_gesendet, app_tot, app_fehler))


try:
    app_geraete_bedienen()
except Exception as e:
    print('App-Push uebersprungen: %s' % e, file=sys.stderr)
