<?php
// Symbol fuer Push-Benachrichtigungen: das Foto der gemeldeten Art.
//
// WARUM DIESER ENDPUNKT VOR DEM ANMELDETOR LIEGT:
// Chrome holt das Symbol einer Benachrichtigung selbst, ohne unsere Sitzung.
// Hinter dem Tor bekaeme es die Anmeldeseite statt eines Bildes.
//
// WARUM DAS UNBEDENKLICH IST:
// Herausgegeben werden ausschliesslich bereits zwischengespeicherte Fotos aus
// oeffentlichen Quellen (iNaturalist, Wikimedia). Wer hier etwas abrufen will,
// muss den Artnamen schon kennen - ueber den Garten, die Erkennungen oder die
// Bewohner verraet der Endpunkt nichts. Er holt auch NICHTS nach: was nicht im
// Puffer liegt, wird durch die Taube ersetzt. Damit gibt es keinen ausgehenden
// Verkehr, den ein Fremder ausloesen koennte.
declare(strict_types=1);

const PUFFER = '/srv/avian/vogelbilder';
const TAUBE  = '/srv/login/taube.png';

function taube_ausliefern(): void {
    // Das Original ist 1,4 MB gross - als Symbol viel zu schwer. Beim ersten
    // Aufruf wird einmalig eine kleine Fassung erzeugt und wiederverwendet.
    $klein = PUFFER . '/taube-192.png';
    if (!is_file($klein) && is_file(TAUBE) && function_exists('imagecreatefrompng')) {
        $q = @imagecreatefrompng(TAUBE);
        if ($q !== false) {
            $b = imagesx($q); $h = imagesy($q); $kante = min($b, $h);
            $z = imagecreatetruecolor(192, 192);
            imagealphablending($z, false); imagesavealpha($z, true);
            imagecopyresampled($z, $q, 0, 0, (int)(($b - $kante) / 2), (int)(($h - $kante) / 2),
                               192, 192, $kante, $kante);
            @imagepng($z, $klein, 6);
            imagedestroy($z); imagedestroy($q);
        }
    }
    $aus = is_file($klein) ? $klein : TAUBE;
    if (is_file($aus)) {
        header('Content-Type: image/png');
        header('Content-Length: ' . (string)filesize($aus));
        header('Cache-Control: public, max-age=86400');
        readfile($aus);
    } else {
        http_response_code(404);
    }
    exit;
}

$sci = trim((string)($_GET['sci'] ?? ''));
// Gleiche Regel wie in vogelbilder.php - Buchstaben, Punkt, Strich, Klammern.
if ($sci === '' || strlen($sci) > 120 || !preg_match('/^[\p{L}\p{M}\s.\'()-]+$/u', $sci)) {
    taube_ausliefern();
}

$slug = strtolower(preg_replace('/[^a-z0-9]+/i', '-', $sci));
$gross = isset($_GET['gross']);
$wurzel = realpath(PUFFER);
if ($wurzel === false) taube_ausliefern();

// Kleine Fassung fuer das Symbol, damit die Benachrichtigung sofort steht.
// Chrome bricht das Laden ab, wenn das Bild zu lange braucht.
$quelle = $wurzel . '/' . $slug . '/0.jpg';
$ziel   = $gross ? $quelle : $wurzel . '/' . $slug . '/symbol-192.jpg';

$echt = realpath($quelle);
// Der aufgeloeste Pfad MUSS im Puffer liegen - schuetzt vor ../-Auswegen.
if ($echt === false || strncmp($echt, $wurzel . '/', strlen($wurzel) + 1) !== 0 || !is_file($echt)) {
    taube_ausliefern();
}

if (!$gross && !is_file($ziel) && function_exists('imagecreatefromjpeg')) {
    $bild = @imagecreatefromjpeg($echt);
    if ($bild !== false) {
        $b = imagesx($bild); $h = imagesy($bild);
        $kante = min($b, $h);                                  // mittig quadratisch
        $klein = imagecreatetruecolor(192, 192);
        imagecopyresampled($klein, $bild, 0, 0,
                           (int)(($b - $kante) / 2), (int)(($h - $kante) / 2),
                           192, 192, $kante, $kante);
        @imagejpeg($klein, $ziel, 82);
        imagedestroy($klein); imagedestroy($bild);
    }
}

$aus = (!$gross && is_file($ziel)) ? $ziel : $echt;
header('Content-Type: image/jpeg');
header('Content-Length: ' . (string)filesize($aus));
header('Cache-Control: public, max-age=604800');
readfile($aus);
