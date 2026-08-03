// Service worker for the coaching dashboard PWA.
//
// Strategy: network-first for everything same-origin, falling back to
// the last good copy. The dashboard is a data display — showing stale
// numbers when fresh ones are available would be worse than a brief
// wait — but it also has to work standing in a field with no signal,
// so every successful response is kept as a fallback.
//
// Bump CACHE whenever this file changes so old caches are cleared and
// the new worker takes over on the next launch.
const CACHE = 'coaching-v4';
const BASE = '/coaching-dashboard/';
const SHELL = [BASE, BASE + 'index.html', BASE + 'manifest.json'];

// Data files are requested with a ?v=<timestamp> cache-buster, so each
// request would otherwise land under a unique key and never be found
// again. Store and look them up by path alone.
const keyFor = (req) => {
  const u = new URL(req.url);
  return u.origin + u.pathname;
};

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL))
      .catch(() => { /* a missing shell file must not block install */ })
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;

  // Leave cross-origin alone entirely — the Chart.js CDN, and above all
  // navigations out to the Google Forms. Intercepting those has no
  // upside and is exactly the sort of thing that breaks a link.
  if (new URL(req.url).origin !== self.location.origin) return;

  e.respondWith(
    fetch(req)
      .then(res => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(keyFor(req), copy)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(keyFor(req)).then(r => r || caches.match(req)))
  );
});
