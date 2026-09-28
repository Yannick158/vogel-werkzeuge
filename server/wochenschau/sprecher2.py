#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spricht die Szenen einzeln. Je Szene eine WAV, damit der Schnitt die
Einzeldauern kennt und die Untertitel nicht verrutschen.

length_scale 1.28: die Recherche nennt 2,3 Woerter je Sekunde als angenehmes
Erzaehltempo. Die erste Fassung lag bei 2,72 und klang gehetzt - fuer zwei
aeltere Zuhoerer der falsche Weg.
"""
import json, os, sys, wave

STIMMEN = {
    'thorsten': '/srv/avian/wochenschau/stimme/de_DE-thorsten-medium.onnx',
    'kerstin':  '/srv/avian/wochenschau/stimme/de_DE-kerstin-low.onnx',
}
TEMPO = 1.28


def main():
    drehbuch = json.load(open(sys.argv[1], encoding='utf-8'))
    name = sys.argv[2] if len(sys.argv) > 2 else 'thorsten'
    ziel = sys.argv[3] if len(sys.argv) > 3 else '/srv/avian/wochenschau/ton'
    os.makedirs(ziel, exist_ok=True)

    from piper import PiperVoice, SynthesisConfig
    stimme = PiperVoice.load(STIMMEN[name])
    klang = SynthesisConfig(length_scale=TEMPO)

    gesamt, woerter = 0.0, 0
    for i, sz in enumerate(drehbuch['szenen']):
        datei = os.path.join(ziel, f'{name}-{i:02d}.wav')
        with wave.open(datei, 'wb') as w:
            stimme.synthesize_wav(sz['text'], w, syn_config=klang)
        with wave.open(datei) as w:
            d = w.getnframes() / w.getframerate()
        gesamt += d + 0.35
        woerter += len(sz['text'].split())
        print(f'  {i:02d} {d:5.2f}s  {sz["text"][:56]}', file=sys.stderr)

    print(f'\n  {len(drehbuch["szenen"])} Szenen, {woerter} Woerter, '
          f'{gesamt:.0f}s -> {woerter/max(gesamt,1):.2f} Woerter/Sekunde',
          file=sys.stderr)
    print(json.dumps({'stimme': name, 'sekunden': round(gesamt, 1),
                      'tempo': round(woerter / max(gesamt, 1), 2)}))


if __name__ == '__main__':
    main()
