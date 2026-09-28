/* Blendet eine Warnung ein, wenn mit der Anlage etwas nicht stimmt.
 *
 * Bewusst zurueckhaltend gestaltet: Die Seite ist ein Wandbild im Wohnzimmer.
 * Solange alles laeuft, ist hier nichts zu sehen. Erst wenn etwas klemmt,
 * erscheint ein schmaler Streifen am oberen Rand - in derselben ruhigen
 * Formensprache wie der Rest, nicht als grelles Alarmschild.
 *
 * Die Beurteilung passiert auf dem Server (eigenes/zustand.php). Hier wird
 * nur angezeigt.
 */
(function () {
  'use strict';

  var ABSTAND_MS = 5 * 60 * 1000;   // alle fuenf Minuten nachsehen
  var streifen = null;

  function bauen() {
    if (streifen) return streifen;
    streifen = document.createElement('div');
    streifen.id = 'anlagen-warnung';
    streifen.setAttribute('role', 'status');
    streifen.style.cssText = [
      'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:9998',
      'font:500 13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace',
      'letter-spacing:.02em',
      'padding:10px 16px', 'text-align:center',
      'transform:translateY(-100%)', 'transition:transform .5s ease',
      'cursor:pointer', 'user-select:none'
    ].join(';');
    streifen.title = 'Antippen für Einzelheiten';
    document.body.appendChild(streifen);
    return streifen;
  }

  function zeigen(stufe, meldungen) {
    var s = bauen();
    var kritisch = stufe === 'kritisch';
    // Gedeckte Farben, die zur Holzschnitt-Aesthetik passen -
    // kein Signalrot, das nachts im Wohnzimmer schreit.
    s.style.background = kritisch ? '#6b2d2d' : '#7a6a3a';
    s.style.color = '#f5f1e8';
    s.style.boxShadow = '0 2px 12px rgba(0,0,0,.18)';

    var erste = meldungen[0];
    var mehr = meldungen.length > 1 ? '  (+' + (meldungen.length - 1) + ' weitere)' : '';
    s.textContent = (kritisch ? '⚠ ' : '') + erste.text + mehr;

    s.onclick = function () { location.href = '/eigenes/status.html'; };
    requestAnimationFrame(function () { s.style.transform = 'translateY(0)'; });
  }

  function verbergen() {
    if (streifen) streifen.style.transform = 'translateY(-100%)';
  }

  // Ein Punkt am Menue-Knopf, wenn etwas nicht stimmt - so faellt es auch auf,
  // ohne dass der Warnstreifen gerade sichtbar ist. Der Punkt selbst ist Teil
  // der Original-Oberflaeche (.menu-btn.has-dot), wir schalten ihn nur.
  function menuePunkt(anzeigen) {
    var btn = document.getElementById('menuBtn');
    if (btn) btn.classList.toggle('has-dot', !!anzeigen);
  }

  function nachsehen() {
    fetch('/eigenes/zustand.php', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var alles_gut = d.stufe === 'ok' || !d.meldungen || !d.meldungen.length;
        menuePunkt(!alles_gut);
        if (alles_gut) verbergen();
        else zeigen(d.stufe, d.meldungen);
      })
      .catch(function () { /* Netzproblem - dann eben beim naechsten Mal */ });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', nachsehen);
  } else {
    nachsehen();
  }
  setInterval(nachsehen, ABSTAND_MS);
})();
