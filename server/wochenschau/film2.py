#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Baut die Wochenschau aus Szenen. Eine Szene = ein Gedanke = ein Bild.

Regeln aus der Recherche (iart-ai/explainer-video-skills), hier umgesetzt:
  - Die Stimme ist das Rueckgrat; jede Szene ist so lang wie ihr Satz.
  - Kein Bild steht laenger als noetig; ein Gedanke je Szene.
  - Schrittweise Enthuellung statt fertiger Tafeln.
  - Untertitel sind Pflicht - hier am Ende in einem Durchgang eingebrannt,
    getaktet aus denselben Dauern wie der Schnitt, damit nichts verrutscht.
  - Farbe bedeutet etwas und wechselt nie: Tinte fuer Inhalt, Matt fuer Beiwerk.
"""
import json, math, os, re, shutil, subprocess, sys, wave
from PIL import Image, ImageDraw, ImageFont

BASIS = '/srv/avian/wochenschau'
ILL   = '/srv/avian/BirdNET-Pi/avian/assets/illustrations'
FONT  = '/srv/avian/BirdNET-Pi/avian/frontend/fonts/Caveat.ttf'
GRAIN = '/srv/avian/BirdNET-Pi/avian/frontend/grain.png'
B, H, FPS = 1280, 720, 25
PAPIER, TINTE, MATT = (250, 249, 246), (42, 36, 30), (140, 130, 118)
_korn = None


def grund():
    """Papier mit Struktur - einmal bauen, dann kopieren."""
    global _korn
    if _korn is None:
        p = Image.new('RGB', (B, H), PAPIER)
        k = Image.open(GRAIN).convert('RGB').resize((B, H))
        _korn = Image.blend(p, k, 0.10)
    return _korn.copy()


def schrift(px):
    return ImageFont.truetype(FONT, px)


def mitte(d, text, y, f, farbe=TINTE):
    b = d.textbbox((0, 0), text, font=f)
    d.text(((B - (b[2] - b[0])) / 2, y), text, font=f, fill=farbe)


def sanft(s):
    """Weiches Ein- und Auslaufen statt linearem Ruck."""
    return s * s * (3 - 2 * s)


# ---------------------------------------------------------------- Bildformen

def tafel_titel(text, s):
    bild = grund(); d = ImageDraw.Draw(bild)
    a = min(1.0, s / 0.25) if s < 0.25 else (1.0 if s < 0.85 else max(0.0, (1 - s) / 0.15))
    if a > 0:
        f = schrift(76)
        b = d.textbbox((0, 0), text, font=f)
        lage = int(H / 2 - 46 + (1 - sanft(a)) * 18)
        d.text(((B - (b[2] - b[0])) / 2, lage), text, font=f,
               fill=tuple(int(p + (250 - p) * (1 - a)) for p in TINTE))
    return bild


def tafel_zahl(wert, beschriftung, s):
    """Grosse Zahl, die hochzaehlt - in der ersten Haelfte, dann steht sie."""
    bild = grund(); d = ImageDraw.Draw(bild)
    lauf = min(1.0, s / 0.45)
    jetzt = int(round(wert * sanft(lauf)))
    f = schrift(190)
    b = d.textbbox((0, 0), f'{jetzt:,}'.replace(',', '.'), font=f)
    d.text(((B - (b[2] - b[0])) / 2, H / 2 - 150), f'{jetzt:,}'.replace(',', '.'),
           font=f, fill=TINTE)
    if s > 0.30:
        a = min(1.0, (s - 0.30) / 0.20)
        mitte(d, beschriftung, H / 2 + 70, schrift(52),
              tuple(int(p + (250 - p) * (1 - a)) for p in MATT))
    return bild


def tafel_liste(eintraege, s):
    """Rangliste, nacheinander - erst der Name, der Balken waechst mit."""
    bild = grund(); d = ImageDraw.Draw(bild)
    if not eintraege:
        return bild
    hoechst = max(v for _, v in eintraege) or 1
    f, fk = schrift(46), schrift(38)
    y0, schritt = 200, 110
    for i, (name, wert) in enumerate(eintraege[:4]):
        an = i * 0.16
        if s < an:
            continue
        a = min(1.0, (s - an) / 0.22)
        y = y0 + i * schritt
        d.text((190, y - 6), name, font=f, fill=TINTE)
        breite = int(520 * (wert / hoechst) * sanft(a))
        d.rounded_rectangle([560, y + 6, 560 + max(4, breite), y + 44], 8, fill=MATT)
        if a > 0.6:
            d.text((560 + max(4, breite) + 18, y), str(wert), font=fk, fill=MATT)
    return bild


def tafel_karte(von, nach, s):
    """Zugweg. Schrittweise: erst die Orte, dann der Bogen, dann der Vogel -
    Knoten, Kanten, Beschriftung, genau in dieser Reihenfolge."""
    bild = grund(); d = ImageDraw.Draw(bild)
    A, Z = (940, 190), (330, 430)
    bx, by = -70, 45

    def auf(t):
        w = 4 * t * (1 - t)
        return (A[0] + (Z[0] - A[0]) * t + bx * w, A[1] + (Z[1] - A[1]) * t + by * w)

    f = schrift(46)
    # Beide Beschriftungen OBERHALB ihres Punktes - unten laufen sie sonst in
    # die Untertitel hinein, was im ersten Durchgang passiert ist.
    for i, ((x, y), name, oben) in enumerate(((A, von, True), (Z, nach, True))):
        an = 0.05 + i * 0.12
        if s < an:
            continue
        a = min(1.0, (s - an) / 0.15)
        r = int(9 * sanft(a))
        d.ellipse([x - r, y - r, x + r, y + r], fill=TINTE)
        d.ellipse([x - 18, y - 18, x + 18, y + 18], outline=MATT, width=2)
        if a > 0.5:
            b = d.textbbox((0, 0), name, font=f)
            d.text((x - (b[2] - b[0]) / 2, y - 78 if oben else y + 30), name,
                   font=f, fill=TINTE)
    if s > 0.32:                                   # Bogen zeichnet sich
        weit = min(1.0, (s - 0.32) / 0.30)
        an = True
        for i in range(int(200 * weit)):
            p, q = auf(i / 200), auf((i + 1) / 200)
            if an:
                d.line([p, q], fill=MATT, width=3)
            if i % 5 == 0:
                an = not an
    return bild



# ------------------------------------------------ Tafeln fuer das Monatsvideo
# Diese drei brauchen mehr Angaben, als in eine Bildzeile passen. Sie lesen
# deshalb direkt aus der Monatsauswertung (monat.py).

MONAT_DATEI = BASIS + '/monat-aktuell.json'
_monat = None


def monatsdaten():
    global _monat
    if _monat is None:
        try:
            _monat = json.load(open(MONAT_DATEI, encoding='utf-8'))
        except Exception as e:
            print(f'  Monatsdaten fehlen: {e}', file=sys.stderr)
            _monat = {}
    return _monat


def tafel_kalender(s):
    """Wann war wer da. Zeilen sind Arten, die Breite ist der Tagesverlauf.
    Jede Art bekommt einen Punkt bei ihrer liebsten Stunde - dazu ein feiner
    Balken ueber die Stunden, in denen sie ueberhaupt gehoert wurde."""
    bild = grund(); d = ImageDraw.Draw(bild)
    daten = (monatsdaten().get('kalender') or [])[:7]
    if not daten:
        return bild
    f, fk = schrift(38), schrift(26)
    x0, x1, y0, schritt = 300, 1130, 168, 74
    # Stundenachse - nur alle sechs Stunden beschriften, sonst wird es unruhig
    for st in (0, 6, 12, 18, 24):
        x = x0 + (x1 - x0) * st / 24
        d.line([(x, y0 - 26), (x, y0 - 14)], fill=MATT, width=2)
        b = d.textbbox((0, 0), f'{st}', font=fk)
        d.text((x - (b[2] - b[0]) / 2, y0 - 62), f'{st} Uhr' if st in (0, 12) else f'{st}',
               font=fk, fill=MATT)
    for i, k in enumerate(daten):
        an = i * 0.11
        if s < an:
            continue
        a = min(1.0, (s - an) / 0.20)
        y = y0 + i * schritt
        d.text((150, y - 22), k['name'], font=f, fill=TINTE)
        d.line([(x0, y), (x0 + (x1 - x0) * sanft(a), y)], fill=(226, 221, 212), width=6)
        st = k.get('lieblingsstunde')
        if st is not None and a > 0.5:
            x = x0 + (x1 - x0) * st / 24
            r = int(13 * sanft(min(1.0, (a - 0.5) / 0.5)))
            d.ellipse([x - r, y - r, x + r, y + r], fill=TINTE)
    return bild


def tafel_zugkarte(s):
    """Alle Abschiede des Monats auf einem Blatt: von Zweitstandort aus faechern
    sich die Wege zu den Winterquartieren auf."""
    bild = grund(); d = ImageDraw.Draw(bild)
    ziele = (monatsdaten().get('zugziele') or [])[:5]
    A = (990, 180)
    f, fk = schrift(42), schrift(32)
    d.ellipse([A[0] - 10, A[1] - 10, A[0] + 10, A[1] + 10], fill=TINTE)
    d.ellipse([A[0] - 20, A[1] - 20, A[0] + 20, A[1] + 20], outline=MATT, width=2)
    b = d.textbbox((0, 0), 'Zweitstandort', font=f)
    d.text((A[0] - (b[2] - b[0]) / 2, A[1] - 72), 'Zweitstandort', font=f, fill=TINTE)
    if not ziele:
        return bild
    for i, z in enumerate(ziele):
        an = 0.10 + i * 0.15
        if s < an:
            continue
        a = min(1.0, (s - an) / 0.22)
        Z = (250, 300 + i * 88)
        weit = sanft(a)
        punkte = []
        for t in range(41):
            u = (t / 40) * weit
            w = 4 * u * (1 - u)
            punkte.append((A[0] + (Z[0] - A[0]) * u - 40 * w,
                           A[1] + (Z[1] - A[1]) * u + 30 * w))
        an_strich = True
        for p, q in zip(punkte, punkte[1:]):
            if an_strich:
                d.line([p, q], fill=MATT, width=2)
            an_strich = not an_strich if punkte.index(p) % 4 == 0 else an_strich
        if a > 0.75:
            d.ellipse([Z[0] - 8, Z[1] - 8, Z[0] + 8, Z[1] + 8], fill=TINTE)
            d.text((Z[0] + 22, Z[1] - 20), f"{z.get('name','')} → {z.get('ziel','')}",
                   font=fk, fill=TINTE)
    return bild


def tafel_verhaeltnis(s):
    """Seltene gegen haeufige Arten als Punktfeld: gefuellt heisst haeufig,
    offen heisst selten. Farbe bedeutet hier nichts - nur die Fuellung."""
    bild = grund(); d = ImageDraw.Draw(bild)
    v = monatsdaten().get('verhaeltnis') or {}
    n_selten, n_haeufig = v.get('anzahl_selten', 0), v.get('anzahl_haeufig', 0)
    gesamt = n_selten + n_haeufig
    if not gesamt:
        return bild
    f, fk = schrift(46), schrift(32)
    proSpalte, r, luecke = 6, 17, 52
    x0, y0 = 240, 210
    for i in range(gesamt):
        an = i / max(1, gesamt) * 0.6
        if s < an:
            continue
        a = min(1.0, (s - an) / 0.16)
        sp, ze = divmod(i, proSpalte)
        x, y = x0 + sp * luecke, y0 + ze * luecke
        rr = int(r * sanft(a))
        if i < n_haeufig:
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=MATT)
        else:
            d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=TINTE, width=3)
    if s > 0.55:
        d.ellipse([760, 232, 794, 266], fill=MATT)
        d.text((812, 228), f'{n_haeufig} häufige Arten', font=f, fill=TINTE)
        d.ellipse([760, 322, 794, 356], outline=TINTE, width=3)
        d.text((812, 318), f'{n_selten} seltene Arten', font=f, fill=TINTE)
        d.text((812, 396), 'selten heißt: selten in Ihrem Garten', font=fk, fill=MATT)
    return bild


def tafel_seltene(s):
    """Appell aller seltenen Arten des Monats - jede mit ihrer Zeichnung, ihrem
    Namen und der Zahl der Erkennungen. Sie erscheinen nacheinander und bleiben
    dann stehen, damit am Ende alle zusammen zu sehen sind. Das Raster passt
    sich der Anzahl an; bei vielen Arten werden die Bilder kleiner statt dass
    Arten wegfallen - der Nutzer wollte sie vollstaendig sehen."""
    bild = grund(); d = ImageDraw.Draw(bild)
    arten = ((monatsdaten().get('verhaeltnis') or {}).get('selten') or [])
    if not arten:
        return bild
    n = len(arten)
    spalten = 3 if n <= 6 else (4 if n <= 12 else 6)
    zeilen = (n + spalten - 1) // spalten
    kante = 210 if spalten == 3 else (160 if spalten == 4 else 112)
    fn = schrift(34 if spalten <= 4 else 24)
    fz = schrift(26 if spalten <= 4 else 20)

    d.text((92, 62), 'Selten gehört in diesem Monat', font=schrift(44), fill=TINTE)
    breite = spalten * kante
    x0 = (B - breite) // 2
    y0 = max(150, (H - zeilen * (kante + 34)) // 2 + 40)

    for i, a in enumerate(arten):
        an = i / max(1, n) * 0.72
        if s < an:
            continue
        p = min(1.0, (s - an) / 0.20)
        sp, ze = i % spalten, i // spalten
        mx = x0 + sp * kante + kante // 2
        my = y0 + ze * (kante + 34)

        pfad = f"{ILL}/{slug(a.get('sci',''))}.png"
        if os.path.isfile(pfad):
            try:
                v = Image.open(pfad).convert('RGBA')
                hoehe = int((kante - 62) * sanft(p))
                if hoehe > 4:
                    br = max(4, int(v.width * hoehe / v.height))
                    v = v.resize((br, hoehe), Image.LANCZOS)
                    if p < 1.0:                       # sanft einblenden
                        alpha = v.getchannel('A').point(lambda q: int(q * sanft(p)))
                        v.putalpha(alpha)
                    bild.paste(v, (mx - br // 2, my - hoehe // 2), v)
            except Exception:
                pass
        if p > 0.55:
            name = a.get('name', '')
            # Lange Namen wie "Flussuferlaeufer" sprengen das Feld und laufen in
            # den Nachbarn. Schrift so weit verkleinern, bis der Name passt.
            fn_hier, gr = fn, fn.size
            while gr > 15:
                b = d.textbbox((0, 0), name, font=fn_hier)
                if (b[2] - b[0]) <= kante - 14:
                    break
                gr -= 2
                fn_hier = schrift(gr)
            b = d.textbbox((0, 0), name, font=fn_hier)
            d.text((mx - (b[2] - b[0]) / 2, my + kante // 2 - 34), name, font=fn_hier, fill=TINTE)
            mal = f"{a.get('n', 0)}\u00d7"
            b2 = d.textbbox((0, 0), mal, font=fz)
            d.text((mx - (b2[2] - b2[0]) / 2, my + kante // 2 - 4), mal, font=fz, fill=MATT)
    return bild

# ------------------------------------------------------------- Szenenbau

def aus_pil(zeichner, dauer, ziel):
    """Bildfolge mit Pillow erzeugen und kodieren."""
    ordner = ziel + '.frames'
    shutil.rmtree(ordner, ignore_errors=True); os.makedirs(ordner)
    n = max(1, int(dauer * FPS))
    for i in range(n):
        zeichner(i / max(1, n - 1)).save(f'{ordner}/{i:04d}.png')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(FPS),
                    '-i', f'{ordner}/%04d.png', '-c:v', 'libx264', '-preset', 'veryfast',
                    '-crf', '23', '-pix_fmt', 'yuv420p', ziel], check=True)
    shutil.rmtree(ordner, ignore_errors=True)
    return ziel


def aus_vogel(pfade, dauer, ziel, fliegend=False):
    """Zeichnung ueber Papier bewegen. Die Illustrationen schauen nach LINKS -
    wer nach rechts fliegt, muss gespiegelt werden, sonst fliegt er rueckwaerts.
    Genau das war im ersten Entwurf falsch."""
    hg = ziel + '.bg.png'
    grund().save(hg)
    ein = ['-loop', '1', '-i', hg]
    for p in pfade:
        ein += ['-loop', '1', '-i', p]
    if fliegend:
        # Rechts nach links - das ist die natuerliche Blickrichtung der
        # Zeichnungen, so muss nichts gespiegelt werden.
        # Kleiner und mit weichem Ein- und Auslauf: bei 380 px Hoehe war der
        # Turmfalke fast schirmbreit und das Vorbeiziehen wirkte ruppig.
        e = f'({dauer and f"(t/{dauer})*(t/{dauer})*(3-2*(t/{dauer}))"})'
        x = f"'W+w-(W+2*w)*{e}'"
        y = f"'(H-h)/2-30+14*sin(t*0.9)'"
        k = [f'[1:v]scale=-1:250,format=rgba[a]', f'[2:v]scale=-1:250,format=rgba[b]',
             f"[0:v][a]overlay=x={x}:y={y}:enable='lt(mod(t,0.4),0.2)'[s1]",
             f"[s1][b]overlay=x={x}:y={y}:enable='gte(mod(t,0.4),0.2)'[v]"]
    else:
        k = [f'[1:v]scale=-1:470,format=rgba[a]',
             f"[0:v][a]overlay=x='(W-w)/2+34*sin(t/3)':y='(H-h)/2-20+10*sin(t*0.9)'[v]"]
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error'] + ein +
                   ['-filter_complex', ';'.join(k), '-map', '[v]', '-t', str(dauer),
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                    '-pix_fmt', 'yuv420p', '-r', str(FPS), ziel], check=True)
    os.remove(hg)
    return ziel


_namen_karte = None


def _deutsch_zu_wissenschaftlich(name):
    """Das Sprachmodell schreibt manchmal den deutschen Namen ins Bildfeld
    ("vogel:Buntspecht"), obwohl die Zeichnungen unter dem wissenschaftlichen
    Namen liegen. Statt dafuer stumm auf eine Titeltafel zurueckzufallen,
    schlagen wir in der Datenbank nach."""
    global _namen_karte
    if _namen_karte is None:
        import sqlite3
        _namen_karte = {}
        try:
            con = sqlite3.connect('file:/srv/pi-daten/birds.db?mode=ro', uri=True)
            for com, sci in con.execute('SELECT DISTINCT Com_Name, Sci_Name FROM detections'):
                _namen_karte[com.lower().strip()] = sci
        except Exception as e:
            print(f'  Namensnachschlag nicht moeglich: {e}', file=sys.stderr)
    return _namen_karte.get(name.lower().strip())


def slug(sci):
    return sci.lower().strip().replace(' ', '-')


def bild_bauen(spec, dauer, ziel):
    art, _, rest = spec.partition(':')
    art = art.strip()
    if art == 'titel':
        return aus_pil(lambda s: tafel_titel(rest.strip(), s), dauer, ziel)
    if art == 'zahl':
        wert, _, bez = rest.partition('|')
        zahl = int(re.sub(r'[^\d]', '', wert) or 0)
        return aus_pil(lambda s: tafel_zahl(zahl, bez.strip(), s), dauer, ziel)
    if art == 'liste':
        eintr = []
        for teil in rest.split(','):
            n, _, w = teil.partition('=')
            if n.strip():
                eintr.append((n.strip(), int(re.sub(r'[^\d]', '', w) or 0)))
        return aus_pil(lambda s: tafel_liste(eintr, s), dauer, ziel)
    if art == 'veo':
        # Der vorab erzeugte Veo-Clip als Hoehepunkt. Er kommt mit 24 Bildern
        # je Sekunde und 8 Sekunden Laenge; hier wird er auf unsere Bildrate
        # und die Laenge dieser Szene gebracht. Fehlt er, faellt die Szene auf
        # die gezeichnete Fassung zurueck - das Video entsteht auch ohne Geld.
        clip = BASIS + '/veo-held.mp4'
        if os.path.isfile(clip):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error',
                            '-stream_loop', '-1', '-i', clip,
                            '-t', str(dauer), '-an',
                            # Veo liefert ein graueres Papier als unsere Tafeln
                            # (gemessen 233,231,227 gegen 250,249,246). Ohne
                            # Angleich sieht man am Schnitt eine Kante. Die
                            # Weisswerte werden deshalb angehoben.
                            '-vf', f'scale={B}:{H}:force_original_aspect_ratio=increase,'
                                   f'crop={B}:{H},fps={FPS},'
                                   'colorlevels=rimax=0.914:romax=0.980'
                                   ':gimax=0.906:gomax=0.976'
                                   ':bimax=0.890:bomax=0.965',
                            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                            '-pix_fmt', 'yuv420p', ziel], check=True)
            return ziel
        print('  Veo-Clip fehlt - zeichne stattdessen', file=sys.stderr)
        art, rest = 'vogel', rest.split('|')[0]

    if art == 'kalender':
        return aus_pil(tafel_kalender, dauer, ziel)
    if art == 'zugkarte':
        return aus_pil(tafel_zugkarte, dauer, ziel)
    if art == 'seltene':
        return aus_pil(tafel_seltene, dauer, ziel)
    if art == 'verhaeltnis':
        return aus_pil(tafel_verhaeltnis, dauer, ziel)
    if art == 'karte':
        von, _, nach = rest.partition('|')
        return aus_pil(lambda s: tafel_karte(von.strip(), nach.strip(), s), dauer, ziel)
    if art in ('vogel', 'flug'):
        s = slug(rest)
        eins, zwei = f'{ILL}/{s}.png', f'{ILL}/{s}-2.png'
        if not os.path.isfile(eins):
            wiss = _deutsch_zu_wissenschaftlich(rest)
            if wiss:
                s = slug(wiss)
                eins, zwei = f'{ILL}/{s}.png', f'{ILL}/{s}-2.png'
                print(f'  "{rest.strip()}" -> {wiss}', file=sys.stderr)
        if not os.path.isfile(eins):
            return aus_pil(lambda p: tafel_titel(rest.strip(), p), dauer, ziel)
        if art == 'flug' and os.path.isfile(zwei):
            return aus_vogel([zwei, eins], dauer, ziel, fliegend=True)
        return aus_vogel([eins], dauer, ziel)
    return aus_pil(lambda s: grund(), dauer, ziel)


def umbrechen(text, max_zeilen=3):
    """Umbrechen, aber NIEMALS abschneiden. Die erste Fassung kappte die zweite
    Zeile hart bei 40 Zeichen - im Bild stand dann "vom Mikrofon erfa".
    Stattdessen wird die Zeilenbreite so lange erhoeht, bis der Satz passt."""
    def wickeln(breite):
        worte, zeilen, jetzt = text.split(), [], ''
        for w in worte:
            if len(jetzt) + len(w) + 1 <= breite:
                jetzt = (jetzt + ' ' + w).strip()
            else:
                zeilen.append(jetzt); jetzt = w
        if jetzt:
            zeilen.append(jetzt)
        return zeilen
    for breite in range(46, 90, 4):
        zeilen = wickeln(breite)
        if len(zeilen) <= max_zeilen:
            return zeilen
    return wickeln(88)[:max_zeilen]


def srt_zeit(t):
    st = int(t // 3600); mi = int((t % 3600) // 60); se = t % 60
    return f'{st:02d}:{mi:02d}:{se:06.3f}'.replace('.', ',')


def main():
    # Aufruf: film2.py <stimme> [drehbuch.json] [ziel.mp4] [tonordner]
    # Ohne Zusatzangaben baut es wie bisher die Wochenschau. Die Artenportraets
    # nutzen dieselbe Maschine mit anderen Pfaden.
    stimme = sys.argv[1] if len(sys.argv) > 1 else 'thorsten'
    db_pfad = sys.argv[2] if len(sys.argv) > 2 else f'{BASIS}/drehbuch-aktuell.json'
    ziel_video = sys.argv[3] if len(sys.argv) > 3 else f'{BASIS}/vogelwoche.mp4'
    ton_ordner = sys.argv[4] if len(sys.argv) > 4 else f'{BASIS}/ton'
    drehbuch = json.load(open(db_pfad, encoding='utf-8'))
    ton = f'{ton_ordner}/{stimme}'
    arbeit = f'{BASIS}/arbeit-' + os.path.basename(ziel_video).replace('.mp4', '')
    shutil.rmtree(arbeit, ignore_errors=True); os.makedirs(arbeit)

    teile, srt, uhr = [], [], 0.0
    for i, sz in enumerate(drehbuch['szenen']):
        wav = f'{ton}-{i:02d}.wav'
        if not os.path.isfile(wav):
            continue
        with wave.open(wav) as w:
            d = round(w.getnframes() / w.getframerate() + 0.35, 2)   # Atempause
        ziel = f'{arbeit}/{i:02d}.mp4'
        bild_bauen(sz.get('bild', ''), d, ziel)
        teile.append((ziel, wav, d))
        zeilen = umbrechen(sz['text'])
        srt.append(f"{len(srt)+1}\n{srt_zeit(uhr+0.15)} --> {srt_zeit(uhr+d-0.15)}\n"
                   + '\n'.join(zeilen) + '\n')
        uhr += d
        print(f'  {i:02d} {d:5.2f}s  {sz.get("bild","")[:38]}', file=sys.stderr)

    # Bild aneinander, Ton aneinander
    with open(f'{arbeit}/v.txt', 'w') as f:
        for t, _, _ in teile:
            f.write(f"file '{t}'\n")
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
                    '-i', f'{arbeit}/v.txt', '-c', 'copy', f'{arbeit}/bild.mp4'], check=True)

    ton_ein, ton_k = [], []
    for j, (_, wav, d) in enumerate(teile):
        ton_ein += ['-i', wav]
        ton_k.append(f'[{j}:a]apad=pad_dur=0.35[t{j}]')
    ton_k.append(''.join(f'[t{j}]' for j in range(len(teile)))
                 + f'concat=n={len(teile)}:v=0:a=1[a]')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error'] + ton_ein +
                   ['-filter_complex', ';'.join(ton_k), '-map', '[a]',
                    f'{arbeit}/ton.wav'], check=True)

    with open(f'{arbeit}/ut.srt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(srt))

    ziel = ziel_video
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error', '-i', f'{arbeit}/bild.mp4',
        '-i', f'{arbeit}/ton.wav',
        '-vf', (f"subtitles={arbeit}/ut.srt:fontsdir={os.path.dirname(FONT)}"
                ":force_style='FontName=Caveat,FontSize=19,PrimaryColour=&H1E242A&,"
                "OutlineColour=&HF6F9FA&,BorderStyle=1,Outline=2,Shadow=0,"
                "Alignment=2,MarginV=40'"),
        '-map', '0:v', '-map', '1:a', '-shortest',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k', ziel], check=True)
    print(f'\n  FERTIG {uhr:.0f}s -> {ziel}', file=sys.stderr)
    print(ziel)


if __name__ == '__main__':
    main()
