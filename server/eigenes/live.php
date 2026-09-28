<?php
// Der Live-Modus, Serverseite.
//
// Diese Datei spricht NICHT mit dem Pi. Sie redet nur mit dem Briefkasten auf
// demselben Rechner (127.0.0.1:8092).
//
// WARUM: Bis zum 11.09.2026 griff sie durch den Rueckwaerts-Tunnel in den Pi
// hinein. Ein Aufruf im Browser konnte damit etwas auf einem Geraet im fremden
// Wohnzimmer ausloesen - und die Regel dieses Projekts lautet, dass ueber die
// Webseite niemand an den Pi kommt. Jetzt legt diese Datei nur einen Wunsch in
// den Briefkasten ("jemand will zuschauen"); der Pi sieht von sich aus nach und
// liefert dorthin. Der Pi nimmt nichts mehr entgegen.
//
// Sie steht hinter dem Anmeldetor (forward_auth in der Caddy-Konfiguration) und
// laesst genau drei Fragen durch:
//
//   ?was=stand                            welche Bloecke bereitliegen, was darin gefunden wurde
//   ?was=streifen&block=JJJJMMTT-HHMMSS    das Spektrogramm eines Blocks
//   ?was=aufnahme&datei=...                der Tonausschnitt EINER Erkennung
//
// WARUM DIE AUSBLENDLISTE HIER GREIFT und nicht auf dem Pi:
// Die Liste ist Servereigentum - "falsch markiert" wird im Browser gesetzt und
// hier gespeichert. Der Pi kennt sie nicht und soll sie nicht kennen. Also
// filtert der Server. Und zwar auch beim direkten Aufruf von ?was=aufnahme,
// sonst waere die Liste blosse Anzeigekosmetik statt einer echten Sperre: wer
// den Dateinamen erraet, bekaeme den Ton einer Art, die ausgeblendet ist.
//
// WAS HIER NIE PASSIERT: ein Pfad aus der Anfrage. Der Blockname muss dem
// Muster JJJJMMTT-HHMMSS genuegen, der Dateiname wird nur weitergereicht und
// vom Pi gegen seine eigene Datenbank geprueft. Diese Datei setzt keinen
// Dateipfad zusammen und liest nichts vom Dateisystem ausser der Ausblendliste.
declare(strict_types=1);

require_once __DIR__ . '/app/_gemeinsam.php';

// Die Umleitung gilt NUR ausserhalb des Webservers - also im Testlauf mit
// `php -S` oder auf der Kommandozeile. Im Betrieb (fpm-fcgi) wird sie gar
// nicht erst gelesen. Damit ist die Zieladresse dort eine Konstante und es
// gibt keinen Weg, diese Datei als Sprungbrett auf einen anderen Port zu
// benutzen - auch nicht mit gestohlenem Sitzungs-Cookie.
$__bk = (PHP_SAPI === 'fpm-fcgi' || PHP_SAPI === 'fpm') ? false : getenv('VOGEL_BRIEFKASTEN');
define('PI_BASIS', (is_string($__bk) && preg_match('#^http://127\\.0\\.0\\.1:\\d{2,5}$#', $__bk))
    ? $__bk : 'http://127.0.0.1:8092');
const PI_GEDULD  = 8;     // Sekunden; der Briefkasten antwortet aus dem Speicher
const BLOCK_FORM = '/^\d{8}-\d{6}$/';

/** Antwort mit einem kurzen Grund abbrechen. */
function schluss(int $status, string $grund): void {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode(['fehler' => $grund], JSON_UNESCAPED_UNICODE);
    exit;
}

/**
 * Eine Frage an den Briefkasten. Gibt [status, kopf, koerper] zurueck.
 *
 * Er liegt auf demselben Rechner und antwortet aus dem Arbeitsspeicher, ist
 * also schnell. Die knappe Geduld ist trotzdem richtig: laeuft er gerade nicht,
 * soll der Browser "gerade nicht erreichbar" sehen und kein ewiges Rad.
 */
function pi_fragen(string $pfad, string $methode = 'GET'): array {
    $ch = curl_init(PI_BASIS . $pfad);
    if ($ch === false) return [0, [], ''];
    $kopf = [];
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 3,
        CURLOPT_TIMEOUT        => PI_GEDULD,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CUSTOMREQUEST  => $methode,
        CURLOPT_HEADERFUNCTION => static function ($ch, $zeile) use (&$kopf) {
            $teile = explode(':', $zeile, 2);
            if (count($teile) === 2) {
                $kopf[strtolower(trim($teile[0]))] = trim($teile[1]);
            }
            return strlen($zeile);
        },
    ]);
    $koerper = curl_exec($ch);
    $status  = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    // Kein curl_close(): seit PHP 8.0 wirkungslos, seit 8.5 abgekuendigt - und
    // die Abkuendigungsmeldung landet mitten in der Antwort. Bei JSON zerlegt
    // das die Antwort, bei einem PNG waere die Datei kaputt. Der Griff wird
    // ohnehin aufgeraeumt, sobald die Variable aus dem Gueltigkeitsbereich faellt.
    if ($koerper === false) return [0, [], ''];
    return [$status, $kopf, (string)$koerper];
}

$standort = standort_aus_host();
$HIDE     = verstecken_laden($standort);
$was      = (string)($_GET['was'] ?? 'stand');

// ---------------------------------------------------------------------------
if ($was === 'stand') {
    // Erst den Wunsch hinterlegen: "jemand will zuschauen". Der Pi sieht
    // hoechstens drei Sekunden spaeter nach und faengt an zu liefern.
    pi_fragen('/wunsch', 'POST');

    [$status, , $koerper] = pi_fragen('/stand');
    if ($status !== 200) schluss(503, 'Die Ablage antwortet gerade nicht.');
    $daten = json_decode($koerper, true);
    if (!is_array($daten)) schluss(502, 'Unverstaendliche Antwort der Ablage.');
    if (!$daten) {
        // Noch nichts da: der Pi waermt sich gerade auf. Das ist kein Fehler,
        // die Seite zeigt dann "ich horche in den Garten".
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        echo json_encode(['hoert_zu' => false, 'bloecke' => [], 'funde' => [],
                          'jetzt' => time()], JSON_UNESCAPED_UNICODE);
        exit;
    }

    // Ausgeblendete Arten und einzeln ausgeblendete Aufnahmen fliegen raus.
    // Beides zaehlt: eine Art kann ganz weg sein, oder nur eine Fehlaufnahme.
    $funde = [];
    foreach (($daten['funde'] ?? []) as $f) {
        if (!is_array($f)) continue;
        if (isset($HIDE['arten'][(string)($f['wiss'] ?? '')])) continue;
        if (isset($HIDE['aufnahmen'][(string)($f['datei'] ?? '')])) continue;
        $funde[] = $f;
    }
    $daten['funde'] = $funde;

    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($daten, JSON_UNESCAPED_UNICODE);
    exit;
}

// ---------------------------------------------------------------------------
if ($was === 'streifen') {
    $block = (string)($_GET['block'] ?? '');
    if (!preg_match(BLOCK_FORM, $block)) schluss(400, 'Kein Blockname.');
    [$status, $kopf, $koerper] = pi_fragen('/streifen/' . rawurlencode($block));
    if ($status !== 200 || $koerper === '') schluss(404, 'Kein Streifen.');

    // Der Pi liefert WebP, faellt aber auf PNG zurueck, wenn seinem ffmpeg
    // libwebp fehlt. Deshalb den gemeldeten Typ uebernehmen - aber nur aus
    // dieser kurzen Liste, damit von dort nichts anderes durchkaeme.
    $typ = (string)($kopf['content-type'] ?? 'image/webp');
    if (!in_array($typ, ['image/webp', 'image/png'], true)) $typ = 'image/webp';
    header('Content-Type: ' . $typ);
    header('Content-Length: ' . strlen($koerper));
    // Ein fertiger Streifen aendert sich nie mehr. Zehn Minuten reichen -
    // laenger braucht ihn niemand, der Ring ist nach gut zwei Minuten leer.
    header('Cache-Control: private, max-age=600');
    header('X-Content-Type-Options: nosniff');
    echo $koerper;
    exit;
}

// ---------------------------------------------------------------------------
if ($was === 'aufnahme') {
    $datei = (string)($_GET['datei'] ?? '');
    if ($datei === '' || strlen($datei) > 120
        || strpos($datei, '/') !== false || strpos($datei, "\0") !== false
        || strpos($datei, '..') !== false) {
        schluss(400, 'Kein Dateiname.');
    }
    if (isset($HIDE['aufnahmen'][$datei])) schluss(404, 'Keine Aufnahme.');

    // Zu welcher Art gehoert dieser Ausschnitt? Frueher schickte der Pi das im
    // Kopf mit; jetzt steht es im Stand, den er ohnehin abgelegt hat. Das ist
    // zugleich strenger: Ausgeliefert wird nur, was gerade wirklich im
    // Live-Fenster steht - ein geratener Dateiname fuehrt ins Leere.
    [$s2, , $k2] = pi_fragen('/stand');
    $stand = ($s2 === 200) ? json_decode($k2, true) : null;
    $art = null;
    foreach ((is_array($stand) ? ($stand['funde'] ?? []) : []) as $f) {
        if (is_array($f) && (string)($f['datei'] ?? '') === $datei) {
            $art = (string)($f['wiss'] ?? '');
            break;
        }
    }
    if ($art === null) schluss(404, 'Keine Aufnahme.');
    if ($art !== '' && isset($HIDE['arten'][$art])) schluss(404, 'Keine Aufnahme.');

    [$status, $kopf, $koerper] = pi_fragen('/aufnahme/' . rawurlencode($datei));
    if ($status !== 200 || $koerper === '') schluss(404, 'Keine Aufnahme.');

    $typ = (string)($kopf['content-type'] ?? 'audio/mpeg');
    if (!in_array($typ, ['audio/mpeg', 'audio/wav'], true)) $typ = 'application/octet-stream';
    header('Content-Type: ' . $typ);
    header('Content-Length: ' . strlen($koerper));
    header('Cache-Control: private, max-age=600');
    header('X-Content-Type-Options: nosniff');
    echo $koerper;
    exit;
}

schluss(400, 'Unbekannte Frage.');
