/* Sirdar Service Worker — PWA foundation (Phase 0).
 *
 * Strategy:
 *  - PRECACHE: app shell on install
 *  - HTML/navigations: network-first, fall back to cache then '/'
 *  - /static/: cache-first (revalidate in background)
 *
 * Push handlers follow in Phase 2/3.
 */

const VERSION = 'sirdar-0.1.0';
const STATIC_CACHE = `sirdar-static-${VERSION}`;
const RUNTIME_CACHE = `sirdar-runtime-${VERSION}`;

const PRECACHE = [
  '/',
  '/static/css/style.css',
  '/static/icon.svg',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE).catch(() => null))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== STATIC_CACHE && k !== RUNTIME_CACHE)
            .map((k) => caches.delete(k)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Static assets: cache-first.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((resp) => {
        const copy = resp.clone();
        caches.open(STATIC_CACHE).then((c) => c.put(request, copy));
        return resp;
      }).catch(() => cached)),
    );
    return;
  }

  // HTML / navigations: network-first, fall back to cache, then '/'.
  if (request.mode === 'navigate' || (request.headers.get('accept') || '').includes('text/html')) {
    event.respondWith(
      fetch(request).then((resp) => {
        const copy = resp.clone();
        caches.open(RUNTIME_CACHE).then((c) => c.put(request, copy));
        return resp;
      }).catch(() => caches.match(request).then((cached) => cached || caches.match('/'))),
    );
  }
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});
