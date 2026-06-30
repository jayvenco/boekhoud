const CACHE = 'boekhoud-v1';

// App-shell bestanden die altijd gecached worden
const SHELL = [
  '/',
  '/static/favicon.png',
  '/static/logo.png',
  'https://fonts.googleapis.com/css2?family=Open+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,300;1,400&display=swap',
];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(cache) {
      return cache.addAll(SHELL);
    }).then(function() {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE; })
            .map(function(k) { return caches.delete(k); })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

self.addEventListener('fetch', function(e) {
  const url = new URL(e.request.url);

  // Sla POST-verzoeken en externe verzoeken (CDN etc.) over
  if (e.request.method !== 'GET') return;
  if (url.origin !== location.origin &&
      !url.href.startsWith('https://fonts.')) return;

  // Navigatieverzoeken (HTML-pagina's): network-first, offline-fallback
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(function() {
        return caches.match('/') || new Response(
          '<html><body style="font-family:sans-serif;padding:40px;text-align:center">' +
          '<h2>Geen internetverbinding</h2>' +
          '<p>Controleer uw verbinding en probeer het opnieuw.</p>' +
          '<button onclick="location.reload()">Opnieuw proberen</button>' +
          '</body></html>',
          { headers: { 'Content-Type': 'text/html' } }
        );
      })
    );
    return;
  }

  // Statische bestanden (logo, fonts): cache-first
  if (url.pathname.startsWith('/static/') ||
      url.href.startsWith('https://fonts.')) {
    e.respondWith(
      caches.match(e.request).then(function(cached) {
        return cached || fetch(e.request).then(function(resp) {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE).then(function(c) { c.put(e.request, clone); });
          }
          return resp;
        });
      })
    );
  }
});
