#!/usr/bin/env python3
"""Prueft die cloud-init-Erstkonfiguration, bevor die Karte in den Pi wandert.

Ein Fehler hier bedeutet: Der Pi startet ohne WLAN und ohne SSH-Zugang, und die
Karte muss neu geflasht werden. Deshalb pruefen wir jedes Pflichtfeld einzeln,
statt uns auf gueltiges YAML zu verlassen.

Aufruf: check-cloudinit.py <user-data> <network-config>
"""
import sys

try:
    import yaml
except ImportError:
    sys.exit("FEHLER: PyYAML fehlt (pip install pyyaml) - Konfiguration ungeprueft.")


def fail(msg):
    sys.exit("FEHLER in der Erstkonfiguration: " + msg)


def check_user_data(path):
    with open(path) as fh:
        first_line = fh.readline()
        fh.seek(0)
        data = yaml.safe_load(fh)

    if not first_line.startswith("#cloud-config"):
        fail("user-data braucht '#cloud-config' als allererste Zeile")
    if not isinstance(data, dict):
        fail("user-data ist kein YAML-Objekt")
    if not data.get("hostname"):
        fail("hostname fehlt")

    users = data.get("users")
    if not isinstance(users, list) or len(users) != 1:
        fail("genau ein Benutzer erwartet ('- default' darf nicht auftauchen, "
             "sonst wird der eigene Benutzer ein Zweitkonto)")
    user = users[0]

    name = user.get("name")
    if not isinstance(name, str) or not name:
        fail("Benutzername fehlt")
    if not name[0].isalpha() or not name.islower():
        fail("Benutzername muss klein geschrieben sein und mit einem Buchstaben beginnen")

    pw = user.get("hashed_passwd")
    if not isinstance(pw, str) or not pw.startswith("$6$"):
        fail("hashed_passwd ist kein sha512-Hash ($6$...) - LibreSSL-Falle?")
    if user.get("lock_passwd") is not False:
        fail("lock_passwd muss ausdruecklich false sein, sonst ist das Konto gesperrt")

    keys = user.get("ssh_authorized_keys")
    if not isinstance(keys, list) or not keys or not str(keys[0]).startswith("ssh-"):
        fail("ssh_authorized_keys fehlt oder enthaelt keinen gueltigen Schluessel")

    if data.get("enable_ssh") is not True:
        fail("enable_ssh muss true sein - der SSH-Dienst ist im Image nicht aktiviert")

    return data["hostname"], name


def check_network_config(path):
    with open(path) as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        fail("network-config ist kein YAML-Objekt")

    # cloud-init akzeptiert die netplan-Struktur mit und ohne 'network'-Wurzel.
    root = data.get("network", data)
    if root.get("version") != 2:
        fail("network-config braucht 'version: 2'")

    try:
        ap = root["wifis"]["wlan0"]["access-points"]
    except (KeyError, TypeError):
        fail("wifis.wlan0.access-points fehlt")
    if not ap:
        fail("kein WLAN eingetragen")

    ssid = list(ap)[0]
    if not isinstance(ssid, str) or not ssid:
        fail("SSID fehlt oder wurde nicht als Text gelesen")

    password = (ap[ssid] or {}).get("password")
    if not isinstance(password, str) or not password:
        fail("WLAN-Passwort fehlt oder wurde als Zahl gelesen "
             "(rein numerische Passwoerter muessen in Anfuehrungszeichen stehen)")

    return ssid


def main():
    if len(sys.argv) != 3:
        sys.exit("Aufruf: check-cloudinit.py <user-data> <network-config>")
    hostname, user = check_user_data(sys.argv[1])
    ssid = check_network_config(sys.argv[2])
    print("  Geprueft: Rechnername %s, Benutzer %s, SSH aktiv, WLAN %s"
          % (hostname, user, ssid))


if __name__ == "__main__":
    main()
