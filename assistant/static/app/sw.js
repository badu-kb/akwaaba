/* Akwaaba service worker: the app opens instantly and saved items work offline. */
const SHELL = "akwaaba-shell-v3";
const DATA = "akwaaba-data-v1";
const FILES = ["./", "index.html", "app.css", "app.js", "manifest.json", "icon-192.png", "icon-512.png", "icon-180.png", "fonts/bricolage-600.woff2", "fonts/hanken-400.woff2", "fonts/hanken-600.woff2", "fonts/mono-500.woff2", "kente.svg", "img/phone-accra.jpg", "img/hero-akwidaa.jpg", "img/mother-village.jpg", "img/kumasi-writing.jpg", "img/butre-river.jpg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => ![SHELL, DATA].includes(k)).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

const OFFLINE_API = /\/api\/(library|items\/\d+$|me$|history$)/;

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Saved items, profile and history: network first, fall back to the last copy.
  if (url.origin === location.origin && OFFLINE_API.test(url.pathname)) {
    e.respondWith(fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(DATA).then((c) => c.put(req.url, copy)); }
      return res;
    }).catch(() => caches.match(req.url).then((r) => r || new Response('{"detail":"offline"}', { status: 503, headers: { "Content-Type": "application/json" } }))));
    return;
  }
  // The app itself and its fonts: cached copy first, refreshed in the background.
  if ((url.origin === location.origin && url.pathname.startsWith("/app/")) ) {
    e.respondWith(caches.match(req).then((hit) => {
      const net = fetch(req).then((res) => {
        if (res.ok || res.type === "opaque") { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(req, copy)); }
        return res;
      }).catch(() => hit || caches.match("index.html"));
      return hit || net;
    }));
  }
});
