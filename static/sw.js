const CACHE = 'darkgc-v5';
const SHELL = ['/', '/static/style.css', '/static/darkgc-notifications.js', '/static/manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  // Always go to network for HTML so fresh content shows up
  if (event.request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(fetch(event.request).catch(() => caches.match('/')));
  } else {
    event.respondWith(caches.match(event.request).then((res) => res || fetch(event.request)));
  }
});

// In-app notification request (foreground / tab open)
self.addEventListener('message', (event) => {
  if (event.data?.type !== 'notify') return;
  const { title, body, tag, url } = event.data;
  event.waitUntil(self.registration.showNotification(title || 'Dark GHC website', {
    body: body || 'You have a new message.',
    tag: tag || 'darkgc-message',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    data: { url: url || '/' },
    vibrate: [90, 40, 90],
    renotify: true,
  }));
});

// REAL background push from server (works when app is fully closed)
self.addEventListener('push', (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (e) { payload = { title: 'Dark GHC website', body: event.data ? event.data.text() : '' }; }
  const title = payload.title || 'Dark GHC website';
  const body = payload.body || 'You have a new message.';
  const url = payload.url || '/';
  event.waitUntil(self.registration.showNotification(title, {
    body,
    tag: payload.tag || 'darkgc-push',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    data: { url },
    vibrate: [120, 60, 120],
    renotify: true,
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
    for (const win of wins) {
      if ('focus' in win) {
        try { win.navigate(url); } catch (e) {}
        return win.focus();
      }
    }
    return clients.openWindow(url);
  }));
});
