#!/usr/bin/env python3
"""Rendert den 24-Stunden-Stand gehoerter Gartenvoegel als ruhiges PNG-Bild.

Gedacht als Sperrbildschirm fuer ein E-Ink-Handy (Bigme, Graustufen) und als
Mac-Bildschirmschoner. Liest die BirdNET-Pi-Detections-DB, zaehlt die Arten im
gewaehlten Zeitfenster und zeichnet sie als kleine Illustrations-Collage mit
deutschen Namen - haeufigere Arten groesser, seltene kleiner, aber immer klar
lesbar.

Kein Netzwerkzugriff, keine Schreibzugriffe auf die DB (read-only geoeffnet).
Nur Standardbibliothek + Pillow.

Aufruf:
    bild_rendern.py --db /pfad/zu/birds.db --illus /pfad/zu/illustrationen --out bild.png
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import math
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    sys.exit("FEHLER: Pillow fehlt (pip install pillow).")


# --------------------------------------------------------------------------
# Konstanten: Aussehen, Standardpfade, Schwellenwerte
# --------------------------------------------------------------------------

DEFAULT_DB = "/srv/pi-daten/birds.db"
DEFAULT_ILLUS = "/srv/avian/BirdNET-Pi/avian/assets/illustrations"
DEFAULT_W = 824
DEFAULT_H = 1648
DEFAULT_HOURS = 24

MAX_ARTEN = 12          # mehr Arten passen ohnehin nicht ruhig auf ein Bild
SUPERSAMPLE = 2          # intern in 2x rendern, am Ende sauber herunterskalieren

# Untergrenzen in LOGISCHEN Pixeln (bei der Zielaufloesung, nicht x SUPERSAMPLE) -
# darunter waere Text/Illustration auf E-Ink nicht mehr klar lesbar. Diese
# Grenzen sind bewusst hart: lieber eine Art weniger zeigen als eine, die man
# nicht mehr lesen kann.
MIN_IMG_PX = 66
MIN_FONT_PX = 15

# Warmes Papier/Tinte-Thema. Bewusst nur zwei Toene (Hintergrund + Tinte) fuer
# durchgehend hohen Kontrast - "dezent" wird ueber Groesse/Abstand erreicht,
# nicht ueber blasses Grau (das auf E-Ink schnell verschwindet).
PALETTE = {
    False: {"bg": (0xFA, 0xF7, 0xF0), "ink": (0x2B, 0x2B, 0x2B)},
    True: {"bg": (0x1C, 0x1A, 0x17), "ink": (0xEA, 0xE4, 0xD6)},
}

# DejaVu Serif ist der auf Linux-Servern uebliche Pfad (siehe Aufgabenstellung).
# Georgia als zweiter Kandidat sorgt dafuer, dass lokale Tests auf macOS
# ebenfalls eine echte Serifenschrift zeigen statt sofort auf Pillows
# Standardschrift zurueckzufallen - faellt aber genauso auf load_default()
# zurueck, wenn keiner der Kandidaten existiert.
FONT_CANDIDATES = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    ],
    "italic": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    ],
}

MONATE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


# --------------------------------------------------------------------------
# Kommandozeile
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rendert den 24h-Stand gehoerter Gartenvoegel als PNG (Sperrbildschirm/Screensaver)."
    )
    p.add_argument("--db", default=DEFAULT_DB, help=f"Pfad zur SQLite-DB (Standard: {DEFAULT_DB})")
    p.add_argument("--illus", default=DEFAULT_ILLUS, help=f"Ordner mit Illustrationen (Standard: {DEFAULT_ILLUS})")
    p.add_argument("--w", type=int, default=DEFAULT_W, help=f"Breite in Pixeln (Standard: {DEFAULT_W})")
    p.add_argument("--h", type=int, default=DEFAULT_H, help=f"Hoehe in Pixeln (Standard: {DEFAULT_H})")
    p.add_argument("--hours", type=int, default=DEFAULT_HOURS, help=f"Zeitfenster in Stunden (Standard: {DEFAULT_HOURS})")
    p.add_argument("--out", required=True, help="Ausgabedatei (PNG); '-' schreibt nach stdout")
    p.add_argument("--dark", action="store_true", help="Dunkler Hintergrund statt creme")
    args = p.parse_args(argv)

    if args.w <= 0 or args.h <= 0:
        p.error("--w/--h muessen positiv sein")
    if args.hours <= 0:
        p.error("--hours muss positiv sein")
    return args


# --------------------------------------------------------------------------
# Schrift
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _resolve_font_path(style: str) -> str | None:
    for path in FONT_CANDIDATES.get(style, []):
        if Path(path).exists():
            return path
    return None


@functools.lru_cache(maxsize=None)
def get_font(style: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(1, int(round(size)))
    path = _resolve_font_path(style)
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


_MEASURE_DRAW = ImageDraw.Draw(Image.new("RGB", (2, 2)))


def measure(text: str, font: ImageFont.FreeTypeFont) -> tuple[float, float]:
    if not text:
        return 0.0, 0.0
    l, t, r, b = _MEASURE_DRAW.textbbox((0, 0), text, font=font)
    return float(r - l), float(b - t)


def wrap_generic(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    """Einfacher, breitenbasierter Zeilenumbruch (fuer Fliesstext wie den Leerzustand)."""
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if not cur or measure(trial, font)[0] <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def wrap_name(name: str, font: ImageFont.FreeTypeFont, budget_w: float):
    """Voegel-Name evtl. auf 2 Zeilen umbrechen, wenn er deutlich breiter als
    sein Bildkasten ist. Einzelne lange Komposita ohne Leerzeichen (z.B.
    'Wintergoldhaehnchen') werden bewusst NICHT mitten im Wort getrennt - dann
    wird die Spalte fuer diese Art halt etwas breiter."""
    w, h = measure(name, font)
    if w <= budget_w or " " not in name.strip():
        return [name], w, h

    words = name.split(" ")
    best = None
    for i in range(1, len(words)):
        line1, line2 = " ".join(words[:i]), " ".join(words[i:])
        w1, h1 = measure(line1, font)
        w2, h2 = measure(line2, font)
        score = max(w1, w2)
        if best is None or score < best[0]:
            best = (score, line1, line2, w1, w2, h1)
    if best and best[0] < w * 0.92:
        _, line1, line2, w1, w2, lh = best
        return [line1, line2], max(w1, w2), lh * 2 + lh * 0.3
    return [name], w, h


# --------------------------------------------------------------------------
# Datenquelle
# --------------------------------------------------------------------------

def slugify(sci_name: str) -> str:
    s = sci_name.strip().lower()
    s = "-".join(s.split())
    return "".join(ch for ch in s if ch.isalnum() or ch == "-")


def lade_arten(db_path: str, hours: int, now: datetime) -> list[dict]:
    """Liest detections der letzten `hours` Stunden und aggregiert nach Art.

    Beendet den Prozess mit Exitcode 1 und einer Meldung auf stderr, wenn die
    DB fehlt oder nicht lesbar ist - das ist der einzige harte Fehlerfall, den
    die Aufgabenstellung verlangt.
    """
    path = Path(db_path)
    if not path.exists():
        print(f"FEHLER: Datenbank nicht gefunden: {path}", file=sys.stderr)
        sys.exit(1)

    cutoff = now - timedelta(hours=hours)
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        # Grosszuegiger Vorfilter auf Tagesbasis (schnell, kein Index noetig);
        # die genaue Grenze wird gleich in Python mit vollem datetime geprueft.
        vorfilter_datum = (cutoff - timedelta(days=1)).strftime("%Y-%m-%d")
        rows = con.execute(
            "SELECT Date, Time, Sci_Name, Com_Name FROM detections WHERE Date >= ?",
            (vorfilter_datum,),
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        print(f"FEHLER: Datenbank konnte nicht gelesen werden ({path}): {exc}", file=sys.stderr)
        sys.exit(1)

    counts: Counter = Counter()
    com_names: dict[str, Counter] = defaultdict(Counter)
    latest: dict[str, datetime] = {}

    for date_s, time_s, sci, com in rows:
        if not sci or not date_s or not time_s:
            continue
        try:
            dt = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if dt < cutoff or dt > now + timedelta(minutes=5):
            continue
        sci = sci.strip()
        counts[sci] += 1
        com_names[sci][(com or "").strip()] += 1
        if sci not in latest or dt > latest[sci]:
            latest[sci] = dt

    arten = []
    for sci, cnt in counts.items():
        com = com_names[sci].most_common(1)[0][0] if com_names[sci] else sci
        arten.append({
            "sci_name": sci,
            "com_name": com or sci,
            "count": cnt,
            "last_seen": latest[sci],
        })
    arten.sort(key=lambda a: (-a["count"], -a["last_seen"].timestamp(), a["com_name"]))
    return arten


# --------------------------------------------------------------------------
# Datum / Text
# --------------------------------------------------------------------------

def format_datum_de(dt: datetime) -> str:
    return f"{dt.day}. {MONATE[dt.month - 1]} {dt.year}"


# --------------------------------------------------------------------------
# Platzhalter fuer fehlende Illustration ("Nest")
# --------------------------------------------------------------------------

def clean_alpha(img: Image.Image, threshold: int = 36) -> Image.Image:
    """Manche Illustrationen sind nicht ganz sauber freigestellt und tragen
    einen kaum sichtbaren Hintergrund-Schleier (z.B. eine Papier-/Scan-Textur
    mit ~10-15% Deckkraft ueber dem ganzen Bild - beobachtet bei
    corvus-corone.png). Auf hellem/dunklem Fond faellt das als schwaches
    Rechteck auf. Wir kappen sehr niedrige Alpha-Werte auf 0 und strecken den
    Rest wieder auf den vollen Bereich; echte halbtransparente Federkanten
    (die naeher an voll deckend liegen) bleiben dabei praktisch unveraendert."""
    r, g, b, a = img.split()
    lut = [0 if v < threshold else round((v - threshold) * 255 / (255 - threshold)) for v in range(256)]
    a = a.point(lut)
    return Image.merge("RGBA", (r, g, b, a))


def draw_nest(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float, ink) -> None:
    r = size / 2
    stroke = max(2, round(size * 0.045))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ink, width=stroke)
    egg_r = size * 0.09
    for dx, dy in ((-egg_r * 1.6, egg_r * 0.5), (0, -egg_r * 0.5), (egg_r * 1.6, egg_r * 0.5)):
        ex, ey = cx + dx, cy + dy
        draw.ellipse([ex - egg_r, ey - egg_r * 1.15, ex + egg_r, ey + egg_r * 1.15], fill=ink)


# --------------------------------------------------------------------------
# Collage-Layout
# --------------------------------------------------------------------------

def normalized_scale(counts: dict, lo=0.68, hi=1.42) -> dict:
    """Bildet Haeufigkeiten (Anzahl Detections) auf einen sanften Groessenfaktor
    ab. log-skaliert, damit ein Ausreisser (z.B. 22 vs. 1) nicht die seltenen
    Arten auf Briefmarkengroesse schrumpft."""
    if not counts:
        return {}
    if len(counts) == 1:
        only = next(iter(counts))
        return {only: (lo + hi) / 2 + 0.06}
    logs = {k: math.log(v + 1) for k, v in counts.items()}
    vmin, vmax = min(logs.values()), max(logs.values())
    if vmax - vmin < 1e-9:
        return {k: (lo + hi) / 2 for k in counts}
    return {k: lo + (v - vmin) / (vmax - vmin) * (hi - lo) for k, v in logs.items()}


def build_cells(subset, rel_scale, content_w, ss, gscale, enforce_min):
    base_img = content_w * 0.235
    base_font = content_w * 0.0275
    cap_gap = content_w * 0.018
    min_img = (MIN_IMG_PX * ss) if enforce_min else (40 * ss)
    min_font = (MIN_FONT_PX * ss) if enforce_min else (10 * ss)

    cells = []
    for idx, item in enumerate(subset):
        s = rel_scale[idx]
        img_side = base_img * s * gscale
        if img_side < min_img:
            return None
        font_size = base_font * (0.74 + 0.26 * s) * gscale
        if font_size < min_font:
            return None
        font = get_font("regular", round(font_size))
        lines, text_w, text_h = wrap_name(item["com_name"], font, img_side * 1.55)
        cell_w = max(img_side, text_w)
        cell_h = img_side + cap_gap + text_h
        cells.append({
            "item": item, "img_side": img_side, "font": font, "lines": lines,
            "cap_gap": cap_gap, "w": cell_w, "h": cell_h,
        })
    return cells


def shelf_pack(cells, content_w, gap_min):
    rows, current, current_w = [], [], 0.0
    for c in cells:
        extra = gap_min if current else 0.0
        if current and current_w + extra + c["w"] > content_w:
            rows.append(current)
            current, current_w = [c], c["w"]
        else:
            current.append(c)
            current_w += extra + c["w"]
    if current:
        rows.append(current)
    return rows


def gentle_jitter(max_range: float, rng: random.Random, damp=0.45) -> float:
    if max_range <= 0:
        return 0.0
    center = max_range / 2
    wobble = rng.uniform(-1, 1) * center * damp
    return min(max(center + wobble, 0.0), max_range)


def make_seed(subset) -> int:
    key = "|".join(f"{a['sci_name']}:{a['count']}" for a in subset)
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], 16)


def finalize_positions(rows, content_w, gap_min, row_gap, rng):
    placed = []
    y = 0.0
    for row in rows:
        row_h = max(c["h"] for c in row)
        natural_w = sum(c["w"] for c in row) + gap_min * (len(row) - 1)
        leftover = max(0.0, content_w - natural_w)
        extra_each = leftover / len(row)
        x = 0.0
        for c in row:
            slot_w = c["w"] + extra_each
            off_x = gentle_jitter(slot_w - c["w"], rng)
            off_y = gentle_jitter(row_h - c["h"], rng)
            angle = rng.uniform(-3.2, 3.2)
            placed.append((c, x + off_x, y + off_y, angle))
            x += slot_w
        y += row_h + row_gap
    used_h = y - row_gap if rows else 0.0
    return placed, used_h


def build_layout(species: list[dict], content_w: float, content_h: float, ss: int):
    """Sucht die groesstmoegliche Darstellung (Artenzahl x Groessenfaktor), die
    ohne Ueberlappung in content_w x content_h passt. Reduziert zuerst die
    globale Groesse, dann notfalls die Artenzahl. Erste Runde mit harter
    Lesbarkeits-Untergrenze, zweite Runde (nur als Notanker) ohne."""
    max_n = min(MAX_ARTEN, len(species))
    gap_min = content_w * 0.024
    row_gap = content_h * 0.017
    # Geometrisch gestufte Kandidaten von gross nach klein: bei wenigen Arten
    # (n=1..3) darf der Massstab weit ueber 1.0 hinausgehen, damit sie die
    # Flaeche wirklich fuellen statt verloren in der Ecke zu haengen; bei
    # vielen Arten scheitern die grossen Werte einfach schnell und die Suche
    # landet bei einem kleineren, passenden Wert. Uebrig bleibender Platz wird
    # danach ohnehin als Zeilenabstand verteilt (siehe unten).
    scale_candidates = []
    v = 3.6
    while v > 0.4:
        scale_candidates.append(round(v, 3))
        v *= 0.89

    for enforce_min in (True, False):
        for n in range(max_n, 0, -1):
            subset = species[:n]
            rel = normalized_scale({i: a["count"] for i, a in enumerate(subset)})
            rng = random.Random(make_seed(subset))
            for g in scale_candidates:
                cells = build_cells(subset, rel, content_w, ss, g, enforce_min)
                if cells is None:
                    continue
                if any(c["w"] > content_w for c in cells):
                    continue
                rows = shelf_pack(cells, content_w, gap_min)
                total_h = sum(max(c["h"] for c in row) for row in rows) + row_gap * (len(rows) - 1)
                if total_h <= content_h:
                    # Bin-Packing springt zwischen Zeilenzahlen oft sprunghaft;
                    # den so entstehenden Rest nicht als toten Raum am Rand
                    # liegen lassen, sondern als zusaetzlichen Zeilenabstand
                    # verteilen - fuellt die Flaeche ruhiger und gleichmaessiger.
                    if len(rows) > 1:
                        # Gedeckelt, damit bei sehr wenigen Zeilen (z.B. genau
                        # 2) nicht der gesamte Rest in eine einzelne, unnatuerlich
                        # grosse Luecke wandert. Was uebrig bleibt, uebernimmt
                        # anschliessend die Zentrierung des ganzen Blocks in render().
                        max_extra = content_h * 0.05
                        extra = min((content_h - total_h) / (len(rows) - 1), max_extra)
                        row_gap = row_gap + extra
                    return finalize_positions(rows, content_w, gap_min, row_gap, rng)
    # Letzter Notanker (praktisch nur bei absurd kleiner Leinwand): eine Art,
    # kleinstmoeglicher Massstab, keine Untergrenzen - Hauptsache kein Absturz.
    subset = species[:1]
    rel = normalized_scale({0: subset[0]["count"]})
    rng = random.Random(make_seed(subset))
    cells = build_cells(subset, rel, content_w, ss, 0.4, enforce_min=False)
    if cells is None:
        return [], 0.0
    rows = shelf_pack(cells, content_w, gap_min)
    return finalize_positions(rows, content_w, gap_min, row_gap, rng)


# --------------------------------------------------------------------------
# Zeichnen
# --------------------------------------------------------------------------

def draw_header(draw, rw, content_x0, content_x1, top_y, hours, now, ink) -> float:
    cx = (content_x0 + content_x1) / 2
    max_w = (content_x1 - content_x0) * 0.98

    eyebrow_font = get_font("italic", rw * 0.0205)
    eyebrow = "gehört"
    ew, eh = measure(eyebrow, eyebrow_font)
    draw.text((cx - ew / 2, top_y), eyebrow, font=eyebrow_font, fill=ink)
    y = top_y + eh * 1.7

    big_text = "heute" if hours <= 24 else f"die letzten {hours} Stunden"
    size = rw * 0.064
    big_font = get_font("bold", size)
    bw, bh = measure(big_text, big_font)
    while bw > max_w and size > rw * 0.03:
        size *= 0.92
        big_font = get_font("bold", size)
        bw, bh = measure(big_text, big_font)
    draw.text((cx - bw / 2, y), big_text, font=big_font, fill=ink)
    y += bh * 1.4

    if hours <= 24:
        small_text = format_datum_de(now)
    else:
        small_text = f"bis {format_datum_de(now)}, {now.strftime('%H:%M')} Uhr"
    small_font = get_font("regular", rw * 0.0235)
    sw, sh = measure(small_text, small_font)
    draw.text((cx - sw / 2, y), small_text, font=small_font, fill=ink)
    y += sh * 1.6

    rule_w = (content_x1 - content_x0) * 0.13
    stroke = max(2 * SUPERSAMPLE, round(rw * 0.003))
    draw.line([(cx - rule_w / 2, y), (cx + rule_w / 2, y)], fill=ink, width=stroke)
    y += stroke + rw * 0.028
    return y


def draw_empty_state(draw, content_x0, content_x1, content_y0, content_y1, ink, rw, hours) -> None:
    cx = (content_x0 + content_x1) / 2
    cy = (content_y0 + content_y1) / 2
    nest_size = min(content_x1 - content_x0, content_y1 - content_y0) * 0.15

    text = "In den letzten Stunden war es still." if hours == 24 else f"In den letzten {hours} Stunden war es still."
    font = get_font("italic", rw * 0.027)
    max_w = (content_x1 - content_x0) * 0.78
    lines = [text] if measure(text, font)[0] <= max_w else wrap_generic(text, font, max_w)

    line_h = measure("Ag", font)[1] * 1.35
    total_h = nest_size + rw * 0.045 + line_h * len(lines)
    top = cy - total_h / 2

    draw_nest(draw, cx, top + nest_size / 2, nest_size, ink)
    ty = top + nest_size + rw * 0.045
    for line in lines:
        lw, lh = measure(line, font)
        draw.text((cx - lw / 2, ty), line, font=font, fill=ink)
        ty += line_h


def draw_species_cell(canvas, draw, cell, x, y, ink, illus_cache) -> None:
    cell_obj, ox, oy, angle = cell
    x, y = x + ox, y + oy
    img_side = cell_obj["img_side"]
    cx = x + cell_obj["w"] / 2

    thumb = illus_cache.get(cell_obj["item"]["sci_name"])
    if thumb is not None:
        fitted = ImageOps.contain(thumb, (round(img_side), round(img_side)), method=Image.LANCZOS)
        if abs(angle) > 0.05:
            fitted = fitted.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
        ix = round(cx - fitted.width / 2)
        iy = round(y + (img_side - fitted.height) / 2)
        canvas.paste(fitted, (ix, iy), fitted)
    else:
        draw_nest(draw, cx, y + img_side / 2, img_side * 0.8, ink)

    ty = y + img_side + cell_obj["cap_gap"]
    line_h = measure("Ag", cell_obj["font"])[1] * 1.3
    for line in cell_obj["lines"]:
        lw, _ = measure(line, cell_obj["font"])
        draw.text((cx - lw / 2, ty), line, font=cell_obj["font"], fill=ink)
        ty += line_h


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------

def render(args: argparse.Namespace, now: datetime) -> Image.Image:
    arten = lade_arten(args.db, args.hours, now)
    top = arten[:MAX_ARTEN]

    ss = SUPERSAMPLE
    rw, rh = args.w * ss, args.h * ss
    palette = PALETTE[args.dark]
    bg, ink = palette["bg"], palette["ink"]

    canvas = Image.new("RGB", (rw, rh), bg)
    draw = ImageDraw.Draw(canvas)

    margin_x = round(rw * 0.075)
    bottom_margin = round(rh * 0.035)
    content_x0, content_x1 = margin_x, rw - margin_x

    # Kopfzeilen-Schriftgroessen sind an rw gekoppelt (fuer normale Hoch-
    # formate genau richtig). Auf einer kurzen/breiten Leinwand (z.B. ein
    # 800x480-E-Ink-Rahmen) wuerde der Kopf sonst unverhaeltnismaessig viel
    # von der knappen Hoehe auffressen - hier wird er samt Aussenabstand
    # gestaucht, damit mehr Platz fuer die Collage bleibt.
    header_scale = min(1.0, (rh / rw) / 0.85)
    header_rw = rw * header_scale
    top_margin = round(rh * 0.04 * max(header_scale, 0.55))

    header_bottom = draw_header(draw, header_rw, content_x0, content_x1, top_margin, args.hours, now, ink)
    content_y0, content_y1 = header_bottom, rh - bottom_margin

    if not top:
        draw_empty_state(draw, content_x0, content_x1, content_y0, content_y1, ink, rw, args.hours)
    else:
        illus_dir = Path(args.illus)
        illus_cache = {}
        for a in top:
            p = illus_dir / f"{slugify(a['sci_name'])}.png"
            img = None
            if p.exists():
                try:
                    img = clean_alpha(Image.open(p).convert("RGBA"))
                except Exception:
                    img = None
            illus_cache[a["sci_name"]] = img

        placed, used_h = build_layout(top, content_x1 - content_x0, content_y1 - content_y0, ss)
        avail_h = content_y1 - content_y0
        # Bei >1 Zeile hat build_layout uebrigen Platz schon als Zeilenabstand
        # verteilt (used_h ~= avail_h); bei nur einer Zeile (z.B. 1-2 Arten)
        # zentrieren wir den Rest hier - leicht obenlastig, das wirkt auf
        # einer Wandanzeige ruhiger als exakt mittig.
        y_start = content_y0 + max(0.0, (avail_h - used_h) * 0.46)
        for cell in placed:
            draw_species_cell(canvas, draw, cell, content_x0, y_start, ink, illus_cache)

    if ss != 1:
        canvas = canvas.resize((args.w, args.h), Image.LANCZOS)
    return canvas


def main(argv=None) -> None:
    args = parse_args(argv)
    now = datetime.now()
    img = render(args, now)

    if args.out == "-":
        img.save(sys.stdout.buffer, format="PNG")
        sys.stdout.buffer.flush()
    else:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, format="PNG")


if __name__ == "__main__":
    main()
