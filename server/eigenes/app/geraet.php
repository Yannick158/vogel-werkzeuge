<?php
// Geräteverwaltung der App (ARCHITEKTUR.md 3.3).
//
// Liegt HINTER dem Anmeldetor: Caddy prüft das Sitzungs-Cookie per
// forward_auth, bevor diese Datei überhaupt läuft. Ein Aufruf ohne Cookie
// kommt hier nie an (er endet als 302 auf /login).
//
// Aktionen: anmelden | aktualisieren | abmelden | liste | widerrufen
//
// Statuscodes nach SICHERHEIT.md B6/S-2:
//   401 = alles rund um den Bearer (fehlt, falsch, gehört zu einem anderen
//         Gerät) - fail2ban zählt auf 401 mit.
//   403 = ausschließlich Herkunftsverstoß (fremder Origin).
//   400 = kaputte Anfrage, 429 = zu viele Geräte.
declare(strict_types=1);

require __DIR__ . '/_gemeinsam.php';

header('Cache-Control: no-store');

$standort = standort_aus_host();

// Herkunft zuerst: ein fremder Origin darf gar nichts auslösen.
if (!herkunft_ok()) {
    json_antwort(403, ['ok' => false, 'grund' => 'herkunft']);
}

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') !== 'POST') {
    json_antwort(400, ['ok' => false, 'grund' => 'methode']);
}

$roh = json_decode((string)file_get_contents('php://input'), true);
if (!is_array($roh)) {
    json_antwort(400, ['ok' => false, 'grund' => 'json']);
}

$aktion = (string)($roh['aktion'] ?? '');
$datei  = $standort['geraete_pfad'];
$geraete = json_lesen($datei, []);

// ---------------------------------------------------------------------------
// Hilfen
// ---------------------------------------------------------------------------

/** Modus prüfen; ungültig -> null. */
function modus_pruefen($m): ?string {
    $m = (string)$m;
    return in_array($m, ['aus', 'neu', 'alle'], true) ? $m : null;
}

/**
 * Push-Angabe prüfen. Rückgabe: [ok, wert] - wert ist ein Array oder null.
 * Fehlt der Schlüssel ganz, meldet der Aufrufer "unverändert".
 */
function push_pruefen($p): array {
    if ($p === null) return [true, null];
    if (!is_array($p)) return [false, null];
    $dienst = (string)($p['dienst'] ?? '');
    $token  = (string)($p['token'] ?? '');
    if (!in_array($dienst, ['apns', 'fcm'], true)) return [false, null];
    // SICHERHEIT.md S-11: Push-Token höchstens 4096 Zeichen.
    if ($token === '' || strlen($token) > 4096) return [false, null];
    if (preg_match('/[\x00-\x1F\x7F]/', $token)) return [false, null];
    return [true, ['dienst' => $dienst, 'token' => $token]];
}

/** Geräte-Kennung aus der Anfrage; ungültiges Format -> ''. */
function geraet_id_aus($v): string {
    $g = (string)$v;
    return preg_match('/^g_[0-9a-f]{16}$/D', $g) === 1 ? $g : '';
}

/**
 * Bearer prüfen und dem angegebenen Gerät zuordnen.
 * Läuft IMMER über alle Einträge (kein früher Abbruch, SICHERHEIT.md S-5).
 * Rückgabe: true, wenn Token und geraet_id zusammengehören.
 */
function bearer_passt_zu(string $bearer, array $geraete, string $geraet_id): bool {
    if (!bearer_format_ok($bearer) || $geraet_id === '') return false;
    $hash = hash('sha256', $bearer);
    $ok = false;
    foreach ($geraete as $gid => $g) {
        if (!is_array($g)) continue;
        $th = (string)($g['token_hash'] ?? '');
        if ($th === '') continue;
        // Beides muss stimmen; die Schleife läuft trotzdem zu Ende.
        if (hash_equals($th, $hash) && hash_equals((string)$gid, $geraet_id)) $ok = true;
    }
    return $ok;
}

// ---------------------------------------------------------------------------
// Aktionen
// ---------------------------------------------------------------------------

switch ($aktion) {

case 'anmelden': {
    $plattform = (string)($roh['plattform'] ?? '');
    if (!in_array($plattform, ['ios', 'android'], true)) {
        json_antwort(400, ['ok' => false, 'grund' => 'plattform']);
    }
    $name = name_bereinigen((string)($roh['name'] ?? ''));
    if ($name === '') $name = ($plattform === 'ios' ? 'iPhone' : 'Android-Gerät');

    $modus = modus_pruefen($roh['modus'] ?? 'neu');
    if ($modus === null) json_antwort(400, ['ok' => false, 'grund' => 'modus']);

    [$push_ok, $push] = push_pruefen($roh['push'] ?? null);
    if (!$push_ok) json_antwort(400, ['ok' => false, 'grund' => 'push']);

    // Schutz gegen Aufblähen (ARCHITEKTUR.md 3.3): höchstens 200 je Standort.
    if (count($geraete) >= APP_MAX_GERAETE) {
        header('Retry-After: 3600');
        json_antwort(429, ['ok' => false, 'grund' => 'zu_viele_geraete']);
    }

    $token = token_neu();
    $gid = geraet_id_neu();
    while (isset($geraete[$gid])) { $gid = geraet_id_neu(); }

    $jetzt = iso_zeit();
    $geraete[$gid] = [
        'name'        => $name,
        'plattform'   => $plattform,
        'token_hash'  => hash('sha256', $token),
        'modus'       => $modus,
        'berichte'    => (bool)($roh['berichte'] ?? true),
        'push'        => $push,
        'seit'        => $jetzt,
        'zuletzt'     => $jetzt,
        'app_version' => name_bereinigen((string)($roh['app_version'] ?? '')),
    ];
    if (!geraete_schreiben($datei, $geraete)) {
        json_antwort(500, ['ok' => false, 'grund' => 'schreiben']);
    }
    // Das Token wird genau hier ein einziges Mal im Klartext herausgegeben.
    json_antwort(200, ['ok' => true, 'geraet_id' => $gid, 'token' => $token]);
}

case 'aktualisieren': {
    $gid = geraet_id_aus($roh['geraet_id'] ?? '');
    $bearer = bearer_aus_header();
    if (!bearer_passt_zu($bearer, $geraete, $gid)) {
        json_antwort(401, ['ok' => false, 'grund' => 'token']);
    }
    $g = $geraete[$gid];

    if (array_key_exists('modus', $roh)) {
        $m = modus_pruefen($roh['modus']);
        if ($m === null) json_antwort(400, ['ok' => false, 'grund' => 'modus']);
        $g['modus'] = $m;
    }
    if (array_key_exists('berichte', $roh)) {
        $g['berichte'] = (bool)$roh['berichte'];
    }
    if (array_key_exists('name', $roh)) {
        $n = name_bereinigen((string)$roh['name']);
        if ($n !== '') $g['name'] = $n;
    }
    if (array_key_exists('push', $roh)) {
        [$push_ok, $push] = push_pruefen($roh['push']);
        if (!$push_ok) json_antwort(400, ['ok' => false, 'grund' => 'push']);
        $g['push'] = $push;   // null hebt den Push-Versand auf
    }
    if (array_key_exists('app_version', $roh)) {
        $g['app_version'] = name_bereinigen((string)$roh['app_version']);
    }
    $g['zuletzt'] = iso_zeit();
    $geraete[$gid] = $g;
    if (!geraete_schreiben($datei, $geraete)) {
        json_antwort(500, ['ok' => false, 'grund' => 'schreiben']);
    }
    json_antwort(200, ['ok' => true]);
}

case 'abmelden': {
    $gid = geraet_id_aus($roh['geraet_id'] ?? '');
    $bearer = bearer_aus_header();
    if (!bearer_passt_zu($bearer, $geraete, $gid)) {
        json_antwort(401, ['ok' => false, 'grund' => 'token']);
    }
    unset($geraete[$gid]);
    if (!geraete_schreiben($datei, $geraete)) {
        json_antwort(500, ['ok' => false, 'grund' => 'schreiben']);
    }
    json_antwort(200, ['ok' => true]);
}

case 'liste': {
    // Nur Cookie (Caddy hat es schon geprüft), kein Bearer nötig.
    // SICHERHEIT.md S-3: niemals token_hash oder Push-Token herausgeben.
    // Kennt der Aufrufer sein eigenes Token, markieren wir seinen Eintrag.
    $bearer = bearer_aus_header();
    $eigen = '';
    if (bearer_format_ok($bearer)) {
        $hash = hash('sha256', $bearer);
        foreach ($geraete as $gid => $g) {
            if (is_array($g) && hash_equals((string)($g['token_hash'] ?? ''), $hash)) $eigen = (string)$gid;
        }
    }
    // Wer sich per Bearer zu erkennen gibt, wird als aktiv vermerkt - so ist
    // 'zuletzt' in der Geräteliste eine echte Angabe und nicht nur der
    // Zeitpunkt der letzten Einstellungsänderung.
    if ($eigen !== '' && is_array($geraete[$eigen] ?? null)) {
        $geraete[$eigen]['zuletzt'] = iso_zeit();
        geraete_schreiben($datei, $geraete);
    }

    $liste = [];
    foreach ($geraete as $gid => $g) {
        if (!is_array($g)) continue;
        $liste[] = [
            'geraet_id'  => (string)$gid,
            'name'       => (string)($g['name'] ?? ''),
            'plattform'  => (string)($g['plattform'] ?? ''),
            'modus'      => (string)($g['modus'] ?? 'neu'),
            'berichte'   => (bool)($g['berichte'] ?? true),
            'seit'       => (string)($g['seit'] ?? ''),
            'zuletzt'    => (string)($g['zuletzt'] ?? ''),
            'push_aktiv' => is_array($g['push'] ?? null) && ($g['push']['token'] ?? '') !== '',
            'selbst'     => $eigen !== '' && $eigen === (string)$gid,
        ];
    }
    // Neueste Aktivität zuerst.
    usort($liste, static fn($a, $b) => strcmp((string)$b['zuletzt'], (string)$a['zuletzt']));
    json_antwort(200, ['ok' => true, 'geraete' => $liste]);
}

case 'widerrufen': {
    // Nur Cookie - so lässt sich ein verlorenes Handy von einem anderen Gerät
    // aussperren (SICHERHEIT.md B2/S-3, Runbook ARCHITEKTUR.md 8a).
    $gid = geraet_id_aus($roh['geraet_id'] ?? '');
    if ($gid === '') json_antwort(400, ['ok' => false, 'grund' => 'geraet_id']);
    if (!isset($geraete[$gid])) {
        // Nicht (mehr) vorhanden ist aus Sicht des Aufrufers dasselbe Ergebnis.
        json_antwort(200, ['ok' => true]);
    }
    unset($geraete[$gid]);
    if (!geraete_schreiben($datei, $geraete)) {
        json_antwort(500, ['ok' => false, 'grund' => 'schreiben']);
    }
    json_antwort(200, ['ok' => true]);
}

default:
    json_antwort(400, ['ok' => false, 'grund' => 'aktion']);
}
