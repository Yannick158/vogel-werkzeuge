<?php
// Illustration einer Art (ARCHITEKTUR.md 10.4) - fuer die Fundliste auf S9.
//
// Oeffentlich, weil das Handy-Mikrofon ohne Anmeldung funktioniert (S10) und
// die Zeichnungen nichts ueber den Garten verraten: Sie stammen aus einem
// oeffentlichen Repository (CC BY-NC-SA), es sind immer dieselben 1244
// Dateien, und welche davon jemand abruft, sagt nichts darueber, was gerade
// im Garten zu hoeren ist.
//
// Vertrag:
//   GET/HEAD ?sci=<Art>[&pose=1|2]  ->  image/png,
//                                       Cache-Control: public, max-age=86400
//   Unbekannte Art -> 404 (JSON). Ratenbremse 60/min je Client-IP.
//
// KEIN PFAD AUS DER ANFRAGE: Der Ordner ist ein Literal ueber APP_WURZEL, der
// Dateiname entsteht ausschliesslich aus dem geprueften sci ueber denselben
// slugify()-Nachbau wie in collage.php - danach enthaelt er nur noch
// [a-z0-9-]. basename() ist der zweite Riegel (SICHERHEIT.md B5/S-6).
declare(strict_types=1);

$__wurzel = getenv('APP_WURZEL');
require (is_string($__wurzel) && $__wurzel !== '' ? rtrim($__wurzel, '/') : '/srv')
      . '/avian/eigenes/app/_gemeinsam.php';

const ILLU_ORDNER = 'avian/BirdNET-Pi/avian/assets/illustrations';
const ILLU_TAKT   = 60;    // Aufrufe je Minute und IP

/** Abbruch als JSON; nie zwischenspeichern lassen. */
function illu_abbruch(int $code, string $grund): void {
    http_response_code($code);
    if (!headers_sent()) {
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
    }
    echo json_encode(['fehler' => $grund], JSON_UNESCAPED_UNICODE);
    exit;
}

/**
 * Nachbau von slugify() aus bild_rendern.py (Zeile 192) - wortgleich zu
 * collage_slug() in collage.php. Beide Dateien fuehren ihn selbst, weil sie
 * getrennt ausgerollt werden; der Testlauf vergleicht sie gegen den echten
 * Renderer.
 */
function illu_slug(string $sci): string {
    $s = strtolower(trim($sci));
    $teile = preg_split('/\s+/', $s, -1, PREG_SPLIT_NO_EMPTY);
    $s = is_array($teile) ? implode('-', $teile) : '';
    return (string)preg_replace('/[^a-z0-9-]/', '', $s);
}

// --- Methode --------------------------------------------------------------
$methode = (string)($_SERVER['REQUEST_METHOD'] ?? 'GET');
if ($methode !== 'GET' && $methode !== 'HEAD') {
    header('Allow: GET, HEAD');
    illu_abbruch(405, 'nur GET');
}

// --- Ratenbremse ----------------------------------------------------------
// Wie collage.php: Schluessel ist der Hash der Adresse, nie die Adresse.
$ip = (string)($_SERVER['REMOTE_ADDR'] ?? '');
if (!takt_pruefen(hash('sha256', 'illustration|' . $ip), 'illustration', ILLU_TAKT)) {
    header('Retry-After: 60');
    illu_abbruch(429, 'zu viele anfragen');
}

// --- Eingabe --------------------------------------------------------------
$sci = $_GET['sci'] ?? '';
if (!is_string($sci)) {
    illu_abbruch(400, 'sci');
}
$sci = trim($sci);
if (preg_match('/^[A-Za-z][A-Za-z .-]{2,60}$/D', $sci) !== 1) {
    illu_abbruch(400, 'sci');
}

// pose=2 ist die zweite Haltung (<slug>-2.png), alles andere die erste.
$pose_roh = $_GET['pose'] ?? '1';
if (!is_string($pose_roh) || ($pose_roh !== '1' && $pose_roh !== '2')) {
    illu_abbruch(400, 'pose');
}
$pose = (int)$pose_roh;

$slug = illu_slug($sci);
if ($slug === '') {
    illu_abbruch(400, 'sci');
}

$datei = basename($slug . ($pose === 2 ? '-2' : '') . '.png');
$pfad  = app_pfad(ILLU_ORDNER) . '/' . $datei;

if (!is_file($pfad) || !is_readable($pfad)) {
    illu_abbruch(404, 'unbekannte art');
}

// --- Ausliefern -----------------------------------------------------------
$groesse = (int)@filesize($pfad);
$mtime   = (int)@filemtime($pfad);
$etag    = '"' . substr(hash('sha256', $mtime . '-' . $groesse . '-' . $datei), 0, 24) . '"';

header('Content-Type: image/png');
header('Cache-Control: public, max-age=86400');
header('X-Content-Type-Options: nosniff');
header('ETag: ' . $etag);
header('Last-Modified: ' . gmdate('D, d M Y H:i:s', $mtime) . ' GMT');

$mit = trim((string)($_SERVER['HTTP_IF_NONE_MATCH'] ?? ''));
if ($mit !== '' && ($mit === $etag || $mit === 'W/' . $etag || $mit === '*')) {
    http_response_code(304);
    exit;
}

header('Content-Length: ' . $groesse);
if ($methode === 'HEAD') {
    exit;
}
readfile($pfad);
