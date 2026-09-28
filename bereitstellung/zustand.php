<?php
// Beurteilt den Gesundheitszustand der Anlage und liefert ihn als JSON.
//
// DER WICHTIGE TEIL: Der Server urteilt SELBST, ohne den Pi zu fragen.
// Ein Waechter, der beim Pi nachfragen muesste, schwiege genau dann, wenn es
// darauf ankommt - naemlich wenn der Pi nicht mehr antwortet. Deshalb wird
// hier nur ausgewertet, was ohnehin auf dem Server liegt: das Alter der
// hochgeladenen Dateien. Bleibt der Abgleich aus, altern sie - und das faellt
// auf, ohne dass irgendjemand den Pi erreichen muss.
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

$DATEN   = '/srv/pi-daten';
$ZUSTAND = "$DATEN/zustand.json";
$DB      = "$DATEN/birds.db";

// Schwellen
const STILL_WARNUNG  = 1800;   // 30 Minuten ohne Abgleich: etwas stimmt nicht
const STILL_KRITISCH = 7200;   // 2 Stunden: der Pi ist weg
const TEMP_WARNUNG   = 70;
const TEMP_KRITISCH  = 78;
const PLATTE_WARNUNG = 85;
const PLATTE_KRITISCH= 95;
const SPANNUNG_ANTEIL_WARNUNG = 0.05;  // bei ueber 5 % der Messungen: "zu oft"

$meldungen = [];
$stufe = 'ok';   // ok | warnung | kritisch

function melde(string $s, string $text, string $rat = ''): void {
    global $meldungen, $stufe;
    $meldungen[] = ['stufe' => $s, 'text' => $text, 'rat' => $rat];
    if ($s === 'kritisch') $stufe = 'kritisch';
    elseif ($s === 'warnung' && $stufe !== 'kritisch') $stufe = 'warnung';
}

// --- 1. Schweigt der Pi? (das merkt der Server allein) -------------------
$alter = is_readable($DB) ? time() - filemtime($DB) : PHP_INT_MAX;
if ($alter === PHP_INT_MAX) {
    melde('kritisch', 'Vom Pi sind noch nie Daten angekommen.',
          'Läuft der Abgleich? Auf dem Pi: systemctl status vogel-upload.timer');
} elseif ($alter > STILL_KRITISCH) {
    $std = round($alter / 3600, 1);
    melde('kritisch', "Der Pi meldet sich seit $std Stunden nicht mehr.",
          'Strom und Netzwerk am Pi prüfen. Erkennungen gehen nicht verloren - sie kommen nach, sobald er wieder da ist.');
} elseif ($alter > STILL_WARNUNG) {
    melde('warnung', 'Der letzte Abgleich ist ' . round($alter / 60) . ' Minuten her.',
          'Normal sind zehn Minuten. Kurze Aussetzer kommen vor.');
}

// --- 2. Was der Pi selbst gemeldet hat ----------------------------------
$z = null;
if (is_readable($ZUSTAND)) {
    $z = json_decode((string)file_get_contents($ZUSTAND), true);
}

if (is_array($z)) {
    $sp = $z['spannung'] ?? [];
    $hf = $z['haeufigkeit'] ?? [];

    // Unterspannung - das haeufigste und heimtueckischste Problem
    $laeufe = max(1, (int)($hf['laeufe'] ?? 1));
    $mitSpannung = (int)($hf['mit_unterspannung'] ?? 0);
    $anteil = $mitSpannung / $laeufe;

    if (!empty($sp['unterspannung_jetzt'])) {
        melde('kritisch', 'Der Pi bekommt gerade zu wenig Strom.',
              'Ein stärkeres Netzteil verwenden (5,1 V / 3 A) und ein kurzes, dickes Kabel. Zu wenig Spannung zerstört auf Dauer die Speicherkarte.');
    } elseif ($anteil > SPANNUNG_ANTEIL_WARNUNG && $mitSpannung > 2) {
        $proz = round($anteil * 100);
        melde('warnung', "Der Pi hatte bei $mitSpannung von $laeufe Messungen zu wenig Strom ($proz Prozent).",
              'Das ist zu oft. Netzteil und Kabel prüfen, bevor die Speicherkarte Schaden nimmt.');
    } elseif (!empty($sp['unterspannung_je'])) {
        melde('warnung', 'Der Pi hatte seit dem letzten Start mindestens einmal zu wenig Strom.',
              'Einzelne Aussetzer sind verkraftbar. Häufen sie sich, hilft ein stärkeres Netzteil.');
    }

    if (!empty($sp['gedrosselt_jetzt'])) {
        melde('warnung', 'Der Pi rechnet gerade langsamer als er könnte.',
              'Meist Folge von Hitze oder zu wenig Strom. Er verpasst dabei möglicherweise Vögel.');
    }

    // Temperatur
    $t = (int)($z['temperatur_celsius'] ?? 0);
    if ($t >= TEMP_KRITISCH) {
        melde('kritisch', "Der Pi ist $t Grad heiß.",
              'Für Luft sorgen oder den Standort wechseln. Ab 80 Grad drosselt er sich selbst.');
    } elseif ($t >= TEMP_WARNUNG) {
        melde('warnung', "Der Pi ist $t Grad warm.",
              'Noch unkritisch, aber im Auge behalten.');
    }

    // Speicherplatz
    $belegt = (int)($z['platte']['belegt_prozent'] ?? 0);
    if ($belegt >= PLATTE_KRITISCH) {
        melde('kritisch', "Die Speicherkarte ist zu $belegt Prozent voll.",
              'Ist sie ganz voll, nimmt der Pi nichts mehr auf. Alte Aufnahmen löschen.');
    } elseif ($belegt >= PLATTE_WARNUNG) {
        melde('warnung', "Die Speicherkarte ist zu $belegt Prozent voll.", '');
    }

    // Speicherkarte am Sterben - die haeufigste Ausfallursache ueberhaupt
    $sd = (int)($z['sd_fehler'] ?? 0);
    if ($sd > 20) {
        melde('kritisch', "Die Speicherkarte meldet $sd Lesefehler.",
              'Das kündigt einen Ausfall an. Karte sichern und tauschen, bevor sie ganz aufgibt.');
    } elseif ($sd > 0) {
        melde('warnung', "Die Speicherkarte meldet $sd Lesefehler.",
              'Beobachten. Steigt die Zahl, die Karte tauschen.');
    }

    // Laeuft BirdNET ueberhaupt?
    if (isset($z['dienste_laufen']) && $z['dienste_laufen'] === false) {
        melde('kritisch', 'Auf dem Pi läuft die Vogelerkennung nicht.',
              'Auf dem Pi: sudo systemctl restart birdnet_recording birdnet_analysis');
    }
} elseif ($alter < STILL_KRITISCH) {
    melde('warnung', 'Der Pi schickt keine Zustandsdaten.',
          'Läuft zustand_erfassen.sh auf dem Pi?');
}

// --- 3. Hört der Pi überhaupt noch Vögel? -------------------------------
// Ein stummes Mikrofon oder ein abgestürzter Dienst faellt sonst tagelang
// nicht auf - die Anzeige sieht ja normal aus, nur ohne neue Voegel.
if (is_readable($DB)) {
    try {
        $db = new SQLite3($DB, SQLITE3_OPEN_READONLY);
        $letzte = $db->querySingle("SELECT Date || ' ' || Time FROM detections ORDER BY Date DESC, Time DESC LIMIT 1");
        if (is_string($letzte) && $letzte !== '') {
            $stunden = (time() - strtotime($letzte)) / 3600;
            // Nachts ist Stille normal, deshalb erst nach einem vollen Tag warnen
            if ($stunden > 24) {
                melde('warnung', 'Seit ' . round($stunden) . ' Stunden wurde kein Vogel mehr gehört.',
                      'Mikrofon prüfen - steckt es noch? Nachts ist Stille normal, über einen ganzen Tag nicht.');
            }
        }
        $db->close();
    } catch (Throwable $e) { /* keine Aussage moeglich */ }
}

echo json_encode([
    'stufe'      => $stufe,
    'meldungen'  => $meldungen,
    'geprueft'   => date('Y-m-d H:i:s'),
    'pi' => [
        'letzter_abgleich_vor_sekunden' => $alter === PHP_INT_MAX ? null : $alter,
        'temperatur'  => $z['temperatur_celsius'] ?? null,
        'laufzeit'    => $z['laufzeit_sekunden'] ?? null,
        'stand'       => $z['erstellt'] ?? null,
    ],
], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
