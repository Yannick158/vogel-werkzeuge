// Temperaturhinweis fuer die Vogelseite. Liest temperatur.json, die der
// Temperaturwaechter auf dem Pi pflegt, und legt bei Bedarf eine Leiste
// ueber die Seite:
//   gelb - Temperatur ueber der Warnschwelle, mit aktuellem Wert
//   rot  - das Geraet hatte sich wegen Ueberhitzung abgeschaltet;
//          bleibt stehen, bis jemand auf das Kreuz tippt (je Geraet gemerkt)
(function () {
  var MERKER = 'temperaturQuittiert';

  var leiste = document.createElement('div');
  leiste.style.cssText =
    'position:fixed;top:0;left:0;right:0;z-index:9999;display:none;' +
    'padding:10px 48px 10px 16px;font:14px/1.5 system-ui,-apple-system,sans-serif;' +
    'color:#fff;text-align:center;box-shadow:0 1px 6px rgba(0,0,0,.3)';
  var text = document.createElement('span');
  var knopf = document.createElement('button');
  knopf.textContent = '×';
  knopf.setAttribute('aria-label', 'Hinweis schliessen');
  knopf.style.cssText =
    'position:absolute;right:8px;top:50%;transform:translateY(-50%);' +
    'background:none;border:none;color:#fff;font-size:22px;padding:4px 10px;cursor:pointer';
  leiste.appendChild(text);
  leiste.appendChild(knopf);
  if (document.body) document.body.appendChild(leiste);
  else document.addEventListener('DOMContentLoaded', function () {
    document.body.appendChild(leiste);
  });

  var quittungsWert = null;
  knopf.onclick = function () {
    if (quittungsWert) {
      try { localStorage.setItem(MERKER, quittungsWert); } catch (e) {}
    }
    leiste.style.display = 'none';
  };

  function zeige(inhalt, farbe, mitKnopf, schluessel) {
    text.textContent = inhalt;
    leiste.style.background = farbe;
    knopf.style.display = mitKnopf ? 'block' : 'none';
    quittungsWert = schluessel || null;
    leiste.style.display = 'block';
  }

  function pruefe() {
    fetch('temperatur.json', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var quittiert = null;
        try { quittiert = localStorage.getItem(MERKER); } catch (e) {}
        if (d.letzte_abschaltung && d.letzte_abschaltung !== quittiert) {
          zeige('Der Vogel-Pi hatte sich am ' + d.letzte_abschaltung +
            ' Uhr wegen Überhitzung abgeschaltet.',
            '#a33327', true, d.letzte_abschaltung);
        } else if (d.status === 'warnung') {
          zeige('Temperatur hoch: ' + d.temp_c + ' °C — bei ' +
            d.abschaltung_ab_c + ' °C schaltet sich das Gerät ab.',
            '#b07a1e', false, null);
        } else {
          leiste.style.display = 'none';
        }
      })
      .catch(function () {});
  }

  pruefe();
  setInterval(pruefe, 60000);
})();
