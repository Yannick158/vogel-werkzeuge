# Server-Konfiguration (manuell deployt)

Diese Dateien liegen 1:1 auf dem Hetzner-Server und werden **von Hand**
gepflegt (nicht von deploy-server.sh, das nur `server/` nach `/opt/vogel-app`
synchronisiert). Hier abgelegt zur Versionierung und Reproduzierbarkeit.

## fail2ban
- `filter.d/vogel-login.conf` → `/etc/fail2ban/filter.d/vogel-login.conf`
  Erkennt Brute-Force auf der neuen Anmeldeseite: `POST /login` + `status 401`.
  login.php liefert bei falschem Passwort bewusst 401 (Erfolg=302, Aufruf=200).
- `filter.d/caddy-auth.conf` → `/etc/fail2ban/filter.d/caddy-auth.conf`
  Deckt die 2. Ebene (Basic-Auth-Menue) ab: `Authorization: REDACTED` + `401`.
  `datepattern = "ts":{EPOCH}` ergaenzt (sonst „no valid date/time"-Warnungen).
- `jail.d/vogel-login.conf` → `/etc/fail2ban/jail.d/vogel-login.conf`
  maxretry 6 / findtime 600 / bantime 7200.
- `jail.d/caddy.conf` → `/etc/fail2ban/jail.d/caddy.conf` (unveraendert, Referenz).

Die [DEFAULT] ignoreip (127.0.0.1/8 ::1 + Admin-IP) steht in /etc/fail2ban/jail.local
(von harden-server.sh gesetzt) und gilt fuer alle Jails.

## systemd
- `vogel-waechter.service` → `/etc/systemd/system/vogel-waechter.service` (Referenz)
- `vogel-waechter.service.d/haerten.conf` → gleichnamiges Drop-in
  Defense-in-Depth: Capabilities geleert, SystemCallFilter=@system-service,
  RestrictAddressFamilies, MemoryDenyWriteExecute, PrivateDevices, IPAddress*=localhost.

## Anwenden auf einem frischen Server
    sudo cp fail2ban/filter.d/*.conf /etc/fail2ban/filter.d/
    sudo cp fail2ban/jail.d/*.conf   /etc/fail2ban/jail.d/
    sudo mkdir -p /etc/systemd/system/vogel-waechter.service.d
    sudo cp systemd/vogel-waechter.service.d/haerten.conf /etc/systemd/system/vogel-waechter.service.d/
    sudo systemctl daemon-reload && sudo systemctl restart vogel-waechter
    sudo fail2ban-client reload
