<?php
// Liefert den aufgezeichneten Zustandsverlauf als JSON - Grundlage für das
// Online-Zeit-Diagramm in den Einstellungen.
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

$VERLAUF = '/srv/pi-daten/verlauf.csv';
$tage = max(1, min(90, (int)($_GET['tage'] ?? 7)));
$seit = time() - $tage * 24 * 3600;

$punkte = [];
$online = 0; $gesamt = 0;
if (is_readable($VERLAUF)) {
    $fh = fopen($VERLAUF, 'r');
    fgetcsv($fh, 0, ','); // Kopfzeile
    while (($r = fgetcsv($fh, 0, ',')) !== false) {
        if (count($r) < 4) continue;
        $e = (int)$r[0];
        if ($e < $seit) continue;
        $punkte[] = ['t' => $e, 'on' => (int)$r[1],
                     'laufzeit' => (int)$r[2], 'temp' => (int)$r[3]];
        $gesamt++;
        if ((int)$r[1] === 1) $online++;
    }
    fclose($fh);
}

// Aktuelle Laufzeit am Stück (aus der letzten Messung)
$laufzeit_jetzt = $punkte ? end($punkte)['laufzeit'] : 0;

echo json_encode([
    'tage'            => $tage,
    'punkte'          => $punkte,
    'online_anteil'   => $gesamt ? round($online / $gesamt * 100, 1) : null,
    'messungen'       => $gesamt,
    'laufzeit_jetzt_s'=> $laufzeit_jetzt,
], JSON_UNESCAPED_UNICODE);
