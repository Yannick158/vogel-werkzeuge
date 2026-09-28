<?php
// Exportiert die gehörten Vögel als Tabelle.
//
// Zwei Formate:
//   ?was=liste     eine Zeile je Erkennung (alles, chronologisch)
//   ?was=arten     eine Zeile je Art (Anzahl, zuerst/zuletzt gehört)  <- Voreinstellung
//
// Bewusst OHNE Koordinaten: Die Datei kann das Haus verorten, und ein Export
// wandert schnell mal per Mail weiter. Wer sie braucht, findet sie in birds.db
// auf dem Pi.
//
// Liegt hinter dem Zugangsschutz der Seite - wer die Anzeige sehen darf, darf
// auch exportieren.
declare(strict_types=1);

$DB = '/srv/pi-daten/birds.db';
if (!is_readable($DB)) {
    http_response_code(503);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Keine Daten vorhanden.';
    exit;
}

$was = $_GET['was'] ?? 'arten';
$db = new SQLite3($DB, SQLITE3_OPEN_READONLY);
$db->busyTimeout(2000);

header('Content-Type: text/csv; charset=utf-8');
$datum = date('Y-m-d');
while (ob_get_level()) ob_end_clean();
$out = fopen('php://output', 'w');
// Ein BOM voran, damit Excel die Umlaute richtig anzeigt.
fwrite($out, "\xEF\xBB\xBF");

if ($was === 'liste') {
    header("Content-Disposition: attachment; filename=\"voegel-liste-$datum.csv\"");
    fputcsv($out, ['Datum', 'Uhrzeit', 'Art', 'Wissenschaftlich', 'Sicherheit_Prozent'], ';');
    $res = $db->query("SELECT Date, Time, Com_Name, Sci_Name, round(Confidence*100) AS p
                       FROM detections ORDER BY Date DESC, Time DESC");
    while ($r = $res->fetchArray(SQLITE3_ASSOC)) {
        fputcsv($out, [$r['Date'], substr((string)$r['Time'], 0, 8),
                       $r['Com_Name'], $r['Sci_Name'], $r['p']], ';');
    }
} else {
    header("Content-Disposition: attachment; filename=\"voegel-arten-$datum.csv\"");
    fputcsv($out, ['Art', 'Wissenschaftlich', 'Anzahl', 'Zuerst_gehört',
                   'Zuletzt_gehört', 'Beste_Sicherheit_Prozent'], ';');
    $res = $db->query("
        SELECT Com_Name, Sci_Name, count(*) AS anzahl,
               min(Date || ' ' || substr(Time,1,5)) AS zuerst,
               max(Date || ' ' || substr(Time,1,5)) AS zuletzt,
               round(max(Confidence)*100) AS beste
        FROM detections GROUP BY Sci_Name ORDER BY anzahl DESC");
    while ($r = $res->fetchArray(SQLITE3_ASSOC)) {
        fputcsv($out, [$r['Com_Name'], $r['Sci_Name'], $r['anzahl'],
                       $r['zuerst'], $r['zuletzt'], $r['beste']], ';');
    }
}
$db->close();
exit;
