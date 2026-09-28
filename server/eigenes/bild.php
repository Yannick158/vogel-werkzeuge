<?php
// Liefert das aktuelle 24-Stunden-Vogelbild als PNG - für Sperrbildschirm,
// Bildschirmschoner und die App.
//
// ZUGANG ÜBER GERÄTE-TOKEN, nicht über das Familienpasswort: Ein Handy am
// Sperrbildschirm hat keine Tastatur. Jedes Gerät bekommt ein eigenes,
// einzeln widerrufbares Token (X-Frame-Token oder ?token=). Der Token-Weg
// ist bewusst auf genau dieses eine Bild beschränkt - er gibt nichts anderes
// frei, keine Artenliste, keine Zeiten.
//
// NEU für die App (ARCHITEKTUR.md 3.5):
//   - zusätzlich 'Authorization: Bearer <64 Hex>' aus geraete-<standort>.json;
//     beide Tokendateien werden immer vollständig durchsucht (S-5).
//   - die Datenbank kommt aus der festen Standort-Tabelle in _gemeinsam.php
//     und wandert als '--db' in den Renderer-Aufruf - ein Code-Literal, nie
//     aus dem Host-String zusammengebaut (S-6).
//   - Ratenbremse: höchstens 12 Aufrufe/Minute je Token-Hash, sonst 429 (S-4).
//     Das Rendern kostet ~0,45 s CPU.
declare(strict_types=1);

// Gemeinsame Bausteine. Der Live-Pfad ist /srv/avian/eigenes/app/ - die
// Wurzel '/srv' lässt sich über die Umgebungsvariable APP_WURZEL umlenken,
// damit die Tests gegen ein Abbild laufen können.
$__wurzel = getenv('APP_WURZEL');
require (is_string($__wurzel) && $__wurzel !== '' ? rtrim($__wurzel, '/') : '/srv')
      . '/avian/eigenes/app/_gemeinsam.php';

$RENDERER = app_pfad('avian/eigenes/bild_rendern.py');
$PYTHON = 'python3';

$standort = standort_aus_host();

function fehler(int $code, string $text): void {
    http_response_code($code);
    header('Content-Type: text/plain; charset=utf-8');
    echo $text;
    exit;
}

// --- Token prüfen ---------------------------------------------------------
// Drei Wege: Bearer (App, strenges Format) sowie X-Frame-Token / ?token=
// (Bigme, historisch gewachsen - dort kein Formatzwang, sonst sperrten wir
// bestehende Geräte aus).
$bearer = bearer_aus_header();
$treffer = null;
if ($bearer !== '') {
    $treffer = token_pruefen($bearer, $standort, true);
}
if ($treffer === null) {
    $alt_roh = (string)($_SERVER['HTTP_X_FRAME_TOKEN'] ?? ($_GET['token'] ?? ''));
    if ($alt_roh !== '') {
        $treffer = token_pruefen($alt_roh, $standort, false);
    }
}
if ($treffer === null) {
    fehler(401, 'Kein gültiges Geräte-Token.');
}

// --- Ratenbremse ----------------------------------------------------------
if (!takt_pruefen($treffer['token_hash'], 'bild', 12)) {
    http_response_code(429);
    header('Retry-After: 60');
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Zu viele Anfragen.';
    exit;
}

// Maße begrenzen (gegen Missbrauch als Rechenlast)
$w = max(200, min(2000, (int)($_GET['w'] ?? 824)));
$h = max(200, min(2600, (int)($_GET['h'] ?? 1648)));
$hours = max(1, min(168, (int)($_GET['hours'] ?? 24)));
$dark = isset($_GET['dark']) ? '--dark' : '';

if (!is_readable($RENDERER)) {
    fehler(503, 'Bild-Renderer noch nicht eingerichtet.');
}

// Renderer aufrufen, PNG nach stdout.
// $w/$h/$hours sind int-gecastet, der DB-Pfad ist ein Literal aus der
// Standort-Tabelle und wird zusätzlich escapeshellarg-geschützt.
$cmd = escapeshellcmd($PYTHON) . ' ' . escapeshellarg($RENDERER)
     . ' --db ' . escapeshellarg($standort['db_pfad'])
     . ' --w ' . $w . ' --h ' . $h . ' --hours ' . $hours
     . ($dark ? ' --dark' : '') . ' --out -';
$png = shell_exec($cmd . ' 2>/dev/null');

if (!is_string($png) || strncmp($png, "\x89PNG", 4) !== 0) {
    fehler(500, 'Bild konnte nicht erzeugt werden.');
}

// Kurz zwischenspeichern lassen (das Bild ändert sich höchstens minütlich).
// Header bewusst unverändert gegenüber dem Ist-Stand (ARCHITEKTUR.md 3.5:
// "Bleibt wie es ist") - eine Umstellung auf 'private' wäre sauberer, ändert
// aber das Verhalten für die bestehenden Bigme-Geräte und gehört deshalb in
// eine eigene Entscheidung der Projektleitung.
header('Content-Type: image/png');
header('Cache-Control: public, max-age=300');
header('Content-Length: ' . strlen($png));
echo $png;
