#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ein Veo-Versuch mit EUREM Startbild.

Der Kniff: nicht "male mir eine Rauchschwalbe" (dann erfindet das Modell einen
beliebigen Vogel in beliebigem Stil), sondern die vorhandene Illustration als
erstes Einzelbild hineinreichen. Veo setzt sie dann in Bewegung - richtige Art,
richtiger Stil, passend zur Wand im Wohnzimmer.
"""
import base64, json, subprocess, sys, time, urllib.request, urllib.error

BASIS  = '/srv/avian/wochenschau'
MODELL = sys.argv[1] if len(sys.argv) > 1 else 'veo-3.1-lite-generate-preview'
SEK    = int(sys.argv[2]) if len(sys.argv) > 2 else 8
ILL    = '/srv/avian/BirdNET-Pi/avian/assets/illustrations'
GRAIN  = '/srv/avian/BirdNET-Pi/avian/frontend/grain.png'

PROMPT = (
    "A hand-painted Japanese woodblock-style bird illustration on textured "
    "off-white paper. The barn swallow gently beats its wings and glides slowly "
    "from left to right across the frame, its long forked tail trailing. "
    "The paper background stays perfectly still and flat. "
    "Calm, gentle, unhurried motion. Soft natural daylight. "
    "Painterly illustration style, muted natural colours, visible paper grain. "
    "No camera shake, no zoom, no text, no people, no watermark."
)

def startbild(ziel):
    """Startbild in genau der Optik unserer eigenen Einstellungen bauen."""
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'color=c=0xFAF9F6:s=1280x720',
        '-i', GRAIN, '-i', f'{ILL}/hirundo-rustica-2.png',
        '-filter_complex',
        '[1:v]scale=1280:720,format=rgba,colorchannelmixer=aa=0.14[k];'
        '[0:v][k]overlay[g];[2:v]scale=-1:430,format=rgba[v];'
        '[g][v]overlay=x=(W-w)/2:y=(H-h)/2-20',
        '-frames:v', '1', ziel], check=True)
    return ziel

def main():
    key = open(BASIS + '/.gemini-key').read().strip()
    bild = startbild('/tmp/veo-start.png')
    roh = base64.b64encode(open(bild, 'rb').read()).decode()
    print(f'  Startbild: {len(roh)//1024} KB base64', file=sys.stderr)

    url = (f'https://generativelanguage.googleapis.com/v1beta/models/'
           f'{MODELL}:predictLongRunning?key={key}')
    koerper = json.dumps({
        'instances': [{'prompt': PROMPT,
                       'image': {'bytesBase64Encoded': roh, 'mimeType': 'image/png'}}],
        # generateAudio kennt das Lite-Modell nicht (HTTP 400). Ton, den Veo
        # von sich aus erzeugt, wird beim Schnitt ohnehin verworfen - unsere
        # Erzaehlstimme und der echte Ruf liegen darueber.
        'parameters': {'aspectRatio': '16:9', 'durationSeconds': SEK},
    }).encode()
    req = urllib.request.Request(url, data=koerper,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            op = json.load(r)
    except urllib.error.HTTPError as e:
        leib = e.read().decode('utf-8', 'replace')
        print(f'ABGELEHNT  HTTP {e.code}', file=sys.stderr)
        print(leib[:900], file=sys.stderr)
        return 2

    name = op.get('name')
    print(f'  Auftrag laeuft: {name}', file=sys.stderr)
    stand = f'https://generativelanguage.googleapis.com/v1beta/{name}?key={key}'
    for versuch in range(60):                    # bis zu 10 Minuten
        time.sleep(10)
        with urllib.request.urlopen(stand, timeout=60) as r:
            o = json.load(r)
        if o.get('done'):
            if 'error' in o:
                print('FEHLER:', json.dumps(o['error'])[:500], file=sys.stderr); return 3
            print(f'  fertig nach {(versuch+1)*10}s', file=sys.stderr)
            print(json.dumps(o.get('response', {}))[:600])
            return 0
        print(f'    …{(versuch+1)*10}s', file=sys.stderr)
    print('Zeitueberschreitung', file=sys.stderr)
    return 4

if __name__ == '__main__':
    sys.exit(main())
