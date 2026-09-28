#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spricht das Drehbuch. Jeder Abschnitt wird einzeln erzeugt, damit spaeter
die Bilder auf die Abschnitte getaktet werden koennen - der Schnitt braucht
die Dauer jedes Stuecks, nicht nur die Gesamtlaenge.

Laeuft vollstaendig auf dem Server, ohne Netz und ohne Konto. Was einmal
heruntergeladen ist, funktioniert auch in fuenf Jahren noch.
"""
import json, os, sys, wave

STIMMEN = {
    'thorsten': '/srv/avian/wochenschau/stimme/de_DE-thorsten-medium.onnx',
    'kerstin':  '/srv/avian/wochenschau/stimme/de_DE-kerstin-low.onnx',
}
PAUSE = 0.55            # Sekunden Stille zwischen den Abschnitten


def dauer(pfad):
    with wave.open(pfad) as w:
        return w.getnframes() / w.getframerate()


def main():
    drehbuch = json.load(open(sys.argv[1], encoding='utf-8'))
    name = sys.argv[2] if len(sys.argv) > 2 else 'thorsten'
    ziel = sys.argv[3] if len(sys.argv) > 3 else '/srv/avian/wochenschau/ton'
    nur = sys.argv[4] if len(sys.argv) > 4 else None      # optional: ein Abschnitt

    from piper import PiperVoice, SynthesisConfig
    stimme = PiperVoice.load(STIMMEN[name])
    # Etwas langsamer als die Voreinstellung - das Publikum ist nicht in Eile.
    try:
        klang = SynthesisConfig(length_scale=1.08)
    except TypeError:
        klang = None

    os.makedirs(ziel, exist_ok=True)
    teile, gesamt = [], 0.0
    for s in drehbuch['segmente']:
        if nur and s['rolle'] != nur:
            continue
        datei = os.path.join(ziel, f"{name}-{s['rolle']}.wav")
        with wave.open(datei, 'wb') as w:
            stimme.synthesize_wav(s['text'], w, syn_config=klang)
        d = dauer(datei)
        gesamt += d + PAUSE
        teile.append({'rolle': s['rolle'], 'datei': datei, 'sekunden': round(d, 2)})
        print(f"  {s['rolle']:14s} {d:5.1f}s  {datei}", file=sys.stderr)

    if not nur:
        # Alles zusammensetzen, mit Atempausen dazwischen
        with wave.open(teile[0]['datei']) as w:
            rahmen, kanaele, breite = w.getframerate(), w.getnchannels(), w.getsampwidth()
        stille = b'\x00' * int(rahmen * PAUSE) * kanaele * breite
        ganz = os.path.join(ziel, f'{name}-ganz.wav')
        with wave.open(ganz, 'wb') as aus:
            aus.setnchannels(kanaele); aus.setsampwidth(breite); aus.setframerate(rahmen)
            for i, t in enumerate(teile):
                with wave.open(t['datei']) as w:
                    aus.writeframes(w.readframes(w.getnframes()))
                if i < len(teile) - 1:
                    aus.writeframes(stille)
        print(f"\n  GESAMT {dauer(ganz):.1f}s -> {ganz}", file=sys.stderr)
        print(json.dumps({'stimme': name, 'ganz': ganz, 'teile': teile,
                          'gesamt_sekunden': round(dauer(ganz), 2)}, ensure_ascii=False))
    else:
        print(json.dumps({'stimme': name, 'teile': teile}, ensure_ascii=False))


if __name__ == '__main__':
    main()
