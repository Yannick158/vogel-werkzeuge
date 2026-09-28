<?php
// Liefert das aktuelle 24-Stunden-Vogelbild als PNG - für Sperrbildschirm,
// Bildschirmschoner und die spätere App.
//
// ZUGANG ÜBER GERÄTE-TOKEN, nicht über das Familienpasswort: Ein Handy am
// Sperrbildschirm hat keine Tastatur. Jedes Gerät bekommt ein eigenes,
// einzeln widerrufbares Token (X-Frame-Token oder ?token=). Der Token-Weg
// ist bewusst auf genau dieses eine Bild beschränkt - er gibt nichts anderes
// frei, keine Artenliste, keine Zeiten.
declare(strict_types=1);

$GERAETE = '/srv/login/geraete.json';   // [{name, token_hash}]
$RENDERER = '/srv/avian/eigenes/bild_rendern.py';
$PYTHON = 'python3';

function token_gueltig(string $datei): bool {
    $roh = $_SERVER['HTTP_X_FRAME_TOKEN'] ?? ($_GET['token'] ?? '');
    if ($roh === '' || !is_readable($datei)) return false;
    $hash = hash('sha256', $roh);
    $liste = json_decode((string)file_get_contents($datei), true) ?: [];
    $ok = false;
    // Alle prüfen (zeitkonstant, ohne früh abzubrechen).
    foreach ($liste as $g) {
        if (hash_equals((string)($g['token_hash'] ?? ''), $hash)) $ok = true;
    }
    return $ok;
}

if (!token_gueltig($GERAETE)) {
    http_response_code(401);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Kein gültiges Geräte-Token.';
    exit;
}

// Maße begrenzen (gegen Missbrauch als Rechenlast)
$w = max(200, min(2000, (int)($_GET['w'] ?? 824)));
$h = max(200, min(2600, (int)($_GET['h'] ?? 1648)));
$hours = max(1, min(168, (int)($_GET['hours'] ?? 24)));
$dark = isset($_GET['dark']) ? '--dark' : '';

if (!is_readable($RENDERER)) {
    http_response_code(503);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Bild-Renderer noch nicht eingerichtet.';
    exit;
}

// Renderer aufrufen, PNG nach stdout
$cmd = escapeshellcmd($PYTHON) . ' ' . escapeshellarg($RENDERER)
     . ' --w ' . $w . ' --h ' . $h . ' --hours ' . $hours
     . ($dark ? ' --dark' : '') . ' --out -';
$png = shell_exec($cmd . ' 2>/dev/null');

if (!is_string($png) || strncmp($png, "\x89PNG", 4) !== 0) {
    http_response_code(500);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Bild konnte nicht erzeugt werden.';
    exit;
}

// Kurz zwischenspeichern lassen (das Bild ändert sich höchstens minütlich).
header('Content-Type: image/png');
header('Cache-Control: public, max-age=300');
header('Content-Length: ' . strlen($png));
echo $png;
