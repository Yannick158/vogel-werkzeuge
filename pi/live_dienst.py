#!/usr/bin/env python3
"""vogel-live - Spektrogramm-Streifen und Fundmeldungen fuer den Live-Modus.

Nur Standardbibliothek. Auf dem Pi wird nichts nachinstalliert; gezeichnet
wird mit ffmpeg, das fuer den Livestream ohnehin vorhanden ist.

WARUM EIN EIGENER MITSCHNITT statt der BirdNET-Dateien:
  BirdNET loescht seine 15-Sekunden-Dateien rund sechs Sekunden nach der
  Analyse wieder. Wer sich darauf stuetzt, verpasst bei jeder Verzoegerung
  einen Streifen und bekommt Loecher im Band. Der eigene Mitschnitt haengt
  ueber ALSA-dsnoop am selben Mikrofon (siehe /etc/asound.conf), stoert die
  Erkennung nicht und ist von deren Dateiverwaltung unabhaengig.

WARUM DIE ANZEIGE ABSICHTLICH NACHLAEUFT:
  BirdNET arbeitet in 15-Sekunden-Bloecken und rechnet danach rund sechs
  Sekunden. Ein Fund steht also erst 6 bis 21 Sekunden nach dem Gesang fest.
  Zeigt man das Band echt live, trifft die Markierung nie die Welle, die sie
  ausgeloest hat. Deshalb laeuft die Anzeige im Browser rund 22 Sekunden
  hinterher - dann sitzt jede Markierung genau richtig, und weil niemand
  einen Vergleich zu "jetzt" hat, wirkt es trotzdem live.

WAS DAS GERAET VERLAESST:
  Bilder (Spektrogramm-Streifen) und Fund-Angaben. Ton nur dann, wenn jemand
  eine Markierung anklickt - und das ist genau die Ausschnittsdatei, die
  ohnehin zum Server abgeglichen wird, also keine neue Tonquelle. Der
  Mitschnitt selbst bleibt im Arbeitsspeicher (tmpfs) und ist nach gut zwei
  Minuten ueberschrieben.

WIE DIE DATEN HERAUSKOMMEN (seit 11.09.2026):
  Gar nicht auf Zuruf. Der Pi sieht von sich aus im Briefkasten auf dem Server
  nach, ob jemand zuschauen will, und schiebt dann hin. Niemand baut eine
  Verbindung zu diesem Geraet auf. Der eigene Zugang auf 127.0.0.1:8091 bleibt
  nur fuer die Fehlersuche vor Ort - von aussen ist er nicht erreichbar.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("vogel-live")

# Ein Block ist so lang wie BirdNETs Analysefenster. Gleiche Laenge heisst:
# ein Fund faellt nie ueber eine Streifengrenze hinweg auseinander.
BLOCK_SEKUNDEN = 15.0

# 40 Bildpunkte je Sekunde. Auf einem Handy (rund 400 Punkte breit) sieht man
# damit zehn Sekunden auf einmal - genug, dass ein Name neben seiner Welle
# lesbar bleibt, ohne dass das Band hetzt.
PUNKTE_JE_SEKUNDE = 40
STREIFEN_HOEHE = 150

# Gezeichnet wird feiner, als angezeigt wird, damit ein Handy mit scharfem
# Bildschirm nicht hochskaliert und verwaescht.
#
# WARUM 1.5 UND NICHT 2: Am echten Gartenton gemessen (10.09.2026). Ein Streifen
# ist voller Rauschstruktur - der Garten ist nun einmal nie still -, und die
# kostet Platz. Gemessen fuer denselben Block:
#     600x150   WebP  18 KB  ->  4 MB/h Zuschauen
#     900x225   WebP  42 KB  -> 10 MB/h
#    1200x300   WebP  71 KB  -> 16 MB/h
#    1200x300   PNG  321 KB  -> 79 MB/h   (so lief es zuerst - viel zu viel)
# 1.5 trifft die Mitte: sichtbar schaerfer als 1:1, und zehn Minuten Zuschauen
# kosten 1,6 MB statt 20. Das laeuft ueber die Hausleitung der Freunde, die
# nebenbei die Erkennungen hochlaedt.
SCHAERFE = 1.5

# WARUM WEBP UND NICHT PNG: PNG ist verlustfrei und bei einem sparsamen Bild
# unschlagbar (2 KB) - aber ein Spektrogramm mit echtem Gartenrauschen ist das
# Gegenteil davon, und dann blaeht PNG auf das Siebenfache. Der Verlust faellt
# bei einer Rauschtextur niemandem auf.
WEBP_GUETE = '70' 

# So viel Vergangenheit halten wir vor. Deckt die 22 Sekunden Versatz ab und
# laesst den Betrachter ein Stueck zurueckrollen, um noch einmal hinzuhoeren.
RING_SEKUNDEN = 150

# Wie nah beieinander duerfen zwei Funde derselben Art liegen, damit sie als
# EIN Ereignis gelten?
#
# BirdNET analysiert mit OVERLAP=2.5, das 3-Sekunden-Fenster rueckt also nur
# eine halbe Sekunde weiter. Ein einziger Ruf wird dadurch bis zu sechsmal
# bewertet, und jedes Mal ueber der Schwelle entsteht ein eigener Eintrag mit
# eigenem Tonausschnitt. Gemessen am 10.09.2026 beim Turmfalken: drei
# Erkennungen innerhalb einer Sekunde, drei Dateien - versetzt geschnitten aus
# demselben Ruf, und darum fuer das Ohr identisch.
#
# Fuenf Sekunden fassen eine solche Serie zusammen, trennen aber zwei
# Strophen, zwischen denen der Vogel wirklich Luft geholt hat.
ZUSAMMEN_SEKUNDEN = 5.0

# Der aelteste Name, den ein Block haben darf: JJJJMMTT-HHMMSS
BLOCK_MUSTER = re.compile(r"^\d{8}-\d{6}$")

# Ausschnittsdateien von BirdNET. Bewusst eng - der Name kommt zwar aus der
# eigenen Datenbank, wird aber trotzdem geprueft, bevor er auf das Dateisystem
# losgelassen wird.
DATEI_MUSTER = re.compile(r"^[A-Za-z0-9_.:À-ɏ-]{1,120}$")


class Schieber:
    """Fragt beim Briefkasten nach und liefert dorthin - der Pi nimmt nichts an.

    WARUM SO HERUM: Frueher holte sich der Server die Daten, indem er durch den
    Rueckwaerts-Tunnel in den Pi hineingriff. Ein Aufruf im Browser konnte damit
    etwas auf einem Geraet im fremden Wohnzimmer ausloesen. Jetzt fragt der Pi
    von sich aus ("will jemand zuschauen?") und schiebt seine Ergebnisse hin.
    Die Verbindung baut immer er auf; niemand baut eine zu ihm auf.

    Der Weg dorthin ist eine Weiterleitung in der ohnehin bestehenden
    Tunnelverbindung (siehe vogel-rueckweg.service). Der Schluessel erlaubt per
    permitopen genau diese eine Adresse, sonst nichts.
    """

    def __init__(self, briefkasten: str, ring: Ring, zeichner: "Zeichner",
                 funde: "Funde", ausschnitte: str) -> None:
        self.basis = briefkasten.rstrip("/")
        self.ring = ring
        self.zeichner = zeichner
        self.funde = funde
        self.ausschnitte = ausschnitte
        self.geschoben_streifen: set[str] = set()
        self.geschoben_ton: set[str] = set()

    # --- Verkehr ------------------------------------------------------------
    def _holen(self, pfad: str) -> dict | None:
        try:
            with urllib.request.urlopen(self.basis + pfad, timeout=8) as a:
                return json.loads(a.read().decode("utf-8"))
        except Exception:      # Leitung weg, Briefkasten aus - beides normal
            return None

    def _legen(self, pfad: str, inhalt: bytes, typ: str) -> bool:
        anfrage = urllib.request.Request(self.basis + pfad, data=inhalt, method="PUT")
        anfrage.add_header("Content-Type", typ)
        try:
            with urllib.request.urlopen(anfrage, timeout=20) as a:
                return a.status == 200
        except Exception:
            return False

    # --- Runde --------------------------------------------------------------
    def runde(self) -> bool:
        """Eine Runde. Gibt zurueck, ob gerade jemand zuschaut."""
        wunsch = self._holen("/wunsch")
        if not wunsch or not wunsch.get("zuschauer"):
            # Niemand da: nichts rendern, nichts schieben. Was schon drueben
            # liegt, raeumt der Briefkasten selbst weg.
            self.geschoben_streifen.clear()
            self.geschoben_ton.clear()
            return False

        bloecke = self.ring.bloecke()
        if not bloecke:
            return True

        # 1. Streifen, die drueben noch fehlen. Gerendert wird nur hier - der
        #    Briefkasten bekommt fertige Bilder, keine Tondaten.
        for name in bloecke:
            if name in self.geschoben_streifen:
                continue
            bild = self.zeichner.streifen(name)
            if bild is None:
                continue
            if self._legen("/streifen/" + urllib.parse.quote(name), bild,
                           self.zeichner.typ):
                self.geschoben_streifen.add(name)
        # Was aus dem Ring gelaufen ist, muss auch hier vergessen werden,
        # sonst waechst die Merkliste ohne Ende.
        self.geschoben_streifen &= set(bloecke)

        # 2. Der Stand
        von = block_beginn(bloecke[0])
        bis = block_beginn(bloecke[-1]) + BLOCK_SEKUNDEN
        funde = self.funde.im_fenster(von, bis)
        stand = {
            "jetzt": round(time.time(), 2),
            "blocklaenge": BLOCK_SEKUNDEN,
            "punkte_je_sekunde": PUNKTE_JE_SEKUNDE,
            "hoehe": STREIFEN_HOEHE,
            "schaerfe": SCHAERFE,
            "hoert_zu": True,
            "bloecke": [{"name": n, "beginn": round(block_beginn(n), 2)} for n in bloecke],
            "funde": funde,
        }
        self._legen("/stand", json.dumps(stand, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8")

        # 3. Die Tonausschnitte der gemeldeten Funde - damit der Abspielknopf
        #    sofort geht und nicht auf den Zehn-Minuten-Abgleich wartet.
        gebraucht = set()
        for f in funde:
            datei = f.get("datei") or ""
            if not datei or not f.get("ton"):
                continue
            gebraucht.add(datei)
            if datei in self.geschoben_ton:
                continue
            eintrag = self.funde.zu_datei(datei)
            if eintrag is None:
                continue
            pfad = self.funde.pfad_der_datei(eintrag["datum"], datei)
            if pfad is None:
                continue
            try:
                with open(pfad, "rb") as fh:
                    roh = fh.read()
            except OSError:
                continue
            typ = "audio/mpeg" if pfad.lower().endswith(".mp3") else "audio/wav"
            if self._legen("/aufnahme/" + urllib.parse.quote(datei), roh, typ):
                self.geschoben_ton.add(datei)
        self.geschoben_ton &= gebraucht
        return True


def schiebe_schleife(schieber: Schieber, takt: float, halt: threading.Event) -> None:
    """Sieht regelmaessig im Briefkasten nach. Das ist die einzige Stelle, an
    der ueberhaupt eine Verbindung nach draussen aufgebaut wird."""
    while not halt.wait(takt):
        try:
            schieber.runde()
        except Exception as fehler:              # noqa: BLE001
            LOG.warning("Schieberunde fehlgeschlagen: %s", fehler)


def block_beginn(name: str) -> float:
    """Startzeitpunkt eines Blocks aus seinem Dateinamen, als Unix-Zeit."""
    zeit = dt.datetime.strptime(name, "%Y%m%d-%H%M%S")
    return zeit.timestamp()


class Ring:
    """Der Mitschnitt-Ring im Arbeitsspeicher.

    Geschrieben wird er von ffmpeg (eigener Dienst), aufgeraeumt hier. Die
    juengste Datei ist immer die, in die gerade geschrieben wird - sie hat
    noch keinen gueltigen WAV-Kopf und wird deshalb nie ausgeliefert.
    """

    def __init__(self, ordner: str) -> None:
        self.ordner = ordner

    def bloecke(self) -> list[str]:
        try:
            namen = os.listdir(self.ordner)
        except OSError:
            return []
        fertig = sorted(
            n[:-4] for n in namen
            if n.endswith(".wav") and BLOCK_MUSTER.match(n[:-4])
        )
        # Die letzte Datei ist noch offen.
        return fertig[:-1]

    def pfad(self, name: str) -> str:
        return os.path.join(self.ordner, name + ".wav")

    def aufraeumen(self) -> None:
        grenze = time.time() - RING_SEKUNDEN
        try:
            namen = os.listdir(self.ordner)
        except OSError:
            return
        for n in namen:
            kern, _punkt, endung = n.rpartition(".")
            if endung not in ("wav", "png", "webp"):
                continue
            if not BLOCK_MUSTER.match(kern):
                continue
            try:
                if block_beginn(kern) + BLOCK_SEKUNDEN < grenze:
                    os.remove(os.path.join(self.ordner, n))
            except (ValueError, OSError):
                pass


class Funde:
    """Liest BirdNETs Datenbank - ausschliesslich lesend.

    BirdNET-Pi schreibt fortlaufend hinein. Eine Schreibsperre von hier waere
    das Letzte, was diesem Pi passieren darf, deshalb der ro-URI.
    """

    def __init__(self, db_pfad: str, ausschnitte: str = "", tabelle: str = "detections") -> None:
        self.db_pfad = db_pfad
        self.ausschnitte = ausschnitte
        self.tabelle = tabelle
        self._conn: sqlite3.Connection | None = None
        self._spalten: set[str] = set()

    def _verbinden(self) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        if not os.path.exists(self.db_pfad):
            return None
        uri = "file:{}?mode=ro".format(urllib.parse.quote(os.path.abspath(self.db_pfad)))
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 4000")
            zeilen = conn.execute(f"PRAGMA table_info({self.tabelle})").fetchall()
            self._spalten = {z["name"] for z in zeilen}
            if not self._spalten:
                conn.close()
                return None
        except sqlite3.Error as fehler:
            LOG.warning("Datenbank nicht lesbar: %s", fehler)
            return None
        self._conn = conn
        return conn

    def _hol(self, zeile: sqlite3.Row, *namen: str):
        for name in namen:
            if name in self._spalten:
                wert = zeile[name]
                if wert is not None:
                    return wert
        return None

    def im_fenster(self, von: float, bis: float) -> list[dict]:
        conn = self._verbinden()
        if conn is None:
            return []
        # Ueber das Datum vorfiltern, damit nicht die ganze Tabelle durchlaufen
        # wird; ueber Mitternacht deckt der Vortag den Rest ab.
        tage = {
            dt.datetime.fromtimestamp(von).strftime("%Y-%m-%d"),
            dt.datetime.fromtimestamp(bis).strftime("%Y-%m-%d"),
        }
        platz = ",".join("?" for _ in tage)
        sql = (
            f"SELECT * FROM {self.tabelle} "
            f"WHERE Date IN ({platz}) ORDER BY rowid DESC LIMIT 400"
        )
        try:
            zeilen = conn.execute(sql, tuple(sorted(tage))).fetchall()
        except sqlite3.Error as fehler:
            LOG.warning("Abfrage fehlgeschlagen: %s", fehler)
            return []

        raus: list[dict] = []
        for zeile in zeilen:
            datum = str(self._hol(zeile, "Date", "date") or "")
            uhr = str(self._hol(zeile, "Time", "time") or "")
            if not datum or not uhr:
                continue
            try:
                beginn = dt.datetime.strptime(
                    f"{datum} {uhr}", "%Y-%m-%d %H:%M:%S"
                ).timestamp()
            except ValueError:
                continue
            if not (von <= beginn <= bis):
                continue
            try:
                sicher = float(self._hol(zeile, "Confidence", "confidence") or 0.0)
            except (TypeError, ValueError):
                sicher = 0.0
            datei = str(self._hol(zeile, "File_Name", "file_name") or "")
            raus.append({
                "beginn": round(beginn, 2),
                "dauer": 3.0,  # BirdNETs Analysefenster
                "art": str(self._hol(zeile, "Com_Name", "com_name") or ""),
                "wiss": str(self._hol(zeile, "Sci_Name", "sci_name") or ""),
                "sicher": round(sicher, 4),
                "datei": datei,
                # Liegt der Ausschnitt schon? Gemessen am 10.09.2026: BirdNET
                # schreibt ihn 11 bis 22 Sekunden nach der Erkennung - also
                # knapp an unserem Nachlauf von 22 Sekunden. Meistens ist er da,
                # manchmal fehlt eine Sekunde. Der Browser soll das wissen,
                # damit der Knopf nie tot wirkt, sondern "kommt gleich" zeigt.
                "ton": bool(datei) and self._liegt_bereit(datum, datei),
            })
        raus.sort(key=lambda f: f["beginn"])
        return self._zusammenfassen(raus)

    @staticmethod
    def _zusammenfassen(funde: list[dict]) -> list[dict]:
        """Serien derselben Art zu einem Eintrag buendeln.

        Behalten wird der sicherste Fund der Serie - seine Tondatei ist die,
        die man hoeren will. Die Markierung spannt sich ueber die ganze Serie,
        und "anzahl" sagt, wie oft es innerhalb dieser Zeit angeschlagen hat.
        Nichts geht verloren, es steht nur nicht mehr dreimal untereinander.
        """
        gebuendelt: list[dict] = []
        for f in funde:
            vorher = None
            for g in gebuendelt:
                if g["wiss"] == f["wiss"] and f["beginn"] - g["ende"] <= ZUSAMMEN_SEKUNDEN:
                    vorher = g
            if vorher is None:
                neu = dict(f)
                neu["ende"] = f["beginn"] + f["dauer"]
                neu["anzahl"] = 1
                gebuendelt.append(neu)
                continue
            vorher["anzahl"] += 1
            vorher["ende"] = max(vorher["ende"], f["beginn"] + f["dauer"])
            vorher["dauer"] = round(vorher["ende"] - vorher["beginn"], 2)
            if f["sicher"] > vorher["sicher"]:
                vorher["sicher"] = f["sicher"]
                vorher["datei"] = f["datei"]
                vorher["ton"] = f["ton"]
        for g in gebuendelt:
            g.pop("ende", None)
        return gebuendelt

    def pfad_der_datei(self, datum: str, datei: str) -> str | None:
        """Wo liegt der Ausschnitt? Gibt den gepruefen Pfad zurueck, sonst None.

        BirdNET legt je Tag NACH ART in Unterordner ab:
            By_Date/2026-09-10/Mauersegler/Mauersegler-79-...mp3
        Aeltere Bestaende liegen flach im Tagesordner. Beides wird abgeklopft,
        aber nur eine Ebene tief - der ganze Baum hat zehntausende Dateien und
        das hier laeuft bei jedem Abruf.

        WARUM EINE GEMEINSAME FASSUNG: Am 10.09.2026 gab es zwei - eine fuers
        Melden ("liegt bereit?"), die den Unterordner kannte, und eine fuers
        Ausliefern, die ihn nicht kannte. Der Knopf meldete also "bereit" und
        lieferte dann 404: kein Ton, ohne sichtbaren Fehler.
        """
        if not self.ausschnitte or not datum or not datei:
            return None
        wurzel = os.path.realpath(self.ausschnitte)
        tag = os.path.join(wurzel, datum)
        kandidaten = [os.path.join(tag, datei)]
        try:
            kandidaten += [os.path.join(tag, u, datei) for u in os.listdir(tag)]
        except OSError:
            pass
        for k in kandidaten:
            echt = os.path.realpath(k)
            # Der Name kommt zwar aus der eigenen Datenbank, der Pfad wird
            # trotzdem gegen den Ausschnittsordner geprueft.
            if echt.startswith(wurzel + os.sep) and os.path.isfile(echt):
                return echt
        return None

    def _liegt_bereit(self, datum: str, datei: str) -> bool:
        return self.pfad_der_datei(datum, datei) is not None

    def zu_datei(self, name: str) -> dict | None:
        """Erkennung zu einer Ausschnittsdatei - oder None.

        Dreifacher Zweck: Es beweist, dass die Datei aus der eigenen Erkennung
        stammt (und nicht irgendein Name von aussen ist), es sagt, in welchem
        Tagesordner sie liegt - damit niemand den ganzen Ausschnittsbaum
        durchsuchen muss -, und es nennt die Art. Die braucht der Server, um
        die Ausblendliste anzuwenden: was dort steht, darf auch ueber einen
        direkt aufgerufenen Link nicht herauskommen.
        """
        conn = self._verbinden()
        if conn is None:
            return None
        spalte = "File_Name" if "File_Name" in self._spalten else "file_name"
        if spalte not in self._spalten:
            return None
        try:
            treffer = conn.execute(
                f"SELECT * FROM {self.tabelle} WHERE {spalte} = ? LIMIT 1", (name,)
            ).fetchone()
        except sqlite3.Error:
            return None
        if treffer is None:
            return None
        datum = str(self._hol(treffer, "Date", "date") or "")
        if not datum:
            return None
        return {"datum": datum, "wiss": str(self._hol(treffer, "Sci_Name", "sci_name") or "")}


# Die Bildeinstellung des Spektrogramms.
#
# WARUM drange/limit und nicht nur gain: Ohne feste Grenzen skaliert ffmpeg
# jeden Streifen fuer sich. Bei einem durchlaufenden Band sieht man das sofort
# - an jeder Blockgrenze sitzt eine Helligkeitsnaht, alle 15 Sekunden eine.
# drange und limit legen die Skala in dBFS fest, damit ist ein leiser Block
# auch wirklich dunkler als ein lauter und die Streifen passen zusammen.
#
# WARUM limit=0/drange=75: Ein Spektrogramm zeigt Energie je Frequenzband.
# Breitbandiges Rauschen verteilt sich darauf und liegt viel tiefer als sein
# Gesamtpegel - im Garten etwa bei -70 dBFS, waehrend Gesang einzelne Baender
# bis etwa -15 dBFS treibt. Der Bereich deckt genau das ab.
#
# Feineinstellung am echten Garten ohne Codeaenderung: VOGEL_LIVE_BILD setzen.
BILD_VOLL = ("color=fire:scale=log:gain=1:drange=75:limit=0")
# Rueckfall fuer aeltere ffmpeg-Fassungen, die drange/limit noch nicht kennen.
BILD_ALT = ("color=fire:scale=log:gain=4")


class Zeichner:
    """Rendert einen Block zum Spektrogramm-Streifen und merkt sich das Bild.

    Gezeichnet wird erst auf Nachfrage. Schaut niemand zu, kostet der
    Live-Modus ausser dem Mitschnitt nichts.
    """

    def __init__(self, ring: Ring, ffmpeg: str, bild: str | None = None) -> None:
        self.ring = ring
        self.ffmpeg = ffmpeg
        self.bild = bild or self._bild_waehlen()
        self.endung, self.kodierer, self.typ = self._format_waehlen()
        self._sperren: dict[str, threading.Lock] = {}
        self._sperre_karte = threading.Lock()

    def _format_waehlen(self) -> tuple[str, list[str], str]:
        """WebP, wenn dieses ffmpeg es kann - sonst PNG.

        Ohne libwebp scheiterte sonst jeder Streifen still und das Band bliebe
        leer. PNG ist dann siebenmal so gross, aber immer noch besser als nichts.
        """
        try:
            liste = subprocess.run([self.ffmpeg, "-hide_banner", "-encoders"],
                                   capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            liste = ""
        if "libwebp" in liste:
            return "webp", ["-c:v", "libwebp", "-quality", WEBP_GUETE], "image/webp"
        LOG.warning("ffmpeg ohne libwebp - Streifen als PNG, deutlich groesser")
        return "png", ["-c:v", "png"], "image/png"

    def _bild_waehlen(self) -> str:
        """Kann dieses ffmpeg feste Helligkeitsgrenzen?

        drange und limit gibt es erst in neueren Fassungen. Auf einem Pi, der
        seit Jahren laeuft, kann eine aeltere stehen - dann scheitert jeder
        Streifen still und das Band bliebe leer. Einmal nachsehen ist billiger
        als eine Fehlersuche im Garten.
        """
        try:
            hilfe = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-h", "filter=showspectrumpic"],
                capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return BILD_ALT
        if "drange" in hilfe and "limit" in hilfe:
            return BILD_VOLL
        LOG.warning("ffmpeg kennt drange/limit nicht - Streifen ohne feste Skala, "
                    "an den Blockgrenzen koennen Helligkeitsnaehte stehen")
        return BILD_ALT

    def _sperre(self, name: str) -> threading.Lock:
        with self._sperre_karte:
            return self._sperren.setdefault(name, threading.Lock())

    def sperren_aufraeumen(self, lebende: set[str]) -> int:
        """Sperren zu Bloecken wegwerfen, die aus dem Ring gelaufen sind.

        Ohne das waechst die Tabelle unbegrenzt: je Block eine Sperre, vier
        Bloecke je Minute, rund zwei Millionen im Jahr - auf einem Pi mit
        1,8 GB Arbeitsspeicher ist das irgendwann spuerbar. Aufgeraeumt wird
        in derselben Runde wie der Ring, das kostet nichts extra.
        """
        with self._sperre_karte:
            weg = [n for n in self._sperren if n not in lebende]
            for n in weg:
                # Nur wegwerfen, was gerade niemand haelt.
                s = self._sperren[n]
                if s.acquire(blocking=False):
                    s.release()
                    del self._sperren[n]
            return len(weg)

    def streifen(self, name: str) -> bytes | None:
        bild = os.path.join(self.ring.ordner, name + "." + self.endung)
        with self._sperre(name):
            if os.path.exists(bild):
                try:
                    with open(bild, "rb") as fh:
                        return fh.read()
                except OSError:
                    pass
            quelle = self.ring.pfad(name)
            if not os.path.exists(quelle):
                return None
            breite = int(BLOCK_SEKUNDEN * PUNKTE_JE_SEKUNDE * SCHAERFE)
            hoehe = int(STREIFEN_HOEHE * SCHAERFE)
            # highpass raeumt Wind und Brummen weg, sonst ersaeuft der untere
            # Rand in einem Balken.
            filter_kette = (
                "highpass=f=180,"
                f"showspectrumpic=s={breite}x{hoehe}:"
                f"mode=combined:legend=disabled:{self.bild}"
            )
            # Den Kodierer ausdruecklich setzen: sonst waehlt ffmpeg ihn nach der
            # Dateiendung, und die Zwischendatei heisst nun einmal .neu - dann
            # kaeme ein JPEG heraus, das unter falschem Typ ausgeliefert wuerde.
            befehl = [
                self.ffmpeg, "-hide_banner", "-loglevel", "error",
                "-i", quelle, "-lavfi", filter_kette,
                "-frames:v", "1", *self.kodierer, "-f", "image2", "-y", bild + ".neu",
            ]
            try:
                subprocess.run(befehl, check=True, timeout=25,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                os.replace(bild + ".neu", bild)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as fehler:
                LOG.warning("Streifen %s nicht gezeichnet: %s", name, fehler)
                try:
                    os.remove(bild + ".neu")
                except OSError:
                    pass
                return None
            try:
                with open(bild, "rb") as fh:
                    return fh.read()
            except OSError:
                return None


class Dienst(BaseHTTPRequestHandler):
    """Drei Endpunkte, sonst nichts. Kein Verzeichnis, keine freie Pfadwahl."""

    server_version = "vogel-live"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    ring: Ring
    funde: Funde
    zeichner: Zeichner
    ausschnitte: str

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        LOG.debug("%s - %s", self.address_string(), format % args)

    def _senden(self, code: int, typ: str, koerper: bytes, cache: int = 0,
                zusatz: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", typ)
        for schluessel, wert in (zusatz or {}).items():
            # Zeilenumbrueche wuerden den Kopf zerlegen; der Wert kommt zwar
            # aus der eigenen Datenbank, wird aber trotzdem entschaerft.
            self.send_header(schluessel, wert.replace("\r", " ").replace("\n", " ")[:100])
        self.send_header("Content-Length", str(len(koerper)))
        self.send_header("Cache-Control",
                         f"private, max-age={cache}" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(koerper)

    def _fehler(self, code: int, text: str) -> None:
        self._senden(code, "application/json; charset=utf-8",
                     json.dumps({"fehler": text}).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        zerlegt = urllib.parse.urlparse(self.path)
        pfad = zerlegt.path
        frage = urllib.parse.parse_qs(zerlegt.query)
        if pfad == "/stand":
            self._stand()
        elif pfad == "/streifen":
            self._streifen(frage.get("block", [""])[0])
        elif pfad == "/aufnahme":
            self._aufnahme(frage.get("datei", [""])[0])
        else:
            self._fehler(404, "unbekannt")

    do_HEAD = do_GET

    def _stand(self) -> None:
        bloecke = self.ring.bloecke()
        antwort: dict = {
            "jetzt": round(time.time(), 2),
            "blocklaenge": BLOCK_SEKUNDEN,
            "punkte_je_sekunde": PUNKTE_JE_SEKUNDE,
            "hoehe": STREIFEN_HOEHE,
            "schaerfe": SCHAERFE,
            "bloecke": [],
            "funde": [],
            # Der Mitschnitt laeuft durchgehend als eigener Dienst. Hier steht
            # nur, ob wirklich Ton ankommt - nach einem Neustart des Pi dauert
            # es ein paar Sekunden, bis der erste Block liegt.
            "hoert_zu": bool(bloecke),
        }
        if not bloecke:
            # Der Mitschnitt laeuft noch nicht lange genug. Kein Fehler -
            # die Anzeige sagt dann einfach "hoere zu".
            self._senden(200, "application/json; charset=utf-8",
                         json.dumps(antwort).encode("utf-8"))
            return
        for name in bloecke:
            try:
                antwort["bloecke"].append({"name": name, "beginn": round(block_beginn(name), 2)})
            except ValueError:
                continue
        von = antwort["bloecke"][0]["beginn"]
        bis = antwort["bloecke"][-1]["beginn"] + BLOCK_SEKUNDEN
        antwort["funde"] = self.funde.im_fenster(von, bis)
        self._senden(200, "application/json; charset=utf-8",
                     json.dumps(antwort).encode("utf-8"))

    def _streifen(self, name: str) -> None:
        if not BLOCK_MUSTER.match(name):
            self._fehler(400, "kein Block")
            return
        bild = self.zeichner.streifen(name)
        if bild is None:
            self._fehler(404, "kein Streifen")
            return
        # Ein fertiger Streifen aendert sich nie mehr, darf also liegen bleiben.
        self._senden(200, self.zeichner.typ, bild, cache=600)

    def _aufnahme(self, name: str) -> None:
        """Die Ausschnittsdatei zu einer Erkennung.

        Drei Huerden, absichtlich uebereinander: der Name muss dem Muster
        genuegen, er muss in BirdNETs Datenbank stehen, und der aufgeloeste
        Pfad muss im Ausschnittsordner liegen. Erst dann wird gelesen.
        """
        if not name or not DATEI_MUSTER.match(name) or "/" in name or ".." in name:
            self._fehler(400, "kein Dateiname")
            return
        fund = self.funde.zu_datei(name)
        if fund is None:
            self._fehler(404, "nicht in der Datenbank")
            return
        treffer = self.funde.pfad_der_datei(fund["datum"], name)
        if treffer is None:
            self._fehler(404, "keine Aufnahme")
            return
        typ = "audio/mpeg" if treffer.lower().endswith(".mp3") else "audio/wav"
        try:
            with open(treffer, "rb") as fh:
                inhalt = fh.read()
        except OSError:
            self._fehler(404, "keine Aufnahme")
            return
        # Die Art wandert im Kopf mit, damit der Server die Ausblendliste
        # anwenden kann, ohne die Datenbank ein zweites Mal zu befragen.
        self._senden(200, typ, inhalt, cache=600,
                     zusatz={"X-Vogel-Art": fund["wiss"]})


def aufraeum_schleife(ring: Ring, zeichner: "Zeichner", halt: threading.Event) -> None:
    """Haelt den Ring klein, auch wenn niemand zuschaut - und die Sperren dazu."""
    while not halt.wait(30):
        ring.aufraeumen()
        try:
            namen = set(ring.bloecke())
            namen.update(n[:-4] for n in os.listdir(ring.ordner) if n.endswith(".wav"))
            zeichner.sperren_aufraeumen(namen)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    zerleger = argparse.ArgumentParser(description="Live-Modus fuer die Vogel-App")
    zerleger.add_argument("--ring", default=os.environ.get("VOGEL_LIVE_RING", "/run/vogel-live"))
    zerleger.add_argument("--db", default=os.environ.get(
        "VOGEL_LIVE_DB", os.path.expanduser("~/BirdNET-Pi/scripts/birds.db")))
    zerleger.add_argument("--ausschnitte", default=os.environ.get(
        "VOGEL_LIVE_AUSSCHNITTE", os.path.expanduser("~/BirdSongs/Extracted/By_Date")))
    zerleger.add_argument("--port", type=int, default=int(os.environ.get("VOGEL_LIVE_PORT", "8091")))
    zerleger.add_argument("--ffmpeg", default=os.environ.get("VOGEL_LIVE_FFMPEG") or shutil.which("ffmpeg") or "/usr/bin/ffmpeg")
    zerleger.add_argument("--bild", default=os.environ.get("VOGEL_LIVE_BILD"),
                          help="Bildeinstellung des Spektrogramms (sonst automatisch)")
    zerleger.add_argument("--briefkasten", default=os.environ.get(
        "VOGEL_BRIEFKASTEN", "http://127.0.0.1:8092"),
        help="Ablage auf dem Server, erreichbar ueber die eigene Tunnelverbindung")
    zerleger.add_argument("--takt", type=float, default=float(
        os.environ.get("VOGEL_LIVE_TAKT", "3")),
        help="wie oft im Briefkasten nachgesehen wird (Sekunden)")
    zerleger.add_argument("--laut", action="store_true", help="mehr im Protokoll")
    args = zerleger.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.laut else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    os.makedirs(args.ring, exist_ok=True)
    ring = Ring(args.ring)
    Dienst.ring = ring
    Dienst.funde = Funde(args.db, args.ausschnitte)
    Dienst.zeichner = Zeichner(ring, args.ffmpeg, args.bild)
    Dienst.ausschnitte = args.ausschnitte

    halt = threading.Event()
    threading.Thread(target=aufraeum_schleife,
                     args=(ring, Dienst.zeichner, halt), daemon=True).start()

    schieber = Schieber(args.briefkasten, ring, Dienst.zeichner,
                        Dienst.funde, args.ausschnitte)
    threading.Thread(target=schiebe_schleife,
                     args=(schieber, args.takt, halt), daemon=True).start()

    # Ausdruecklich nur die Rueckschleife. Nach aussen fuehrt allein der
    # Tunnel zum Server, und dort liegt das Anmeldetor davor.
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Dienst)
    LOG.info("Live-Dienst auf 127.0.0.1:%d, Ring %s", args.port, args.ring)
    LOG.info("Spektrogramm: %s, %s bei Schaerfe %s",
             Dienst.zeichner.bild, Dienst.zeichner.endung, SCHAERFE)
    LOG.info("Schiebt nach %s, sieht alle %gs nach", args.briefkasten, args.takt)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        halt.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
