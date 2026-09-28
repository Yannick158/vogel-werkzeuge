/* Artenportraet-Video in der Vogelkarte. Der Knopf erscheint nur, wenn es fuer
   diese Art ein Video gibt. Er sitzt neben dem Foto-Knopf und spielt das Video
   dort ab, wo sonst die Zeichnung steht.

   Wie bei den Fotos liegt die Flaeche UNTER der Knopfreihe (z-index 2 gegen
   3/4), damit man jederzeit zurueck zur Zeichnung findet. */
(function () {
  if ((location.hostname || '').indexOf('zweitstandort') >= 0) return;
  var ORT = '/eigenes/artvideos/';
  var liste = null, an = false, aktuell = '';

  var css = '\
.g-vid-knopf{position:absolute;z-index:4;bottom:14px;display:none;align-items:center;\
justify-content:center;width:34px;height:34px;border:1px solid var(--paper-3);\
border-radius:999px;background:var(--paper);color:var(--ink-soft);cursor:pointer;padding:0;\
transition:color .15s,border-color .15s,background .15s;}\
.g-vid-knopf.da{display:inline-flex;}\
.g-vid-knopf:hover{color:var(--ink);border-color:var(--ink-soft);}\
.g-vid-knopf[data-an="1"]{background:var(--ink);color:var(--paper);border-color:var(--ink);}\
.g-vid-knopf svg{width:17px;height:17px;}\
#g-vid-flaeche{position:absolute;inset:0;z-index:2;display:flex;align-items:center;\
justify-content:center;background:var(--paper);border-radius:inherit;overflow:hidden;}\
#g-vid-flaeche[hidden]{display:none;}\
#g-vid-flaeche video{max-width:100%;max-height:100%;display:block;}';
  var st = document.createElement('style'); st.textContent = css;
  (document.head || document.documentElement).appendChild(st);

  var FILM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9.2l5 2.8-5 2.8z"/></svg>';

  function el(id) { return document.getElementById(id); }
  function sciJetzt() {
    var e = el('modalSci');
    var t = e ? (e.textContent || '').trim() : '';
    if (t) return t;
    var m = /[#&]sci=([^&]+)/.exec(location.hash || '');
    return m ? decodeURIComponent(m[1]) : '';
  }

  function aus() {
    an = false;
    var f = el('g-vid-flaeche'), k = el('g-vid-knopf');
    if (f) {
      var v = f.querySelector('video');
      if (v) {
        v.pause();
        /* Quelle loesen: ein nur pausiertes Video haelt seinen geladenen
           Puffer fest. Beim naechsten Oeffnen wird ohnehin neu gesetzt. */
        try { v.removeAttribute('src'); v.removeAttribute('data-fuer'); v.load(); } catch (e) {}
      }
      f.hidden = true;
    }
    if (k) k.setAttribute('data-an', '0');
  }

  function umschalten() {
    if (an) { aus(); return; }
    var sci = sciJetzt();
    if (!liste || !liste[sci]) return;
    var f = el('g-vid-flaeche'), k = el('g-vid-knopf');
    if (!f) return;
    var v = f.querySelector('video');
    var datei = String(liste[sci].datei || '');
    if (!/^[\w.-]+\.mp4$/.test(datei)) return;
    if (v.getAttribute('data-fuer') !== sci) {
      /* Versionskennung anhaengen: der Dateiname aendert sich beim Neubau
         nicht, der Browser wuerde sonst seine alte Kopie behalten. */
      var stand = liste[sci].stand || '';
      v.src = ORT + datei + (stand ? '?v=' + stand : '');
      v.setAttribute('data-fuer', sci);
    }
    f.hidden = false;
    if (k) k.setAttribute('data-an', '1');
    an = true;
    v.play().catch(function () {});
  }

  function platziere() {
    var foto = el('g-foto-knopf'), tog = el('modalPoseToggle'), k = el('g-vid-knopf');
    if (!k) return;
    var links = 14;
    if (foto && foto.offsetWidth > 0) links = foto.offsetLeft + foto.offsetWidth + 8;
    else if (tog && tog.offsetWidth > 0) links = tog.offsetLeft + tog.offsetWidth + 8;
    k.style.left = links + 'px';
  }

  function bauen() {
    var tog = el('modalPoseToggle'), art = el('modalArtwork');
    if (tog && !el('g-vid-knopf')) {
      var b = document.createElement('button');
      b.type = 'button'; b.id = 'g-vid-knopf'; b.className = 'g-vid-knopf';
      b.title = 'Kurzes Porträt dieser Art ansehen';
      b.setAttribute('aria-label', 'Kurzes Porträt dieser Art ansehen');
      b.setAttribute('data-an', '0');
      b.innerHTML = FILM;
      if (tog.parentNode) tog.parentNode.insertBefore(b, tog.nextSibling);
    }
    if (art && !el('g-vid-flaeche')) {
      if (getComputedStyle(art).position === 'static') art.style.position = 'relative';
      var d = document.createElement('div');
      d.id = 'g-vid-flaeche'; d.hidden = true;
      d.innerHTML = '<video controls playsinline preload="none"></video>';
      art.appendChild(d);
    }
    platziere();
    var sci = sciJetzt();
    if (an && sci !== aktuell) aus();
    aktuell = sci;
    var k = el('g-vid-knopf');
    if (k) {
      var hat = !!(liste && liste[sci]);
      if (hat) k.classList.add('da'); else k.classList.remove('da');
    }
  }

  document.addEventListener('click', function (e) {
    var t = e.target; if (!t || !t.closest) return;
    if (t.closest('#g-vid-knopf')) { e.preventDefault(); e.stopPropagation(); umschalten(); return; }
    /* Pose waehlen oder Fotos oeffnen blendet das Video aus - sonst laege es
       stumm unter der Zeichnung. */
    if (t.closest('#modalPoseToggle') || t.closest('#g-foto-knopf')) { if (an) aus(); }
  }, true);

  window.addEventListener('resize', platziere);

  var obs = new MutationObserver(function () {
    obs.disconnect(); bauen();
    if (document.body) obs.observe(document.body, { childList: true, subtree: true });
  });
  function start() {
    fetch(ORT + 'videos.json', { credentials: 'same-origin', cache: 'no-cache' })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (j) { liste = j || {}; bauen(); })
      .catch(function () { liste = {}; });
    bauen();
    if (document.body) obs.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState !== 'loading') start();
  else document.addEventListener('DOMContentLoaded', start);
})();
