<?php
// Kurzstand für Widget und Abruf-Rückfall (ARCHITEKTUR.md 3.4).
//
// Liegt AUSSERHALB des Anmeldetors (eigene Caddy-Ausnahme wie bild.php) und
// prüft deshalb selbst: Authorization: Bearer <64 Hex> gegen die App-Geräte
// des Standorts UND gegen die alten Bigme-Geräte in /srv/login/geraete.json.
// Beide Dateien werden immer vollständig durchsucht (SICHERHEIT.md S-5).
//
// Der Token-Weg gibt bewusst nur diesen Kurzstand frei - keine Aufnahmen,
// keine Menüs, keine Einstellungen.
//
// Parameter:
//   stunden = 1…168 (Standard 24)
//   seit    = ISO-8601. ACHTUNG: das '+' im Zonen-Offset muss der Client als
//             %2B senden, sonst kommt es als Leerzeichen an. Ein Leerzeichen
//             vor dem Offset wird hier trotzdem wieder als '+' gelesen.
declare(strict_types=1);

require __DIR__ . '/_gemeinsam.php';

$standort = standort_aus_host();

// --- Tor: Geräte-Token ----------------------------------------------------
$bearer = bearer_aus_header();
if ($bearer === '') {
    // Bequemlichkeit für Widgets ohne Header-Möglichkeit gibt es hier NICHT -
    // anders als bei bild.php bleibt es beim Header.
    $treffer = null;
} else {
    $treffer = token_pruefen($bearer, $standort);
}
if ($treffer === null) {
    // SICHERHEIT.md S-2: 401, damit fail2ban mitzählt.
    json_antwort(401, ['ok' => false, 'grund' => 'token']);
}

// --- Ratenbremse (SICHERHEIT.md S-4: 20/min) ------------------------------
if (!takt_pruefen($treffer['token_hash'], 'stand', 20)) {
    header('Retry-After: 60');
    json_antwort(429, ['ok' => false, 'grund' => 'zu_haeufig']);
}

// --- Parameter ------------------------------------------------------------
$stunden = max(1, min(168, (int)($_GET['stunden'] ?? 24)));
$seit_roh = (string)($_GET['seit'] ?? '');
$seit_lokal = $seit_roh !== '' ? iso_zu_ortszeit($seit_roh) : null;

// --- Datenbank (nur lesen) ------------------------------------------------
if (!is_file($standort['db_pfad'])) {
    json_antwort(503, ['ok' => false, 'grund' => 'keine_datenbank']);
}
try {
    // Nur-Lese-Verbindung (ARCHITEKTUR.md 3.4 'mode=ro'). SQLite3 versteht
    // keine file:-URIs ohne zusaetzliche Schalter; SQLITE3_OPEN_READONLY tut
    // dasselbe und ist der Weg, den auch birdnet-api.php geht.
    $db = new SQLite3($standort['db_pfad'], SQLITE3_OPEN_READONLY);
    $db->busyTimeout(2000);
} catch (Throwable $e) {
    json_antwort(503, ['ok' => false, 'grund' => 'datenbank']);
}

/** Kleine Abfragehilfe (wie birdnet-api.php). */
function zeilen(SQLite3 $db, string $sql, array $bind = []): array {
    $stmt = $db->prepare($sql);
    if ($stmt === false) return [];
    foreach ($bind as $k => $v) $stmt->bindValue($k, $v);
    $res = $stmt->execute();
    $out = [];
    while ($r = $res->fetchArray(SQLITE3_ASSOC)) $out[] = $r;
    return $out;
}
function eine(SQLite3 $db, string $sql, array $bind = []) {
    $r = zeilen($db, $sql, $bind);
    return $r[0] ?? null;
}

// Ausblendliste - dieselbe Logik wie birdnet-api.php.
$HIDE = verstecken_laden($standort);
$FILE_EXCL = verstecken_sql($HIDE);

/** Versteckte Arten aus einer Ergebnisliste werfen (birdnet-api.php). */
function ohne_versteckte(array $rows, array $HIDE): array {
    if (empty($HIDE['arten'])) return $rows;
    return array_values(array_filter($rows, static function ($r) use ($HIDE) {
        return !isset($HIDE['arten'][(string)($r['sci'] ?? '')]);
    }));
}

// Anker = jetzt in Ortszeit Europe/Beispielstadt; genau so liegen Date/Time in der
// birds.db (Ortszeit ohne Zonenangabe).
$jetzt_dt = new DateTimeImmutable('now', new DateTimeZone('Europe/Beispielstadt'));
$anker = $jetzt_dt->format('Y-m-d H:i:s');
$fenster_ab = $jetzt_dt->modify('-' . $stunden . ' hours')->format('Y-m-d H:i:s');

$WO = "DATETIME(Date||' '||Time) > DATETIME(:ab) AND DATETIME(Date||' '||Time) <= DATETIME(:anker)";
$bind = [':ab' => $fenster_ab, ':anker' => $anker];

// --- Arten im Zeitfenster -------------------------------------------------
$rs = zeilen($db,
      "SELECT Sci_Name AS sci, Com_Name AS name, COUNT(*) AS anzahl, "
    . "       MAX(Date||' '||Time) AS zuletzt "
    . "FROM detections WHERE $WO" . $FILE_EXCL . " "
    . "GROUP BY Sci_Name ORDER BY anzahl DESC, zuletzt DESC",
    $bind);
$rs = ohne_versteckte($rs, $HIDE);

$anzahl_erkennungen = 0;
foreach ($rs as $r) $anzahl_erkennungen += (int)$r['anzahl'];
$anzahl_arten = count($rs);

$arten = [];
foreach (array_slice($rs, 0, 40) as $r) {     // höchstens 40 (ARCHITEKTUR.md 3.4)
    $arten[] = [
        'sci'     => (string)$r['sci'],
        'name'    => (string)$r['name'],
        'anzahl'  => (int)$r['anzahl'],
        'zuletzt' => iso_zeit((string)$r['zuletzt']),
    ];
}

// Letzte Erkennung überhaupt (nicht nur im Fenster), ohne Verstecktes.
$letzte = null;
$sichtbar_letzte = zeilen($db,
      "SELECT Sci_Name AS sci, MAX(Date||' '||Time) AS ts FROM detections "
    . "WHERE DATETIME(Date||' '||Time) <= DATETIME(:anker)" . $FILE_EXCL . " "
    . "GROUP BY Sci_Name ORDER BY ts DESC LIMIT 60",
    [':anker' => $anker]);
$sichtbar_letzte = ohne_versteckte($sichtbar_letzte, $HIDE);
foreach ($sichtbar_letzte as $z) {
    $ts = (string)$z['ts'];
    if ($letzte === null || $ts > $letzte) $letzte = $ts;
}

// --- Neue Arten: Erstnachweis überhaupt in den letzten 7 Tagen ------------
$vor7 = $jetzt_dt->modify('-7 days')->format('Y-m-d H:i:s');
$erst = zeilen($db,
      "SELECT Sci_Name AS sci, Com_Name AS name, MIN(Date||' '||Time) AS erstmals "
    . "FROM detections WHERE DATETIME(Date||' '||Time) <= DATETIME(:anker)" . $FILE_EXCL . " "
    . "GROUP BY Sci_Name HAVING erstmals > :vor ORDER BY erstmals DESC",
    [':anker' => $anker, ':vor' => $vor7]);
$erst = ohne_versteckte($erst, $HIDE);
$neue_arten = [];
foreach ($erst as $r) {
    $neue_arten[] = [
        'sci'      => (string)$r['sci'],
        'name'     => (string)$r['name'],
        'erstmals' => iso_zeit((string)$r['erstmals']),
    ];
}

// --- Folgen: neueste Woche + neuester Monat -------------------------------
/** Einen Eintrag aus folgen.json auf die Vertragsfelder eindampfen. */
function folge_form(array $e): array {
    return [
        'kw'        => (string)($e['kw'] ?? ''),
        'typ'       => (string)($e['typ'] ?? 'woche'),   // fehlt bei alten Einträgen = Woche
        'datum'     => (string)($e['datum'] ?? ''),
        'titel'     => (string)($e['titel'] ?? ''),
        'kernsatz'  => (string)($e['kernsatz'] ?? ''),
        'datei'     => (string)($e['datei'] ?? ''),
        'sekunden'  => isset($e['sekunden']) && $e['sekunden'] !== null ? (int)$e['sekunden'] : null,
    ];
}
$folgen = [];
$folgen_alle = [];
if ($standort['folgen_pfad'] !== null) {
    $roh_folgen = json_lesen($standort['folgen_pfad'], []);
    $neueste = ['woche' => null, 'monat' => null];
    foreach ($roh_folgen as $e) {
        if (!is_array($e) || ($e['datei'] ?? '') === '') continue;
        $f = folge_form($e);
        $typ = $f['typ'] === 'monat' ? 'monat' : 'woche';
        $folgen_alle[] = $f;
        if ($neueste[$typ] === null || $f['datum'] > $neueste[$typ]['datum']) $neueste[$typ] = $f;
    }
    foreach (['woche', 'monat'] as $t) {
        if ($neueste[$t] !== null) $folgen[] = $neueste[$t];
    }
    usort($folgen, static fn($a, $b) => strcmp((string)$b['datum'], (string)$a['datum']));
}

// --- Antwort zusammenbauen ------------------------------------------------
$antwort = [
    'ok'                 => true,
    'standort'           => $standort['id'],
    'name'               => $standort['name'],
    'sprache'            => $standort['sprache'],
    'jetzt'              => iso_zeit($jetzt_dt),
    'letzte_erkennung'   => $letzte === null ? null : iso_zeit($letzte),
    'stunden'            => $stunden,
    'anzahl_erkennungen' => $anzahl_erkennungen,
    'anzahl_arten'       => $anzahl_arten,
    'arten'              => $arten,
    'neue_arten'         => $neue_arten,
    'folgen'             => $folgen,
];

// --- neu_seit (Abruf-Rückfall) -------------------------------------------
if ($seit_lokal !== null) {
    $sb = [':seit' => $seit_lokal, ':anker' => $anker];
    $SW = "DATETIME(Date||' '||Time) > DATETIME(:seit) AND DATETIME(Date||' '||Time) <= DATETIME(:anker)";

    $rs2 = zeilen($db,
          "SELECT Sci_Name AS sci, Com_Name AS name, COUNT(*) AS anzahl, "
        . "       MAX(Date||' '||Time) AS zuletzt "
        . "FROM detections WHERE $SW" . $FILE_EXCL . " GROUP BY Sci_Name",
        $sb);
    $rs2 = ohne_versteckte($rs2, $HIDE);
    $n_erk = 0;
    foreach ($rs2 as $r) $n_erk += (int)$r['anzahl'];

    // Erstnachweis überhaupt nach 'seit' -> das sind die neuen Arten.
    $erst2 = zeilen($db,
          "SELECT Sci_Name AS sci, Com_Name AS name, MIN(Date||' '||Time) AS erstmals "
        . "FROM detections WHERE DATETIME(Date||' '||Time) <= DATETIME(:anker)" . $FILE_EXCL . " "
        . "GROUP BY Sci_Name HAVING erstmals > :seit",
        $sb);
    $erst2 = ohne_versteckte($erst2, $HIDE);
    $erst_map = [];
    foreach ($erst2 as $r) $erst_map[(string)$r['sci']] = (string)$r['erstmals'];

    $neu_arten = [];
    foreach ($rs2 as $r) {
        $sci = (string)$r['sci'];
        if (!isset($erst_map[$sci])) continue;
        $neu_arten[] = [
            'sci'      => $sci,
            'name'     => (string)$r['name'],
            'anzahl'   => (int)$r['anzahl'],
            'zuletzt'  => iso_zeit((string)$r['zuletzt']),
            'erstmals' => iso_zeit($erst_map[$sci]),
        ];
    }
    usort($neu_arten, static fn($a, $b) => $b['anzahl'] <=> $a['anzahl']);

    // Folgen mit datum >= dem Tag von 'seit'.
    $seit_tag = substr($seit_lokal, 0, 10);
    $neu_folgen = array_values(array_filter($folgen_alle,
        static fn($f) => (string)$f['datum'] >= $seit_tag));
    usort($neu_folgen, static fn($a, $b) => strcmp((string)$b['datum'], (string)$a['datum']));

    $antwort['neu_seit'] = [
        'seit'        => iso_zeit($seit_lokal),
        'erkennungen' => $n_erk,
        'arten'       => $neu_arten,
        'folgen'      => $neu_folgen,
    ];
}

// Personenbezogen: nie in einen gemeinsamen Zwischenspeicher.
header('Cache-Control: private, max-age=60');
json_antwort(200, $antwort);
