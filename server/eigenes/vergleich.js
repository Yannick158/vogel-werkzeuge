/* Vergleichen: hoert sich der erkannte Vogel wirklich so an?
   Knopf in jeder Aufnahmenzeile, direkt neben "falsch". Oben die eigene
   Aufnahme, darunter Referenzen aus dem xeno-canto-Archiv zum Durchklicken.
   Beide Spektrogramme entstehen auf dem Server in derselben Darstellung -
   sonst vergleicht man die Darstellung statt den Vogel. */
(function () {
  if ((location.hostname || '').indexOf('zweitstandort') >= 0) return;
  var API = './avian/api/vergleich.php';

  var css = '\
/* Die Leiste ist ein Raster mit GENAU DREI Spalten (24px, 1fr, auto). Ein\
   viertes Kind landet sonst in einer Zeile darunter - ausserhalb der 30 Pixel\
   hohen, absolut gesetzten Leiste und damit unsichtbar. Deshalb eine vierte\
   Spalte, mit derselben Genauigkeit wie die Regel im Stylesheet. */\
.postcard-recordings .rec-player-controls{grid-template-columns:24px minmax(0,1fr) auto auto;}\
.g-vgl-auf{display:inline-flex;align-items:center;gap:5px;\
border:0;background:transparent;cursor:pointer;padding:4px 2px;\
font:9.5px/1 ui-sans-serif,system-ui,sans-serif;letter-spacing:.09em;\
text-transform:uppercase;color:var(--ink-soft);opacity:.75;\
transition:opacity .15s,color .15s;white-space:nowrap;}\
.g-vgl-auf:hover{opacity:1;color:var(--ink);}\
.g-vgl-auf[data-an="1"]{opacity:1;color:var(--ink);}\
.g-vgl-auf:hover{opacity:1;color:var(--ink);}\
.g-vgl-auf[data-an="1"]{opacity:1;color:var(--ink);}\
.g-vgl{margin:10px 0 4px;padding:14px 14px 12px;border:1px solid var(--paper-3);border-radius:10px;\
background:var(--paper);}\
.g-vgl h4{margin:0 0 6px;font:9.5px/1 ui-sans-serif,system-ui,sans-serif;letter-spacing:.11em;\
text-transform:uppercase;color:var(--ink-soft);font-weight:400;}\
.g-vgl img{width:100%;height:auto;display:block;border-radius:6px;background:#100e12;}\
.g-vgl audio{width:100%;margin:7px 0 0;height:32px;}\
.g-vgl-block{margin:0 0 14px;}\
.g-vgl-block:last-child{margin-bottom:0;}\
.g-vgl-kopf{display:flex;align-items:center;justify-content:space-between;gap:10px;}\
.g-vgl-nav{display:flex;gap:4px;}\
.g-vgl-nav button{border:1px solid var(--paper-3);background:var(--paper);border-radius:6px;\
width:26px;height:24px;cursor:pointer;color:var(--ink-soft);font:13px/1 Georgia,serif;padding:0;}\
.g-vgl-nav button:hover{color:var(--ink);}\
.g-vgl-quelle{margin:6px 0 0;font:9.5px/1.5 ui-sans-serif,system-ui,sans-serif;letter-spacing:.03em;\
color:var(--ink-soft);}\
.g-vgl-quelle a{color:inherit;}\
.g-vgl-laden{font:11px/1.5 Georgia,serif;font-style:italic;color:var(--ink-soft);padding:6px 0;}';
  var st = document.createElement('style'); st.textContent = css;
  (document.head || document.documentElement).appendChild(st);

  /* Der Artname steht NICHT in der Adresszeile, sondern im versteckten
     #modalSci. Genau daran scheiterte schon die Fotogalerie. */
  function sciJetzt() {
    var e = document.getElementById('modalSci');
    var t = e ? (e.textContent || '').trim() : '';
    if (t) return t;
    var m = /[#&]sci=([^&]+)/.exec(location.hash || '');
    return m ? decodeURIComponent(m[1]) : '';
  }

  var listen = {};          /* sci -> Aufnahmen */
  var stand = {};           /* Dateiname -> laufende Nummer */

  function knopfBauen() {
    /* Der Knopf sitzt in der Abspielerleiste neben "loop". Dort taucht er von
       selbst erst auf, wenn eine Aufnahme geoeffnet ist - im Zeilenkopf hatte
       er dagegen die Zeitangabe ueberdeckt. */
    var leisten = document.querySelectorAll('.rec-player-controls');
    for (var i = 0; i < leisten.length; i++) {
      var l = leisten[i];
      if (l.querySelector('.g-vgl-auf')) continue;
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'g-vgl-auf';
      b.innerHTML = '<span aria-hidden="true">≡</span><span>vergleichen</span>';
      b.title = 'Mit Archivaufnahmen vergleichen';
      b.setAttribute('data-an', '0');
      l.appendChild(b);
    }
  }

  function bauePanel(zeile, sci, datei) {
    var p = document.createElement('div');
    p.className = 'g-vgl';
    p.innerHTML =
      '<div class="g-vgl-block"><h4>Ihre Aufnahme</h4>'
      + '<img alt="Spektrogramm Ihrer Aufnahme" data-eigen>'
      + '<audio controls preload="none" data-eigen-ton></audio></div>'
      + '<div class="g-vgl-block"><div class="g-vgl-kopf"><h4 data-titel>Vergleich</h4>'
      + '<span class="g-vgl-nav"><button type="button" data-zurueck aria-label="vorherige">&lsaquo;</button>'
      + '<button type="button" data-weiter aria-label="nächste">&rsaquo;</button></span></div>'
      + '<img alt="Spektrogramm der Vergleichsaufnahme" data-ref>'
      + '<audio controls preload="none" data-ref-ton></audio>'
      + '<p class="g-vgl-quelle" data-quelle></p></div>';
    var eigen = p.querySelector('[data-eigen]');
    eigen.src = API + '?eigene=' + encodeURIComponent(datei) + '&spek=1';
    p.querySelector('[data-eigen-ton]').src = API + '?eigene=' + encodeURIComponent(datei);
    /* An die ZEILE haengen, nicht in den Spektrogramm-Block: der hat
       "overflow:hidden" und im geoeffneten Zustand exakt 142 Pixel feste
       Hoehe - alles darin wird abgeschnitten. Die Zeile selbst ist ein
       schlichtes Block-Element und waechst mit. */
    zeile.appendChild(p);
    return p;
  }

  function zeige(p, sci) {
    var l = listen[sci] || [];
    var titel = p.querySelector('[data-titel]');
    var bild = p.querySelector('[data-ref]');
    var ton = p.querySelector('[data-ref-ton]');
    var q = p.querySelector('[data-quelle]');
    if (!l.length) {
      titel.textContent = 'Vergleich';
      q.textContent = 'Für diese Art liegen keine Archivaufnahmen vor.';
      bild.removeAttribute('src'); ton.removeAttribute('src');
      return;
    }
    var n = stand[sci] || 0;
    if (n < 0) n = l.length - 1;
    if (n >= l.length) n = 0;
    stand[sci] = n;
    var a = l[n];
    titel.textContent = 'Vergleich ' + (n + 1) + ' von ' + l.length
                      + (a.typ ? '  ·  ' + a.typ : '');
    bild.src = API + '?sci=' + encodeURIComponent(sci) + '&nr=' + a.nr + '&spek=1';
    ton.src = API + '?sci=' + encodeURIComponent(sci) + '&nr=' + a.nr;
    q.textContent = '';
    q.appendChild(document.createTextNode(
      a.wer + '  ·  ' + a.lizenz + (a.ort ? '  ·  ' + a.ort : '') + '  ·  '));
    var link = document.createElement('a');
    link.href = a.seite; link.target = '_blank'; link.rel = 'noopener';
    link.textContent = 'xeno-canto';
    q.appendChild(link);
  }

  function laden(p, sci) {
    if (listen[sci]) { zeige(p, sci); return; }
    var q = p.querySelector('[data-quelle]');
    q.textContent = 'Archivaufnahmen werden geholt …';
    fetch(API + '?sci=' + encodeURIComponent(sci), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) { listen[sci] = (j && j.aufnahmen) || []; zeige(p, sci); })
      .catch(function () { listen[sci] = []; zeige(p, sci); });
  }

  document.addEventListener('click', function (e) {
    var t = e.target; if (!t || !t.closest) return;

    var b = t.closest('.g-vgl-auf');
    if (b) {
      e.preventDefault(); e.stopPropagation();
      var zeile = b.closest('.rec-row');
      var datei = zeile ? zeile.getAttribute('data-file') : '';
      var sci = sciJetzt();
      if (!zeile || !datei || !sci) return;
      var vorhanden = zeile.querySelector('.g-vgl');
      if (vorhanden) {
        vorhanden.remove(); b.setAttribute('data-an', '0');
        b.textContent = '≡';
      b.title = 'Mit Archivaufnahmen vergleichen';
      b.setAttribute('aria-label', 'Mit Archivaufnahmen vergleichen');
        return;
      }
      var p = bauePanel(zeile, sci, datei);
      b.setAttribute('data-an', '1');
      b.innerHTML = '<span aria-hidden="true">✕</span><span>vergleich zu</span>';
      laden(p, sci);
      return;
    }

    var z = t.closest('[data-zurueck]'), w = t.closest('[data-weiter]');
    if (z || w) {
      e.preventDefault(); e.stopPropagation();
      var panel = t.closest('.g-vgl');
      var s = sciJetzt();
      if (!panel || !s) return;
      stand[s] = (stand[s] || 0) + (w ? 1 : -1);
      zeige(panel, s);
    }
  }, true);

  /* Wird die Zeile zugeklappt, verschwindet auch die Vergleichsflaeche -
     sonst haengt sie unter einer geschlossenen Zeile in der Luft. */
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var kopf = t.closest('.rec-row-toggle');
    if (!kopf) return;
    var zeile = kopf.closest('.rec-row');
    var flaeche = zeile && zeile.querySelector('.g-vgl');
    if (!flaeche) return;
    setTimeout(function () {
      if (!zeile.classList.contains('expanded')) {
        flaeche.remove();
        var k = zeile.querySelector('.g-vgl-auf');
        if (k) {
          k.setAttribute('data-an', '0');
          k.innerHTML = '<span aria-hidden="true">≡</span><span>vergleichen</span>';
        }
      }
    }, 60);
  }, true);

  var obs = new MutationObserver(function () {
    obs.disconnect(); knopfBauen();
    if (document.body) obs.observe(document.body, { childList: true, subtree: true });
  });
  function start() {
    knopfBauen();
    if (document.body) obs.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState !== 'loading') start();
  else document.addEventListener('DOMContentLoaded', start);
})();
