// DARK GC service worker — offline shell + push-style notifications
const CACHE = "darkgc-v1";
const SHELL = ["/", "/static/style.css", "/static/manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(()=>self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  // network-first for HTML, cache-first for everything else
  if (req.headers.get("accept")?.includes("text/html")) {
    e.respondWith(fetch(req).catch(() => caches.match("/")));
  } else {
    e.respondWith(caches.match(req).then(r => r || fetch(req)));
  }
});

// Allow the page to ask the SW to show a notification (works in background tabs)
self.addEventListener("message", e => {
  if (e.data?.type === "notify") {
    const { title, body, tag, url } = e.data;
    self.registration.showNotification(title || "DARK GC", {
      body: body || "",
      tag: tag || "darkgc",
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      data: { url: url || "/" },
      vibrate: [80, 40, 80]
    });
  }
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = e.notification.data?.url || "/";
  e.waitUntil(clients.matchAll({ type: "window" }).then(wins => {
    for (const w of wins) { if (w.url.includes(url) && "focus" in w) return w.focus(); }
    return clients.openWindow(url);
  }));
});
