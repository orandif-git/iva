/* Control Center service worker. Its scope is /console/ only (the old app's /sw.js is a different file that retires itself).
   It keeps the app's own pages and pictures so the app opens quickly and shows something without a connection, and it shows push notifications.
   It never keeps anything from /admin/ (every number comes from the server each time). */
const CACHE = "console-v1";
const SHELL = ["/console/", "/console/console.css", "/console/phone.css", "/console/app.js", "/console/lib.js", "/console/logo-compact.svg", "/img/icon-192.png"];

self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())); });

/* Network first, so a new version is used as soon as it is deployed; the saved copy is only for when there is no connection. */
self.addEventListener("fetch", (e) => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || !(u.pathname.startsWith("/console/") || u.pathname.startsWith("/img/"))) return;
  if (u.pathname === "/console/sw.js") return;
  e.respondWith(fetch(r).then((res) => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(r, copy)); } return res; }).catch(() => caches.match(r).then((m) => m || caches.match("/console/"))));
});

/* Push: the server sends {title, body, url, tag}. */
self.addEventListener("push", (e) => {
  let d = {}; try { d = e.data ? e.data.json() : {}; } catch { d = { title: "Jas iVision", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Jas iVision", { body: d.body || "", tag: d.tag || undefined, icon: "/img/icon-192.png", badge: "/img/icon-192.png", data: { url: d.url || "/console/" }, renotify: !!d.tag }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "/console/", location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
    const c = cs.find((x) => x.url.startsWith(location.origin + "/console/"));
    if (c) { c.navigate(url).catch(() => {}); return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
