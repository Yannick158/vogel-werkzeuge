/* Hält alle Töne an, sobald man die Vogelkarte verlässt oder die Art wechselt.
   Ohne das lief das Artenporträt oder eine Vergleichsaufnahme munter weiter,
   während längst wieder die Collage zu sehen war.

   Zusätzlich wird die Videoquelle geleert. Ein pausiertes Video behält sonst
   seinen geladenen Puffer im Speicher - bei mehreren angesehenen Arten summiert
   sich das. */
(function () {
  if ((location.hostname || '').indexOf('zweitstandort') >= 0) return;

  function alleStoppen(wurzel) {
    var m = (wurzel || document).querySelectorAll('video, audio');
    for (var i = 0; i < m.length; i++) {
      try {
        m[i].pause();
        m[i].currentTime = 0;
        if (m[i].tagName === 'VIDEO' && m[i].src) {
          m[i].removeAttribute('src');
          m[i].removeAttribute('data-fuer');
          m[i].load();                 /* gibt den Puffer frei */
        }
      } catch (e) { /* ein steckengebliebenes Element darf den Rest nicht aufhalten */ }
    }
    /* Vergleichsflächen mitnehmen - sie gehören zur geschlossenen Karte */
    var f = (wurzel || document).querySelectorAll('.g-vgl');
    for (var j = 0; j < f.length; j++) f[j].remove();
    var k = (wurzel || document).querySelectorAll('.g-vgl-auf');
    for (var n = 0; n < k.length; n++) {
      k[n].setAttribute('data-an', '0');
      k[n].innerHTML = '<span aria-hidden="true">≡</span><span>vergleichen</span>';
    }
    var vf = document.getElementById('g-vid-flaeche');
    if (vf) vf.hidden = true;
    var vk = document.getElementById('g-vid-knopf');
    if (vk) vk.setAttribute('data-an', '0');
  }

  function karte() { return document.getElementById('postcard-modal'); }

  var zuletztOffen = null, zuletztSci = '';
  function pruefen() {
    var m = karte();
    if (!m) return;
    var offen = m.getAttribute('aria-hidden') !== 'true';
    var e = document.getElementById('modalSci');
    var sci = e ? (e.textContent || '').trim() : '';

    if (zuletztOffen === true && !offen) alleStoppen(m);      /* Karte zugemacht */
    else if (offen && sci && zuletztSci && sci !== zuletztSci) alleStoppen(m);  /* andere Art */

    zuletztOffen = offen;
    if (sci) zuletztSci = sci;
  }

  function start() {
    var m = karte();
    if (!m) { setTimeout(start, 400); return; }
    zuletztOffen = m.getAttribute('aria-hidden') !== 'true';
    new MutationObserver(pruefen).observe(m, {
      attributes: true, attributeFilter: ['aria-hidden', 'class'],
      childList: true, subtree: true
    });
    /* Escape und Zurück-Taste lösen kein Attribut-Ereignis aus, das wir sehen */
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') setTimeout(pruefen, 80);
    });
    window.addEventListener('pagehide', function () { alleStoppen(document); });
  }
  if (document.readyState !== 'loading') start();
  else document.addEventListener('DOMContentLoaded', start);
})();
