<?php
// Gemeinsame Bausteine der App-Schnittstelle (Begleit-App).
//
// EINE Stelle für alle Server-Pfade: Jeder /srv-Pfad wird über die Konstante
// APP_WURZEL zusammengesetzt. Standard ist '/srv' (der Live-Server); die
// Testumgebung setzt die Umgebungsvariable APP_WURZEL auf ein Abbild mit
// derselben Verzeichnisstruktur. Ohne diese Umlenkung müsste jede Datei ihre
// Pfade selbst kennen - genau das soll es nicht geben.
//
// Die Standort-Tabelle ist bewusst eine feste Liste aus Code-Literalen
// (SICHERHEIT.md B5/S-6): Der Host-Header entscheidet nur, WELCHE Zeile
// genommen wird, nie WIE ein Pfad aussieht.
declare(strict_types=1);

// --- Wurzel ---------------------------------------------------------------
if (!defined('APP_WURZEL')) {
    $__w = getenv('APP_WURZEL');
    define('APP_WURZEL', (is_string($__w) && $__w !== '') ? rtrim($__w, '/') : '/srv');
}

/** Setzt einen Serverpfad aus der Wurzel zusammen ('/srv/...' -> APP_WURZEL.'/...'). */
function app_pfad(string $unter_srv): string {
    return APP_WURZEL . '/' . ltrim($unter_srv, '/');
}

// --- Standort-Tabelle -----------------------------------------------------
// Alle Werte sind Literale. Neue Standorte kommen hier dazu (und in
// oeffentlich/app/standorte.json), sonst nirgends.
const APP_STANDORTE = [
    'zweitstandort' => [
        'id'         => 'zweitstandort',
        'name'       => 'Zweitstandort',
        'untertitel' => 'Beispielstadt',
        'host'       => 'zweitstandort.example.org',
        'sprache'    => 'en',
        'db'         => 'zweitstandort-daten/birds.db',
        'verstecken' => 'avian/verstecken/zweitstandort.json',
        'geraete'    => 'avian/app/geraete-zweitstandort.json',
        'folgen'     => null,   // in zweitstandort wird (noch) keine Wochenschau erzeugt
    ],
    'garten' => [
        'id'         => 'garten',
        'name'       => 'Garten',
        'untertitel' => 'Zweitstandort',
        'host'       => 'garten.example.org',
        'sprache'    => 'de',
        'db'         => 'pi-daten/birds.db',
        'verstecken' => 'avian/verstecken/garten.json',
        'geraete'    => 'avian/app/geraete-garten.json',
        'folgen'     => 'avian/eigenes/wochenschau/folgen.json',
    ],
];

// Feste Pfade außerhalb der Standort-Tabelle.
const APP_ALT_GERAETE = 'login/geraete.json';    // Bigme-Geräte (altes Format)
const APP_TAKT_DIR    = 'avian/app/takt';        // Ratenbremse je Token-Hash
const APP_MAX_GERAETE = 200;                     // je Standort

/**
 * Standort anhand des Host-Headers. Wie birdnet-api.php / push-anmelden.php:
 * enthält der Host 'zweitstandort', ist es zweitstandort, sonst der Garten.
 * Der zurückgegebene Datensatz enthält bereits absolute Pfade.
 */
function standort_aus_host(?string $host = null): array {
    $host = (string)($host ?? ($_SERVER['HTTP_HOST'] ?? ''));
    $id = (stripos($host, 'zweitstandort') !== false) ? 'zweitstandort' : 'garten';
    $s = APP_STANDORTE[$id];
    $s['db_pfad']         = app_pfad($s['db']);
    $s['verstecken_pfad'] = app_pfad($s['verstecken']);
    $s['geraete_pfad']    = app_pfad($s['geraete']);
    $s['folgen_pfad']     = $s['folgen'] === null ? null : app_pfad($s['folgen']);
    return $s;
}

/** Standort über seine id (für die Kommandozeile), sonst null. */
function standort_aus_id(string $id): ?array {
    if (!isset(APP_STANDORTE[$id])) return null;
    return standort_aus_host(APP_STANDORTE[$id]['host']);
}

// --- Token ----------------------------------------------------------------

/** Holt den Bearer-Wert aus dem Authorization-Header (leer, wenn keiner da ist). */
function bearer_aus_header(): string {
    $roh = (string)($_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '');
    if ($roh === '' && function_exists('apache_request_headers')) {
        $h = apache_request_headers();
        foreach ($h as $k => $v) {
            if (strcasecmp($k, 'Authorization') === 0) { $roh = (string)$v; break; }
        }
    }
    if (stripos($roh, 'Bearer ') === 0) return trim(substr($roh, 7));
    return '';
}

/** Bearer-Format nach SICHERHEIT.md S-11: genau 64 Hex-Zeichen, klein. */
function bearer_format_ok(string $t): bool {
    return preg_match('/^[0-9a-f]{64}$/D', $t) === 1;
}

/**
 * Prüft ein Klartext-Token gegen BEIDE Tokendateien.
 *
 * SICHERHEIT.md S-5/B4: Es wird immer alles gelesen und komplett durchlaufen -
 * kein früher return, kein "bei Treffer die zweite Datei sparen". Sonst
 * verriete die Antwortzeit, in welcher Datei ein Token steckt.
 *
 * $streng = true (Standard) verlangt das Bearer-Format aus S-11. Für den alten
 * Weg über X-Frame-Token/?token= (Bigme) muss es false sein: jene Token sind
 * vor dieser Regel entstanden und dürfen nicht plötzlich abgewiesen werden.
 *
 * Rückgabe bei Treffer:
 *   ['quelle'=>'app'|'alt', 'geraet_id'=>string|null, 'name'=>string,
 *    'token_hash'=>string, 'eintrag'=>array]
 * sonst null.
 */
function token_pruefen(string $bearer, array $standort, bool $streng = true): ?array {
    $treffer = null;
    if ($streng && !bearer_format_ok($bearer)) {
        // Trotzdem beide Dateien lesen: gleiche Arbeit, gleiche Laufzeit.
        $bearer = '';
    }
    $hash = $bearer === '' ? str_repeat('0', 64) : hash('sha256', $bearer);

    // 1) Neue App-Geräte: { "g_...": {name, token_hash, ...} }
    $app = json_lesen($standort['geraete_pfad'], []);
    foreach ($app as $gid => $g) {
        if (!is_array($g)) continue;
        $th = (string)($g['token_hash'] ?? '');
        if ($th !== '' && hash_equals($th, $hash) && $bearer !== '') {
            $treffer = ['quelle' => 'app', 'geraet_id' => (string)$gid,
                        'name' => (string)($g['name'] ?? ''), 'token_hash' => $th, 'eintrag' => $g];
        }
    }

    // 2) Alte Geräte (Bigme): [ {name, token_hash} ]
    $alt = json_lesen(app_pfad(APP_ALT_GERAETE), []);
    foreach ($alt as $g) {
        if (!is_array($g)) continue;
        $th = (string)($g['token_hash'] ?? '');
        if ($th !== '' && hash_equals($th, $hash) && $bearer !== '') {
            if ($treffer === null) {
                $treffer = ['quelle' => 'alt', 'geraet_id' => null,
                            'name' => (string)($g['name'] ?? ''), 'token_hash' => $th, 'eintrag' => $g];
            }
        }
    }
    return $treffer;
}

// --- Ratenbremse ----------------------------------------------------------

/**
 * Dateibasierte Bremse je Token-Hash und Bereich (SICHERHEIT.md B3/S-4).
 * Nicht je IP: hinter NAT teilen sich mehrere Geräte eine Adresse.
 * Gibt true zurück, wenn der Aufruf erlaubt ist.
 */
function takt_pruefen(string $token_hash, string $bereich, int $max_pro_min): bool {
    $dir = app_pfad(APP_TAKT_DIR);
    if (!is_dir($dir)) { @mkdir($dir, 0750, true); }
    // Alte Dateien (>2 min) aufräumen, damit das Verzeichnis nicht wächst.
    $jetzt = time();
    $eintraege = @scandir($dir);
    if (is_array($eintraege)) {
        foreach ($eintraege as $e) {
            if ($e === '.' || $e === '..') continue;
            $p = $dir . '/' . $e;
            $mt = @filemtime($p);
            if ($mt !== false && ($jetzt - $mt) > 120) @unlink($p);
        }
    }

    $datei = $dir . '/' . preg_replace('/[^a-z0-9]/', '', strtolower($bereich))
           . '_' . substr(hash('sha256', $token_hash . '|' . $bereich), 0, 32);
    $fh = @fopen($datei, 'c+');
    if ($fh === false) return true;   // Bremse darf nie den Dienst blockieren
    @flock($fh, LOCK_EX);
    $roh = (string)stream_get_contents($fh);
    $grenze = $jetzt - 60;
    $liste = [];
    foreach (explode("\n", $roh) as $z) {
        $z = trim($z);
        if ($z === '' || !ctype_digit($z)) continue;
        if ((int)$z >= $grenze) $liste[] = (int)$z;
    }
    $erlaubt = count($liste) < $max_pro_min;
    if ($erlaubt) $liste[] = $jetzt;
    ftruncate($fh, 0);
    rewind($fh);
    fwrite($fh, implode("\n", $liste) . "\n");
    @flock($fh, LOCK_UN);
    fclose($fh);
    @chmod($datei, 0640);
    return $erlaubt;
}

// --- Dateien --------------------------------------------------------------

/** Liest eine JSON-Datei; bei jedem Problem den Ersatzwert. */
function json_lesen(?string $datei, array $ersatz = []): array {
    if ($datei === null || !is_readable($datei)) return $ersatz;
    $j = json_decode((string)@file_get_contents($datei), true);
    return is_array($j) ? $j : $ersatz;
}

/**
 * Atomar schreiben (tmp + rename), wie push-anmelden.php (SICHERHEIT.md S-12).
 * Die temporäre Datei liegt im selben Verzeichnis, damit rename() nicht über
 * Dateisystemgrenzen geht.
 */
function atomar_schreiben(string $datei, string $inhalt, int $rechte = 0640): bool {
    $dir = dirname($datei);
    if (!is_dir($dir)) { @mkdir($dir, 0750, true); }
    $tmp = $datei . '.tmp';
    if (@file_put_contents($tmp, $inhalt, LOCK_EX) === false) return false;
    @chmod($tmp, $rechte);
    if (!@rename($tmp, $datei)) { @unlink($tmp); return false; }
    return true;
}

/** Geräte-Datei atomar mit 640 schreiben. */
function geraete_schreiben(string $datei, array $geraete): bool {
    return atomar_schreiben($datei,
        (string)json_encode($geraete, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
        0640);
}

// --- Antworten ------------------------------------------------------------

/** Einheitliche JSON-Antwort; beendet die Anfrage. */
function json_antwort(int $status, array $daten): void {
    http_response_code($status);
    if (!headers_sent()) header('Content-Type: application/json; charset=utf-8');
    echo json_encode($daten, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

/** ISO-8601 in Ortszeit Europe/Beispielstadt, mit Offset (z. B. 2026-09-09T18:30:00+02:00). */
function iso_zeit($zeit = null): string {
    $tz = new DateTimeZone('Europe/Beispielstadt');
    if ($zeit === null) {
        $d = new DateTimeImmutable('now', $tz);
    } elseif ($zeit instanceof DateTimeInterface) {
        $d = DateTimeImmutable::createFromInterface($zeit)->setTimezone($tz);
    } elseif (is_int($zeit)) {
        $d = (new DateTimeImmutable('@' . $zeit))->setTimezone($tz);
    } else {
        // 'YYYY-MM-DD HH:MM:SS' aus birds.db - Ortszeit ohne Zonenangabe.
        $d = DateTimeImmutable::createFromFormat('!Y-m-d H:i:s', (string)$zeit, $tz);
        if ($d === false) {
            $d = DateTimeImmutable::createFromFormat('!Y-m-d', (string)$zeit, $tz);
        }
        if ($d === false) return '';
    }
    return $d->format('c');
}

/**
 * Wandelt einen ISO-Zeitstempel des Clients in 'YYYY-MM-DD HH:MM:SS' Ortszeit
 * (so liegen Date/Time in birds.db). Null bei unlesbarer Eingabe.
 *
 * Hinweis für die App: Ein '+' im Offset muss in der Query als %2B kodiert
 * sein, sonst kommt es als Leerzeichen an. Deshalb wird ein Leerzeichen vor
 * einem 4-stelligen Offset hier wieder als '+' gelesen.
 */
function iso_zu_ortszeit(string $iso): ?string {
    $iso = trim($iso);
    if ($iso === '') return null;
    $iso = preg_replace('/ (\d{2}:?\d{2})$/', '+$1', $iso);
    try {
        $d = new DateTimeImmutable($iso);
    } catch (Throwable $e) {
        return null;
    }
    return $d->setTimezone(new DateTimeZone('Europe/Beispielstadt'))->format('Y-m-d H:i:s');
}

// --- Herkunft -------------------------------------------------------------

/**
 * Origin-/Referer-Prüfung wie push-anmelden.php: fehlt der Origin (oder ist er
 * 'null'), lassen wir durch - App-Anfragen haben keinen. Ein FREMDER Origin
 * wird abgewiesen (403, SICHERHEIT.md B6: 403 nur hier, nie bei Token-Fehlern).
 */
function herkunft_ok(): bool {
    $host = preg_replace('/:\d+$/', '', (string)($_SERVER['HTTP_HOST'] ?? ''));
    $origin = (string)($_SERVER['HTTP_ORIGIN'] ?? '');
    if ($origin === '' || strcasecmp($origin, 'null') === 0) return true;
    $oh = parse_url($origin, PHP_URL_HOST) ?: '';
    if ($oh === '') return true;
    return strcasecmp($oh, (string)$host) === 0;
}

// --- Kleinkram ------------------------------------------------------------

/** Gerätename bereinigen und auf 60 Zeichen kürzen (SICHERHEIT.md S-11). */
function name_bereinigen(string $roh): string {
    // Steuerzeichen raus, Zeilenumbrüche zu Leerzeichen, Mehrfach-Leerraum eins.
    $n = preg_replace('/[\x00-\x1F\x7F]+/u', ' ', $roh) ?? '';
    $n = trim(preg_replace('/\s+/u', ' ', $n) ?? '');
    if (mb_strlen($n, 'UTF-8') > 60) $n = mb_substr($n, 0, 60, 'UTF-8');
    return $n;
}

/** Neue Geräte-Kennung: 'g_' + 16 Hex-Zeichen. */
function geraet_id_neu(): string {
    return 'g_' . bin2hex(random_bytes(8));
}

/** Neues Geräte-Token: 64 Hex-Zeichen aus random_bytes(32). */
function token_neu(): string {
    return bin2hex(random_bytes(32));
}

/**
 * Ausblendliste eines Standorts, in derselben Form wie birdnet-api.php:
 * ['arten' => [sci => 0, ...], 'aufnahmen' => [file => 0, ...]] (array_flip,
 * damit isset() reicht).
 */
function verstecken_laden(array $standort): array {
    $h = ['arten' => [], 'aufnahmen' => []];
    $j = json_lesen($standort['verstecken_pfad'], []);
    $h['arten']     = array_flip(array_map('strval', $j['arten'] ?? []));
    $h['aufnahmen'] = array_flip(array_map('strval', $j['aufnahmen'] ?? []));
    return $h;
}

/**
 * SQL-Zusatz, der versteckte Aufnahmen ausschließt - wörtlich die Logik aus
 * birdnet-api.php ($FILE_EXCL). Dateinamen werden escaped, nicht gebunden,
 * weil ihre Anzahl variabel ist.
 */
function verstecken_sql(array $hide): string {
    if (empty($hide['aufnahmen'])) return '';
    $qs = array_map(static function ($f) {
        return "'" . SQLite3::escapeString((string)$f) . "'";
    }, array_keys($hide['aufnahmen']));
    return ' AND File_Name NOT IN (' . implode(',', $qs) . ')';
}
