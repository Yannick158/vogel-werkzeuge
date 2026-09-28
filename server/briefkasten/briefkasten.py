#!/usr/bin/env python3
"""vogel-briefkasten - die Ablage zwischen Pi und Webseite.

WOFUER DAS DA IST:
  Bis zum 11.09.2026 holte sich die Webseite die Live-Daten, indem der Server
  durch den Rueckwaerts-Tunnel in den Pi hineingriff. Damit konnte ein Aufruf
  im Browser etwas auf dem Pi ausloesen - und genau das soll nicht sein: Der
  Pi im Wohnzimmer nimmt nichts entgegen, er liefert nur.
  Jetzt legt die Webseite hier einen Wunsch ab ("jemand will zuschauen"), der
  Pi sieht von sich aus nach und legt seine Ergebnisse hier ab. Beide Seiten
  reden nur mit dieser Ablage, nie miteinander.

WER HIER HINEINKOMMT:
  Nur 127.0.0.1 des Servers. Die Webseite erreicht das ueber PHP auf demselben
  Rechner, der Pi ueber eine Weiterleitung in SEINER bestehenden Verbindung -
  er baut sie auf, niemand baut eine zu ihm auf.

WAS HIER LIEGT:
  Spektrogramm-Streifen, Fundangaben und die Tonausschnitte erkannter Voegel.
  Alles im Arbeitsspeicher (tmpfs) und nach wenigen Minuten geloescht. Die
  Ausschnitte kommen ohnehin taeglich per Abgleich auf diesen Server - hier
  sind sie nur frueher da.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("vogel-briefkasten")

# Wie lange nach dem letzten Seitenaufruf gilt "jemand will zuschauen"?
# Die Seite fragt alle fuenf Sekunden nach; dreissig Sekunden ueberbruecken ein
# kurzes Stocken, ohne dass der Pi lange ins Leere arbeitet.
WUNSCH_GILT = 30.0

# So alt darf hier etwas werden, bevor es weggeraeumt wird.
ALTER_STREIFEN = 300.0
ALTER_AUFNAHME = 900.0

# Bewusst eng: Blocknamen sind Zeitstempel, Aufnahmen kommen aus BirdNETs
# Datenbank. Was nicht passt, wird gar nicht erst zu einem Pfad.
BLOCK_MUSTER = re.compile(r"^\d{8}-\d{6}$")
DATEI_MUSTER = re.compile(r"^[A-Za-z0-9_.:À-ɏ-]{1,120}$")

# Mehr nimmt der Briefkasten nicht an - ein Streifen ist rund 40 KB, ein
# Ausschnitt rund 100 KB.
MAX_KOERPER = 2 * 1024 * 1024


class Ablage:
    """Der Inhalt des Briefkastens. Alles im Arbeitsspeicher bzw. tmpfs."""

    def __init__(self, ordner: str) -> None:
        self.ordner = ordner
        os.makedirs(ordner, exist_ok=True)
        os.makedirs(os.path.join(ordner, "streifen"), exist_ok=True)
        os.makedirs(os.path.join(ordner, "aufnahmen"), exist_ok=True)
        self.wunsch_bis = 0.0
        self.stand: dict = {}
        self.stand_zeit = 0.0
        self._sperre = threading.Lock()

    # --- Wunsch ------------------------------------------------------------
    def wuenschen(self) -> None:
        with self._sperre:
            self.wunsch_bis = time.time() + WUNSCH_GILT

    def wunsch(self) -> dict:
        with self._sperre:
            rest = max(0.0, self.wunsch_bis - time.time())
        return {"zuschauer": rest > 0, "noch": round(rest, 1)}

    # --- Stand -------------------------------------------------------------
    def stand_setzen(self, daten: dict) -> None:
        with self._sperre:
            self.stand = daten
            self.stand_zeit = time.time()

    def stand_holen(self) -> dict:
        with self._sperre:
            # Ist der Stand alt, ist der Pi still geworden. Dann lieber nichts
            # zeigen als etwas Falsches: die Seite sagt dann "waermt sich auf".
            if not self.stand or time.time() - self.stand_zeit > 60:
                return {}
            return dict(self.stand)

    # --- Dateien -----------------------------------------------------------
    def _pfad(self, art: str, name: str) -> str | None:
        muster = BLOCK_MUSTER if art == "streifen" else DATEI_MUSTER
        if not muster.match(name):
            return None
        wurzel = os.path.realpath(os.path.join(self.ordner, art))
        ziel = os.path.realpath(os.path.join(wurzel, name))
        return ziel if ziel.startswith(wurzel + os.sep) else None

    def ablegen(self, art: str, name: str, inhalt: bytes) -> bool:
        p = self._pfad(art, name)
        if p is None:
            return False
        try:
            with open(p + ".neu", "wb") as fh:
                fh.write(inhalt)
            os.replace(p + ".neu", p)
        except OSError as fehler:
            LOG.warning("konnte %s/%s nicht ablegen: %s", art, name, fehler)
            return False
        return True

    def lesen(self, art: str, name: str) -> bytes | None:
        p = self._pfad(art, name)
        if p is None or not os.path.isfile(p):
            return None
        try:
            with open(p, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def aufraeumen(self) -> None:
        jetzt = time.time()
        for art, grenze in (("streifen", ALTER_STREIFEN), ("aufnahmen", ALTER_AUFNAHME)):
            ordner = os.path.join(self.ordner, art)
            try:
                namen = os.listdir(ordner)
            except OSError:
                continue
            for n in namen:
                p = os.path.join(ordner, n)
                try:
                    if jetzt - os.path.getmtime(p) > grenze:
                        os.remove(p)
                except OSError:
                    pass


class Dienst(BaseHTTPRequestHandler):
    """Zwei Seiten, streng getrennt.

    Der Pi (kommt ueber seine eigene Weiterleitung):
        GET  /wunsch                will gerade jemand zuschauen?
        PUT  /stand                 der aktuelle Stand als JSON
        PUT  /streifen/<block>      ein Spektrogramm-Streifen
        PUT  /aufnahme/<datei>      der Tonausschnitt einer Erkennung

    Die Webseite (kommt ueber PHP auf demselben Rechner):
        POST /wunsch                "jemand will zuschauen" melden
        GET  /stand                 den Stand lesen
        GET  /streifen/<block>      einen Streifen lesen
        GET  /aufnahme/<datei>      einen Ausschnitt lesen
    """

    server_version = "vogel-briefkasten"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    ablage: Ablage

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        LOG.debug("%s - %s", self.address_string(), format % args)

    def _senden(self, code: int, typ: str, koerper: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(koerper)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(koerper)

    def _json(self, code: int, daten: dict) -> None:
        self._senden(code, "application/json; charset=utf-8",
                     json.dumps(daten, ensure_ascii=False).encode("utf-8"))

    def _teile(self) -> tuple[str, str]:
        pfad = urllib.parse.urlparse(self.path).path
        stueck = [urllib.parse.unquote(x) for x in pfad.strip("/").split("/", 1)]
        return (stueck[0] if stueck else ""), (stueck[1] if len(stueck) > 1 else "")

    def _koerper(self) -> bytes | None:
        try:
            laenge = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if laenge <= 0 or laenge > MAX_KOERPER:
            return None
        return self.rfile.read(laenge)

    # --- vom Pi -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        was, name = self._teile()
        if was == "wunsch":
            self._json(200, self.ablage.wunsch())
        elif was == "stand":
            self._json(200, self.ablage.stand_holen())
        elif was in ("streifen", "aufnahme"):
            art = "streifen" if was == "streifen" else "aufnahmen"
            inhalt = self.ablage.lesen(art, name)
            if inhalt is None:
                self._json(404, {"fehler": "nicht da"})
                return
            typ = "image/webp" if was == "streifen" else (
                "audio/mpeg" if name.lower().endswith(".mp3") else "audio/wav")
            self._senden(200, typ, inhalt)
        else:
            self._json(404, {"fehler": "unbekannt"})

    do_HEAD = do_GET

    def do_PUT(self) -> None:  # noqa: N802
        was, name = self._teile()
        koerper = self._koerper()
        if koerper is None:
            self._json(400, {"fehler": "kein Inhalt"})
            return
        if was == "stand":
            try:
                daten = json.loads(koerper.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"fehler": "kein JSON"})
                return
            if not isinstance(daten, dict):
                self._json(400, {"fehler": "kein Objekt"})
                return
            self.ablage.stand_setzen(daten)
            self._json(200, {"gut": True})
        elif was in ("streifen", "aufnahme"):
            art = "streifen" if was == "streifen" else "aufnahmen"
            if not self.ablage.ablegen(art, name, koerper):
                self._json(400, {"fehler": "kein Name"})
                return
            self._json(200, {"gut": True})
        else:
            self._json(404, {"fehler": "unbekannt"})

    # --- von der Webseite ---------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        was, _ = self._teile()
        if was != "wunsch":
            self._json(404, {"fehler": "unbekannt"})
            return
        self.ablage.wuenschen()
        self._json(200, self.ablage.wunsch())


def aufraeum_schleife(ablage: Ablage, halt: threading.Event) -> None:
    while not halt.wait(30):
        ablage.aufraeumen()


def main(argv: list[str] | None = None) -> int:
    z = argparse.ArgumentParser(description="Ablage zwischen Pi und Webseite")
    z.add_argument("--ordner", default=os.environ.get("VOGEL_BRIEFKASTEN", "/run/vogel-briefkasten"))
    z.add_argument("--port", type=int, default=int(os.environ.get("VOGEL_BRIEFKASTEN_PORT", "8092")))
    z.add_argument("--laut", action="store_true")
    args = z.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.laut else logging.INFO,
                        format="%(levelname)s %(message)s")

    Dienst.ablage = Ablage(args.ordner)
    halt = threading.Event()
    threading.Thread(target=aufraeum_schleife, args=(Dienst.ablage, halt), daemon=True).start()

    # Nur die Rueckschleife. Der Pi kommt ueber seine eigene Weiterleitung
    # hierher, die Webseite ueber PHP auf demselben Rechner.
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Dienst)
    LOG.info("Briefkasten auf 127.0.0.1:%d, Ablage %s", args.port, args.ordner)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        halt.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
