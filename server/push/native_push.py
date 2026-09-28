#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nativer Push-Versand an App-Geraete (ARCHITEKTUR.md 3.7).

Zwei Dienste, eine Schnittstelle:

  fcm  - Android, HTTP v1 (https://fcm.googleapis.com/v1/projects/<id>/messages:send).
         Zugriffstoken kommt aus dem Dienstkonto-JSON: ein selbst signiertes
         RS256-JWT wird bei https://oauth2.googleapis.com/token gegen ein
         Bearer-Token getauscht und ~50 Minuten zwischengespeichert.
         Nachricht als reine Daten-Nachricht, damit die App das Bild selbst
         nachlaedt und die Meldung baut.

  apns - iOS, HTTP/2 (httpx[http2]) an api.push.apple.com bzw.
         api.sandbox.push.apple.com. Autorisierung per ES256-JWT aus dem
         .p8-Schluessel.

Konfiguration: /srv/avian/push/native.json (Umgebungsvariable NATIVE_KONFIG
lenkt sie fuer Tests um). Fehlt die Datei oder ein Abschnitt, wird der
betroffene Dienst uebersprungen - eine Logzeile, kein Fehler (SICHERHEIT.md
P-4). In Logs steht nie ein vollstaendiges Token, nur die ersten 8 Zeichen
(P-1).
"""
import base64
import json
import os
import sys
import time

BASIS = os.path.dirname(os.path.abspath(__file__))
STANDARD_KONFIG = '/srv/avian/push/native.json'

FCM_URL = 'https://fcm.googleapis.com/v1/projects/%s/messages:send'
OAUTH_URL = 'https://oauth2.googleapis.com/token'
FCM_BEREICH = 'https://www.googleapis.com/auth/firebase.messaging'
APNS_HOST = 'api.push.apple.com'
APNS_HOST_SANDBOX = 'api.sandbox.push.apple.com'

TOKEN_PUFFER = os.path.join(BASIS, '.fcm-token.json')
TOKEN_DAUER = 50 * 60          # Google gibt 60 min; wir erneuern frueher
ZEITLIMIT = 15.0               # Sekunden je HTTP-Anfrage


def kurz(token):
    """Nur die ersten 8 Zeichen eines Tokens - fuer Logzeilen (P-1)."""
    t = str(token or '')
    return t[:8] + '…' if len(t) > 8 else t


def log(text):
    print(text, flush=True)


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

def lade_konfig():
    """Liest native.json. Fehlt sie oder ist sie kaputt: leeres Dict."""
    pfad = os.environ.get('NATIVE_KONFIG') or STANDARD_KONFIG
    try:
        with open(pfad, encoding='utf-8') as f:
            k = json.load(f)
        return k if isinstance(k, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log('native.json nicht lesbar (%s) - nativer Push uebersprungen' % e)
        return {}


# ---------------------------------------------------------------------------
# JWT-Bausteine
# ---------------------------------------------------------------------------

def _b64(roh):
    return base64.urlsafe_b64encode(roh).rstrip(b'=')


def _jwt(kopf, nutz, signieren):
    teil = _b64(json.dumps(kopf, separators=(',', ':')).encode()) + b'.' \
         + _b64(json.dumps(nutz, separators=(',', ':')).encode())
    return (teil + b'.' + _b64(signieren(teil))).decode('ascii')


def _rs256(privat_pem, daten):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    schl = serialization.load_pem_private_key(privat_pem.encode(), password=None)
    return schl.sign(daten, padding.PKCS1v15(), hashes.SHA256())


def _es256(privat_pem, daten):
    """ES256 fuer APNs: die DER-Signatur muss in das feste 64-Byte-Format."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    schl = serialization.load_pem_private_key(privat_pem.encode(), password=None)
    der = schl.sign(daten, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    return r.to_bytes(32, 'big') + s.to_bytes(32, 'big')


# ---------------------------------------------------------------------------
# FCM
# ---------------------------------------------------------------------------

def _fcm_token_aus_puffer():
    try:
        with open(TOKEN_PUFFER, encoding='utf-8') as f:
            d = json.load(f)
        if d.get('bis', 0) > time.time() and d.get('token'):
            return str(d['token'])
    except Exception:
        pass
    return None


def _fcm_token_puffern(token, bis):
    try:
        tmp = TOKEN_PUFFER + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'token': token, 'bis': bis}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, TOKEN_PUFFER)
    except Exception as e:
        log('FCM-Token konnte nicht gepuffert werden: %s' % e)


def fcm_zugriffstoken(konf):
    """OAuth2-Zugriffstoken fuer FCM; None bei Problemen."""
    puffer = _fcm_token_aus_puffer()
    if puffer:
        return puffer
    pfad = konf.get('dienstkonto') or ''
    try:
        with open(pfad, encoding='utf-8') as f:
            dk = json.load(f)
        email = dk['client_email']
        privat = dk['private_key']
    except Exception as e:
        log('FCM-Dienstkonto nicht lesbar (%s) - uebersprungen' % e)
        return None

    jetzt = int(time.time())
    jwt = _jwt({'alg': 'RS256', 'typ': 'JWT'},
               {'iss': email, 'scope': FCM_BEREICH, 'aud': OAUTH_URL,
                'iat': jetzt, 'exp': jetzt + 3600},
               lambda d: _rs256(privat, d))
    try:
        import httpx
        with httpx.Client(timeout=ZEITLIMIT) as c:
            a = c.post(OAUTH_URL, data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': jwt})
        if a.status_code != 200:
            log('FCM-OAuth fehlgeschlagen: HTTP %s' % a.status_code)
            return None
        token = a.json().get('access_token')
    except Exception as e:
        log('FCM-OAuth-Ausnahme: %s' % e)
        return None
    if not token:
        return None
    _fcm_token_puffern(token, time.time() + TOKEN_DAUER)
    return token


def _fcm_senden(konf, geraet_token, daten):
    projekt = konf.get('projekt_id') or ''
    if not projekt:
        log('FCM ohne projekt_id - uebersprungen')
        return 'uebersprungen'
    zugriff = fcm_zugriffstoken(konf)
    if not zugriff:
        return 'fehler'
    nachricht = {'message': {
        'token': geraet_token,
        'data': {k: str(v) for k, v in daten.items() if v is not None},
        'android': {'priority': 'high'},
    }}
    try:
        import httpx
        with httpx.Client(timeout=ZEITLIMIT) as c:
            a = c.post(FCM_URL % projekt, json=nachricht,
                       headers={'Authorization': 'Bearer ' + zugriff,
                                'Content-Type': 'application/json'})
    except Exception as e:
        log('FCM-Ausnahme (%s): %s' % (kurz(geraet_token), e))
        return 'fehler'
    if a.status_code == 200:
        return 'ok'
    text = a.text or ''
    if a.status_code == 404 or 'UNREGISTERED' in text:
        log('FCM: Token %s ist tot' % kurz(geraet_token))
        return 'tot'
    if a.status_code in (400, 403) and 'INVALID_ARGUMENT' in text and 'token' in text.lower():
        log('FCM: Token %s ungueltig' % kurz(geraet_token))
        return 'tot'
    log('FCM-Fehler %s fuer %s' % (a.status_code, kurz(geraet_token)))
    return 'fehler'


# ---------------------------------------------------------------------------
# APNs
# ---------------------------------------------------------------------------

_apns_jwt = {'wert': None, 'bis': 0}


def apns_jwt(konf):
    """ES256-JWT fuer APNs; Apple erlaubt Wiederverwendung bis 1 h."""
    if _apns_jwt['wert'] and _apns_jwt['bis'] > time.time():
        return _apns_jwt['wert']
    try:
        with open(konf.get('schluessel') or '', encoding='utf-8') as f:
            p8 = f.read()
    except Exception as e:
        log('APNs-Schluessel nicht lesbar (%s) - uebersprungen' % e)
        return None
    team = konf.get('team_id') or ''
    kid = konf.get('key_id') or ''
    if not team or not kid:
        log('APNs ohne team_id/key_id - uebersprungen')
        return None
    jetzt = int(time.time())
    try:
        t = _jwt({'alg': 'ES256', 'kid': kid},
                 {'iss': team, 'iat': jetzt},
                 lambda d: _es256(p8, d))
    except Exception as e:
        log('APNs-JWT fehlgeschlagen: %s' % e)
        return None
    _apns_jwt['wert'] = t
    _apns_jwt['bis'] = jetzt + 45 * 60
    return t


def _apns_senden(konf, geraet_token, titel, text, daten):
    bundle = konf.get('bundle_id') or ''
    if not bundle:
        log('APNs ohne bundle_id - uebersprungen')
        return 'uebersprungen'
    jwt = apns_jwt(konf)
    if not jwt:
        return 'fehler'
    host = APNS_HOST_SANDBOX if konf.get('sandbox') else APNS_HOST
    koerper = {'aps': {'alert': {'title': titel, 'body': text},
                       'mutable-content': 1,
                       'thread-id': str(daten.get('tag') or 'vogel'),
                       'sound': 'default'}}
    for k, v in daten.items():
        if v is not None and k != 'aps':
            koerper[k] = str(v)
    try:
        import httpx
        with httpx.Client(http2=True, timeout=ZEITLIMIT) as c:
            a = c.post('https://%s/3/device/%s' % (host, geraet_token),
                       json=koerper,
                       headers={'authorization': 'bearer ' + jwt,
                                'apns-topic': bundle,
                                'apns-push-type': 'alert',
                                'apns-priority': '10',
                                'apns-expiration': str(int(time.time()) + 3600)})
    except Exception as e:
        log('APNs-Ausnahme (%s): %s' % (kurz(geraet_token), e))
        return 'fehler'
    if a.status_code == 200:
        return 'ok'
    antwort = a.text or ''
    if a.status_code == 410 or 'BadDeviceToken' in antwort or 'Unregistered' in antwort:
        log('APNs: Token %s ist tot' % kurz(geraet_token))
        return 'tot'
    log('APNs-Fehler %s fuer %s' % (a.status_code, kurz(geraet_token)))
    return 'fehler'


# ---------------------------------------------------------------------------
# Oeffentliche Schnittstelle
# ---------------------------------------------------------------------------

def sende(geraet, titel, text, daten, konfig=None):
    """Schickt eine Meldung an EIN Geraet.

    geraet: der Eintrag aus geraete-<standort>.json (braucht 'push').
    daten:  Nutzdaten nach ARCHITEKTUR.md 3.7 (alle Werte werden Strings).

    Rueckgabe: 'ok' | 'tot' | 'fehler' | 'uebersprungen'
      'tot' heisst: das Push-Token ist ungueltig; der Aufrufer setzt
      geraet['push'] auf None und behaelt den Eintrag (P-2).
    """
    push = (geraet or {}).get('push') or {}
    dienst = str(push.get('dienst') or '')
    token = str(push.get('token') or '')
    if not dienst or not token:
        return 'uebersprungen'

    konf = konfig if konfig is not None else lade_konfig()
    abschnitt = konf.get(dienst)
    if not isinstance(abschnitt, dict) or not abschnitt:
        log('%s nicht konfiguriert - uebersprungen (%s)' % (dienst, kurz(token)))
        return 'uebersprungen'

    voll = dict(daten or {})
    voll.setdefault('titel', titel)
    voll.setdefault('text', text)

    try:
        if dienst == 'fcm':
            return _fcm_senden(abschnitt, token, voll)
        if dienst == 'apns':
            return _apns_senden(abschnitt, token, titel, text, voll)
    except Exception as e:
        # Nie mit Traceback abbrechen: ein kaputter Dienst darf den Lauf
        # der anderen nicht mitreissen.
        log('%s-Ausnahme (%s): %s' % (dienst, kurz(token), e))
        return 'fehler'
    log('Unbekannter Push-Dienst: %s' % dienst)
    return 'uebersprungen'


if __name__ == '__main__':
    # Kleiner Selbsttest: zeigt, was konfiguriert ist - ohne etwas zu senden.
    k = lade_konfig()
    if not k:
        print('native.json fehlt oder ist leer - nativer Push ist aus.')
        sys.exit(0)
    for d in ('fcm', 'apns'):
        a = k.get(d)
        print('%-5s %s' % (d, 'konfiguriert' if isinstance(a, dict) and a else 'fehlt'))
