/* Sirdar — Standort-Button für die Wetter-Integration (Settings).
 *
 * Holt per navigator.geolocation die Browser-Koordinaten und schreibt sie in die
 * Felder #weather_latitude / #weather_longitude. Anschließend (optional) fragt es
 * den serverseitigen Reverse-Geocode-Endpoint ab und zeigt "Erkannt: <Stadt>".
 * Gespeichert wird ganz normal über das bestehende Settings-Formular.
 *
 * Prinzip wie MeBelo (weather.api.ts): Browser liefert Koordinaten, Stadtname
 * via Reverse-Geocoding. Hier serverseitig (CORS-frei). Robust: Fehler -> nur
 * Koordinaten / Hinweis, kein Crash. Alle Texte kommen via data-* aus i18n.
 */
(function () {
  'use strict';

  var btn = document.getElementById('weather-geolocate');
  if (!btn) return;

  var latInput = document.getElementById('weather_latitude');
  var lonInput = document.getElementById('weather_longitude');
  var statusEl = document.getElementById('weather-geo-status');

  function t(key, fallback) {
    return (btn.dataset && btn.dataset[key]) || fallback;
  }

  function setStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text || '';
    statusEl.hidden = !text;
    statusEl.className = 'geo-status' + (kind ? ' ' + kind : '');
  }

  function round5(n) {
    return Math.round(n * 1e5) / 1e5;
  }

  function reverseGeocode(lat, lon) {
    // Ortsnamen anzeigen — rein kosmetisch; Fehler => nur Koordinaten.
    fetch('/api/geocode/reverse?lat=' + encodeURIComponent(lat) + '&lon=' + encodeURIComponent(lon))
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(function (data) {
        var coords = lat.toFixed(4) + ', ' + lon.toFixed(4);
        if (data && data.name) {
          setStatus(t('detected', 'Detected') + ': ' + data.name + ' (' + coords + ')', 'ok');
        } else {
          setStatus(t('coordsSet', 'Coordinates set') + ' (' + coords + ')', 'ok');
        }
      })
      .catch(function () {
        var coords = lat.toFixed(4) + ', ' + lon.toFixed(4);
        setStatus(t('coordsSet', 'Coordinates set') + ' (' + coords + ')', 'ok');
      });
  }

  btn.addEventListener('click', function () {
    if (!navigator.geolocation) {
      setStatus(t('unsupported', 'Geolocation not supported.'), 'bad');
      return;
    }
    setStatus(t('locating', 'Locating …'), null);
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        var lat = round5(pos.coords.latitude);
        var lon = round5(pos.coords.longitude);
        if (latInput) latInput.value = lat;
        if (lonInput) lonInput.value = lon;
        reverseGeocode(lat, lon);
      },
      function (err) {
        if (err && err.code === 1) {
          setStatus(t('denied', 'Location access denied.'), 'bad');
        } else {
          setStatus(t('error', 'Could not determine location.'), 'bad');
        }
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 }
    );
  });
})();
