<?php
// Geräteverwaltung von der Kommandozeile - für den Fall, dass keine App zur
// Hand ist (verlorenes Handy, Erstausstattung, Runbook ARCHITEKTUR.md 8a).
//
//   sudo -u www-data php /srv/avian/eigenes/app/geraet-cli.php anlegen garten "iPhone von Operator"
//   sudo -u www-data php /srv/avian/eigenes/app/geraet-cli.php liste garten
//   sudo -u www-data php /srv/avian/eigenes/app/geraet-cli.php widerrufen garten g_ab12cd34ef567890
//
// Als www-data laufen lassen, sonst gehört die geschriebene Datei root.
declare(strict_types=1);

// Über das Web ist diese Datei nichts als ein 404. Sie liegt zwar im
// eigenes/-Baum, aber die Caddy-Allowlist kennt sie nicht - der Riegel hier
// ist der zweite Boden.
if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    echo "Nicht gefunden.\n";
    exit;
}

require __DIR__ . '/_gemeinsam.php';

$argv = $_SERVER['argv'] ?? [];
$befehl = (string)($argv[1] ?? '');
$ort_id = (string)($argv[2] ?? '');

function hilfe(int $code = 1): void {
    fwrite(STDERR,
        "Aufruf:\n"
      . "  geraet-cli.php anlegen    <standort> \"<name>\"\n"
      . "  geraet-cli.php liste      <standort>\n"
      . "  geraet-cli.php widerrufen <standort> <geraet_id>\n\n"
      . "Standorte: " . implode(', ', array_keys(APP_STANDORTE)) . "\n");
    exit($code);
}

if ($befehl === '' || $ort_id === '') hilfe();

$standort = standort_aus_id($ort_id);
if ($standort === null) {
    fwrite(STDERR, "Unbekannter Standort: {$ort_id}\n");
    hilfe(2);
}
$datei = $standort['geraete_pfad'];
$geraete = json_lesen($datei, []);

switch ($befehl) {

case 'anlegen': {
    $name = name_bereinigen((string)($argv[3] ?? ''));
    if ($name === '') { fwrite(STDERR, "Name fehlt.\n"); hilfe(2); }
    if (count($geraete) >= APP_MAX_GERAETE) {
        fwrite(STDERR, "Zu viele Geräte (" . APP_MAX_GERAETE . ") an diesem Standort.\n");
        exit(3);
    }
    $token = token_neu();
    $gid = geraet_id_neu();
    while (isset($geraete[$gid])) { $gid = geraet_id_neu(); }
    $jetzt = iso_zeit();
    $geraete[$gid] = [
        'name'        => $name,
        'plattform'   => 'cli',
        'token_hash'  => hash('sha256', $token),
        'modus'       => 'neu',
        'berichte'    => true,
        'push'        => null,
        'seit'        => $jetzt,
        'zuletzt'     => $jetzt,
        'app_version' => '',
    ];
    if (!geraete_schreiben($datei, $geraete)) {
        fwrite(STDERR, "Schreiben fehlgeschlagen: {$datei}\n");
        exit(4);
    }
    // Das Token gibt es genau einmal - danach steht nur noch der Hash da.
    echo "Standort:  {$standort['id']}\n";
    echo "Gerät:     {$name}\n";
    echo "geraet_id: {$gid}\n";
    echo "Token:     {$token}\n";
    echo "\nDas Token wird NICHT erneut angezeigt. Jetzt notieren.\n";
    exit(0);
}

case 'liste': {
    if (!$geraete) { echo "Keine Geräte für {$standort['id']}.\n"; exit(0); }
    printf("%-20s %-9s %-6s %-5s %-25s %s\n", 'geraet_id', 'plattform', 'modus', 'push', 'zuletzt', 'name');
    foreach ($geraete as $gid => $g) {
        if (!is_array($g)) continue;
        printf("%-20s %-9s %-6s %-5s %-25s %s\n",
            (string)$gid,
            (string)($g['plattform'] ?? '-'),
            (string)($g['modus'] ?? '-'),
            (is_array($g['push'] ?? null) && ($g['push']['token'] ?? '') !== '') ? 'ja' : 'nein',
            (string)($g['zuletzt'] ?? '-'),
            (string)($g['name'] ?? ''));
    }
    exit(0);
}

case 'widerrufen': {
    $gid = (string)($argv[3] ?? '');
    if ($gid === '') { fwrite(STDERR, "geraet_id fehlt.\n"); hilfe(2); }
    if (!isset($geraete[$gid])) {
        fwrite(STDERR, "Kein Gerät {$gid} an Standort {$standort['id']}.\n");
        exit(5);
    }
    $name = (string)($geraete[$gid]['name'] ?? '');
    unset($geraete[$gid]);
    if (!geraete_schreiben($datei, $geraete)) {
        fwrite(STDERR, "Schreiben fehlgeschlagen: {$datei}\n");
        exit(4);
    }
    echo "Widerrufen: {$gid} ({$name}) an Standort {$standort['id']}.\n";
    exit(0);
}

default:
    hilfe();
}
