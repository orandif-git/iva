// Jas iVision service worker. Keeps the app's own files so it opens instantly, and keeps the last copy of a few read-only
// pages of data so the app still shows something when the network is down. It never keeps sign-in tokens, camera pictures,
// file exports or anything from the admin or internal areas, and the saved data is deleted when the person signs out.
const VERSION = "v5";
const SHELL = `gi-shell-${VERSION}`, API = `gi-api-${VERSION}`;
const PRECACHE = ["/", "/css/app.css", "/js/lib.js", "/js/app/main.js", "/js/app/ui.js", "/js/app/shell.js", "/js/app/alertrules.js", "/js/app/areas.js", "/js/app/table.js", "/js/app/theme.js", "/js/app/screens.js", "/js/app/recorders.js",
  "/img/logo-compact.svg", "/img/logo-compact-on-dark.svg", "/img/logo.svg", "/img/logo-on-dark.svg", "/img/favicon.svg", "/img/site.webmanifest"];
// Read-only data worth showing offline. Everything else under /v1 always goes to the network.
const SAVE = [/^\/v1\/me$/, /^\/v1\/cameras(\/[\w-]+\/zones)?$/, /^\/v1\/recorders(\/[\w-]+(\/channels)?)?$/, /^\/v1\/footfall(\/summary)?$/, /^\/v1\/plates$/,
  /^\/v1\/alerts$/, /^\/v1\/alert-types$/, /^\/v1\/reports$/];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(SHELL).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => ![SHELL, API].includes(k)).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("message", (e) => { if (e.data === "clear-api-cache") e.waitUntil(caches.delete(API)); });

self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  const p = url.pathname;
  if (p.startsWith("/v1/")) {
    if (req.headers.get("X-View-As")) return;                  // a staff member looking at a customer: nothing is kept on this device
    if (SAVE.some((re) => re.test(p))) e.respondWith(networkThenSaved(req));
    return;                                                     // everything else: the network, untouched
  }
  if (p.startsWith("/admin") || p.startsWith("/internal") || p.startsWith("/staff") || p.startsWith("/next") || p.startsWith("/evidence") || p === "/set-password") return;
  if (req.mode === "navigate") { e.respondWith(fetch(req).catch(() => caches.match("/"))); return; }
  e.respondWith(staleWhileRevalidate(req));
});

async function staleWhileRevalidate(req) {
  const cache = await caches.open(SHELL), hit = await cache.match(req);
  const net = fetch(req).then((r) => { if (r.ok) cache.put(req, r.clone()); return r; }).catch(() => null);
  return hit || (await net) || Response.error();
}

async function networkThenSaved(req) {
  const cache = await caches.open(API);
  const key = new Request(req.url);                              // the saved copy is keyed by address only: tokens are never stored
  try {
    const r = await fetch(req);
    if (r.ok) cache.put(key, r.clone());
    return r;
  } catch (err) {
    const hit = await cache.match(key);
    if (!hit) throw err;
    const headers = new Headers(hit.headers);
    headers.set("X-From-Cache", "1");
    return new Response(hit.body, { status: 200, headers });
  }
}
