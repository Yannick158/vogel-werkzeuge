#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Eine Veo-Einstellung als Hoehepunkt des Monatsvideos.

Startbild ist die EIGENE Zeichnung der Art, damit Stil und Art stimmen.
Der erste Versuch litt daran, dass Veo den Hintergrund mitwandern liess -
"no camera shake" als Verneinung reichte nicht. Jetzt steht die Kamera
ausdruecklich fest auf einem Stativ; positive Anweisungen wirken bei
Bildmodellen zuverlaessiger als Verbote.
"""
import base64, json, shutil, subprocess, sys, time, urllib.request, urllib.error

BASIS  = '/srv/avian/wochenschau'
ILL    = '/srv/avian/BirdNET-Pi/avian/assets/illustrations'
GRAIN  = '/srv/avian/BirdNET-Pi/avian/frontend/grain.png'
MODELL = 'veo-3.1-lite-generate-preview'


def startbild(slug, ziel):
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'color=c=0xFAF9F6:s=1280x720',
        '-i', GRAIN, '-i', f'{ILL}/{slug}.png',
        '-filter_complex',
        '[1:v]scale=1280:720,format=rgba,colorchannelmixer=aa=0.14[k];'
        '[0:v][k]overlay[g];[2:v]scale=-1:430,format=rgba[v];'
        '[g][v]overlay=x=(W-w)/2:y=(H-h)/2-10',
        '-frames:v', '1', ziel], check=True)
    return ziel


def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else 'actitis-hypoleucos'
    was  = sys.argv[2] if len(sys.argv) > 2 else (
        'the sandpiper bobs its rear body up and down and takes two small steps')
    sek  = int(sys.argv[3]) if len(sys.argv) > 3 else 8

    prompt = (
        'Static locked-off camera on a tripod. The camera stays completely still: '
        'no pan, no tilt, no zoom, no dolly, no handheld motion. '
        'The textured off-white paper background is a flat painted surface and '
        'stays perfectly fixed in frame, its grain unchanged. '
        'A hand-painted Japanese woodblock-style bird illustration sits in the '
        'centre of the paper. Only the bird itself is animated: ' + was + '. '
        'The movement is small, calm and unhurried. '
        'Painterly illustration style, muted natural colours, soft even daylight. '
        'No text, no people, no watermark, no added objects.')

    key = open(f'{BASIS}/.gemini-key').read().strip()
    roh = base64.b64encode(open(startbild(slug, '/tmp/held-start.png'), 'rb').read()).decode()
    url = (f'https://generativelanguage.googleapis.com/v1beta/models/{MODELL}'
           f':predictLongRunning?key={key}')
    koerper = json.dumps({
        'instances': [{'prompt': prompt,
                       'image': {'bytesBase64Encoded': roh, 'mimeType': 'image/png'}}],
        'parameters': {'aspectRatio': '16:9', 'durationSeconds': sek},
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url, data=koerper, headers={'Content-Type': 'application/json'}),
                timeout=180) as r:
            op = json.load(r)
    except urllib.error.HTTPError as e:
        print(f'ABGELEHNT HTTP {e.code}: {e.read().decode("utf-8","replace")[:400]}',
              file=sys.stderr)
        return 2

    stand = f"https://generativelanguage.googleapis.com/v1beta/{op['name']}?key={key}"
    print(f'  Auftrag laeuft ({sek}s, etwa {sek*0.05:.2f} $)', file=sys.stderr)
    for i in range(60):
        time.sleep(10)
        with urllib.request.urlopen(stand, timeout=60) as r:
            o = json.load(r)
        if o.get('done'):
            if 'error' in o:
                print('FEHLER:', json.dumps(o['error'])[:400], file=sys.stderr); return 3
            uri = (o['response']['generateVideoResponse']['generatedSamples'][0]
                   ['video']['uri'])
            ziel = f'{BASIS}/veo-held.mp4'
            # Der Schluessel darf NICHT in eine Befehlszeile: sudo protokolliert
            # jede davon ins Journal, und dort stand er dann im Klartext.
            # urllib laedt genauso, aber ohne diesen Umweg.
            # (kein Schluessel in der Befehlszeile)
            with urllib.request.urlopen(
                    urllib.request.Request(f'{uri}&key={key}',
                                           headers={'User-Agent': 'AvianVisitors/1.0'}),
                    timeout=300) as q, open(ziel, 'wb') as f:
                shutil.copyfileobj(q, f)
            # Merken, fuer WELCHE Art dieser Clip gilt - sonst koennte ein
            # spaeterer Lauf ihn faelschlich fuer einen anderen Vogel nutzen.
            with open(f'{BASIS}/veo-held.json', 'w', encoding='utf-8') as f:
                json.dump({'slug': slug, 'sekunden': sek}, f)
            print(f'  fertig nach {(i+1)*10}s -> {ziel}', file=sys.stderr)
            print(ziel)
            return 0
        print(f'    …{(i+1)*10}s', file=sys.stderr)
    return 4


if __name__ == '__main__':
    sys.exit(main())
