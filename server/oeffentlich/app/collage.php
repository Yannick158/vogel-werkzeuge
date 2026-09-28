<?php
// Collage fuer lokale Standorte (ARCHITEKTUR.md 10.4, Bildschirm S11).
//
// Das Handy hoert selbst mit seinem Mikrofon; die Funde bleiben auf dem Gerät.
// Damit die Collage trotzdem genauso aussieht wie die vom Garten, schickt die
// App nur die ARTENLISTE hierher und bekommt ein PNG zurueck.
//
// WAS HIER NICHT PASSIERT:
//   * Nichts wird gespeichert. Die Artenliste steht in einer temporaeren
//     SQLite-Datei, die noch im selben Aufruf geloescht wird - und in keiner
//     Logzeile (die Liste verriete, wo und wann jemand zugehoert hat).
//   * Keine Datenbank des Gartens wird angefasst. Dieser Endpunkt steht VOR
//     dem Anmeldetor und darf von dort nichts wissen; deshalb kommen die
//     deutschen Namen aus der eigenen Datei namen-de.json.
//   * Kein Pfad kommt aus der Anfrage. Der Renderer, der Illustrationsordner
//     und die Namensdatei sind Code-Literale ueber APP_WURZEL
//     (SICHERHEIT.md B5/S-6); die temporaere DB legt tempnam() an.
//
// Vertrag:
//   POST  {"titel":"Handy","arten":[{"sci":"Turdus merula","anzahl":3},…],
//          "w":900,"h":900,"dark":0}
//   ->    image/png, Cache-Control: no-store
//   Grenzen: Koerper <= 8 KB, hoechstens 40 Arten, anzahl 1..9999,
//   w 200..2000, h 200..2600 (wie bild.php), Ratenbremse 6/min je Client-IP.
//   Arten ohne Illustration werden ausgelassen; bleibt keine uebrig: 422.
declare(strict_types=1);

// Gemeinsame Bausteine (Ratenbremse, JSON-Lesen, app_pfad). Wie bild.php:
// die Wurzel '/srv' laesst sich ueber APP_WURZEL auf die Testumgebung
// umlenken, damit hier nichts doppelt gepflegt werden muss.
$__wurzel = getenv('APP_WURZEL');
require (is_string($__wurzel) && $__wurzel !== '' ? rtrim($__wurzel, '/') : '/srv')
      . '/avian/eigenes/app/_gemeinsam.php';

const COLLAGE_MAX_KOERPER = 8192;    // Bytes; mehr braucht eine Artenliste nie
const COLLAGE_MAX_ARTEN   = 40;      // Vertrag 10.4
const COLLAGE_MAX_ANZAHL  = 9999;    // je Art
const COLLAGE_MAX_ZEILEN  = 20000;   // Summe aller Zeilen in der temporaeren DB
const COLLAGE_MAX_TITEL   = 40;      // Zeichen
const COLLAGE_TAKT        = 6;       // Aufrufe je Minute und IP

// Pfade als Literale (ueber APP_WURZEL zusammengesetzt), nie aus der Anfrage.
const COLLAGE_ILLUS   = 'avian/BirdNET-Pi/avian/assets/illustrations';
const COLLAGE_RENDER  = 'avian/eigenes/bild_rendern.py';
const COLLAGE_NAMEN   = 'avian/oeffentlich/app/namen-de.json';
const COLLAGE_PYTHON  = 'python3';

/** Abbruch mit kurzem Grund. Enthaelt nie etwas aus der Artenliste. */
function collage_abbruch(int $code, string $grund): void {
    http_response_code($code);
    if (!headers_sent()) {
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
    }
    echo json_encode(['fehler' => $grund], JSON_UNESCAPED_UNICODE);
    exit;
}

/**
 * Nachbau von slugify() aus bild_rendern.py (Zeile 192) - Zeichen fuer Zeichen
 * dasselbe Ergebnis, sonst faende diese Datei andere Illustrationen als der
 * Renderer:
 *     s = sci.strip().lower(); s = "-".join(s.split())
 *     s = "".join(ch for ch in s if ch.isalnum() or ch == "-")
 * Die Eingabe ist durch das sci-Muster auf ASCII beschraenkt, deshalb reicht
 * strtolower/[^a-z0-9-].
 */
function collage_slug(string $sci): string {
    $s = strtolower(trim($sci));
    $teile = preg_split('/\s+/', $s, -1, PREG_SPLIT_NO_EMPTY);
    $s = is_array($teile) ? implode('-', $teile) : '';
    return (string)preg_replace('/[^a-z0-9-]/', '', $s);
}

/** Temporaere Datenbank samt Begleitdateien restlos entfernen. */
function collage_aufraeumen(?string $tmp): void {
    if ($tmp === null || $tmp === '') return;
    foreach (['', '-journal', '-wal', '-shm'] as $anhang) {
        if (is_file($tmp . $anhang)) @unlink($tmp . $anhang);
    }
}

// --- Methode und Herkunft -------------------------------------------------
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
    header('Allow: POST');
    collage_abbruch(405, 'nur POST');
}
// 403 nur bei einem FREMDEN Origin (SICHERHEIT.md B6). Anfragen der App haben
// gar keinen - die laufen wie bisher durch.
if (!herkunft_ok()) {
    collage_abbruch(403, 'fremde herkunft');
}

// --- Koerper lesen und begrenzen ------------------------------------------
$laenge = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
if ($laenge > COLLAGE_MAX_KOERPER) {
    collage_abbruch(413, 'koerper zu gross');
}
$roh = (string)@file_get_contents('php://input', false, null, 0, COLLAGE_MAX_KOERPER + 1);
if (strlen($roh) > COLLAGE_MAX_KOERPER) {
    collage_abbruch(413, 'koerper zu gross');
}

// --- Ratenbremse ----------------------------------------------------------
// Je Client-IP, weil dieser Endpunkt kein Token kennt. Der Schluessel ist ein
// SHA-256 der Adresse, nie die Adresse selbst - im takt-Verzeichnis soll nicht
// stehen, wer wann eine Collage gebaut hat. Hinter Caddy steht in REMOTE_ADDR
// bereits die echte Client-Adresse (trusted_proxies ist gesetzt).
$ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
if (!takt_pruefen(hash('sha256', 'collage|' . $ip), 'collage', COLLAGE_TAKT)) {
    header('Retry-After: 60');
    collage_abbruch(429, 'zu viele anfragen');
}

// --- Eingabe pruefen ------------------------------------------------------
$j = json_decode($roh, true);
if (!is_array($j)) {
    collage_abbruch(400, 'kein json');
}

$titel = $j['titel'] ?? '';
if (!is_string($titel) || mb_strlen($titel, 'UTF-8') > COLLAGE_MAX_TITEL) {
    collage_abbruch(400, 'titel');
}

$roh_arten = $j['arten'] ?? null;
if (!is_array($roh_arten) || $roh_arten === [] || !array_is_list($roh_arten)) {
    collage_abbruch(400, 'arten');
}
if (count($roh_arten) > COLLAGE_MAX_ARTEN) {
    collage_abbruch(400, 'zu viele arten');
}

$w = max(200, min(2000, (int)($j['w'] ?? 900)));
$h = max(200, min(2600, (int)($j['h'] ?? 900)));

$dark_roh = $j['dark'] ?? 0;
if (is_bool($dark_roh)) {
    $dark = $dark_roh;
} elseif (is_int($dark_roh) && ($dark_roh === 0 || $dark_roh === 1)) {
    $dark = ($dark_roh === 1);
} else {
    collage_abbruch(400, 'dark');
}

$ILLUS  = app_pfad(COLLAGE_ILLUS);
$namen  = json_lesen(app_pfad(COLLAGE_NAMEN), []);
$namen  = is_array($namen['namen'] ?? null) ? $namen['namen'] : [];

$gefunden = [];   // sci => anzahl (nur Arten, zu denen es eine Illustration gibt)
foreach ($roh_arten as $eintrag) {
    if (!is_array($eintrag)) {
        collage_abbruch(400, 'art');
    }
    $sci = $eintrag['sci'] ?? '';
    if (!is_string($sci)) {
        collage_abbruch(400, 'sci');
    }
    $sci = trim($sci);
    if (preg_match('/^[A-Za-z][A-Za-z .-]{2,60}$/D', $sci) !== 1) {
        collage_abbruch(400, 'sci');
    }
    $anzahl = $eintrag['anzahl'] ?? null;
    if (is_string($anzahl) && ctype_digit($anzahl)) {
        $anzahl = (int)$anzahl;
    }
    if (!is_int($anzahl) || $anzahl < 1 || $anzahl > COLLAGE_MAX_ANZAHL) {
        collage_abbruch(400, 'anzahl');
    }

    // Unbekannt heisst: es gibt keine Illustration. Solche Arten werden
    // ausgelassen (Vertrag 10.4) - der Renderer zeichnete sonst eine Luecke.
    $slug = collage_slug($sci);
    if ($slug === '' || !is_file($ILLUS . '/' . $slug . '.png')) {
        continue;
    }
    // Dieselbe Art doppelt geschickt: Zahlen addieren, gedeckelt.
    $gefunden[$sci] = min(COLLAGE_MAX_ANZAHL, ($gefunden[$sci] ?? 0) + $anzahl);
}

if ($gefunden === []) {
    collage_abbruch(422, 'keine bekannte art');
}

// Gesamtzahl der Zeilen deckeln. Der Renderer richtet sich nach den
// VERHAELTNISSEN der Zahlen, nicht nach ihrer absoluten Hoehe - ein
// gleichmaessiges Herunterrechnen sieht deshalb genauso aus, kostet aber bei
// 40 x 9999 nicht 400.000 Zeilen Schreib- und Lesearbeit.
$summe = array_sum($gefunden);
if ($summe > COLLAGE_MAX_ZEILEN) {
    $faktor = COLLAGE_MAX_ZEILEN / $summe;
    foreach ($gefunden as $sci => $n) {
        $gefunden[$sci] = max(1, (int)round($n * $faktor));
    }
}

// --- Temporaere Datenbank -------------------------------------------------
$RENDERER = app_pfad(COLLAGE_RENDER);
if (!is_readable($RENDERER)) {
    collage_abbruch(503, 'renderer fehlt');
}

$tmp = tempnam(sys_get_temp_dir(), 'collage');
if ($tmp === false) {
    collage_abbruch(500, 'kein temp');
}
@chmod($tmp, 0600);
// Auch bei einem Abbruch mittendrin darf nichts liegen bleiben.
register_shutdown_function(static function () use ($tmp): void { collage_aufraeumen($tmp); });

try {
    $db = new SQLite3($tmp, SQLITE3_OPEN_READWRITE | SQLITE3_OPEN_CREATE);
} catch (Throwable $e) {
    collage_abbruch(500, 'keine temp-db');
}
$db->busyTimeout(2000);
// Ohne Journal/WAL bleibt es bei EINER Datei - und die Daten muessen nichts
// ueberleben, sie sind in ein paar Zeilen wieder weg.
$db->exec('PRAGMA journal_mode = OFF');
$db->exec('PRAGMA synchronous = OFF');
// Spalten wie in birds.db (BirdNET-Pi). Der Renderer liest nur Date, Time,
// Sci_Name und Com_Name; der Rest steht da, damit die Datei dem Original
// gleicht und kuenftige Renderer-Fassungen nicht stolpern. Lat/Lon bleiben
// leer - eine Position gehoert hier nicht hin.
$db->exec(
    'CREATE TABLE detections (Date TEXT, Time TEXT, Sci_Name TEXT, Com_Name TEXT, '
    . 'Confidence REAL, File_Name TEXT, Lat REAL, Lon REAL, Cutoff REAL, '
    . 'Week INTEGER, Sens REAL, Overlap REAL)');

$jetzt  = new DateTimeImmutable('now', new DateTimeZone('Europe/Beispielstadt'));
$datum  = $jetzt->format('Y-m-d');
$uhrzeit = $jetzt->format('H:i:s');
$woche  = (int)$jetzt->format('W');

$einf = $db->prepare(
    'INSERT INTO detections (Date,Time,Sci_Name,Com_Name,Confidence,File_Name,'
    . 'Lat,Lon,Cutoff,Week,Sens,Overlap) VALUES (?,?,?,?,?,?,NULL,NULL,0.7,?,1.25,0.0)');
if ($einf === false) {
    collage_abbruch(500, 'temp-db');
}

$db->exec('BEGIN');
foreach ($gefunden as $sci => $anzahl) {
    // Deutscher Name aus namen-de.json; fehlt er, steht der wissenschaftliche
    // unter der Illustration (so macht es der Renderer ohnehin als Rueckfall).
    $de = (string)($namen[$sci] ?? '');
    if ($de === '') $de = $sci;
    for ($i = 0; $i < $anzahl; $i++) {
        $einf->bindValue(1, $datum, SQLITE3_TEXT);
        $einf->bindValue(2, $uhrzeit, SQLITE3_TEXT);
        $einf->bindValue(3, $sci, SQLITE3_TEXT);
        $einf->bindValue(4, $de, SQLITE3_TEXT);
        $einf->bindValue(5, 0.9, SQLITE3_FLOAT);
        $einf->bindValue(6, '', SQLITE3_TEXT);
        $einf->bindValue(7, $woche, SQLITE3_INTEGER);
        $einf->execute();
        $einf->reset();
    }
}
$db->exec('COMMIT');
$db->close();

// --- Rendern --------------------------------------------------------------
// Alles im Befehl ist entweder ein Literal, eine int-gecastete Zahl oder ein
// von tempnam() erzeugter Pfad - und zusaetzlich escapeshellarg-geschuetzt.
// Der Titel der App wandert NICHT in den Aufruf: der Renderer schreibt
// "gehört heute", und dabei bleibt es (Vertrag 10.4).
$cmd = escapeshellcmd(COLLAGE_PYTHON) . ' ' . escapeshellarg($RENDERER)
     . ' --db ' . escapeshellarg($tmp)
     . ' --illus ' . escapeshellarg($ILLUS)
     . ' --w ' . $w . ' --h ' . $h . ' --hours 24'
     . ($dark ? ' --dark' : '') . ' --out -';
$png = shell_exec($cmd . ' 2>/dev/null');

collage_aufraeumen($tmp);

if (!is_string($png) || strncmp($png, "\x89PNG", 4) !== 0) {
    collage_abbruch(500, 'kein bild');
}

header('Content-Type: image/png');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header('Content-Length: ' . strlen($png));
echo $png;
