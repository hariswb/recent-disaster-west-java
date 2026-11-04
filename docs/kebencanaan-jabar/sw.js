// Network first, always: the dashboard must show the latest data. The cache is only a fallback
// so the installed app still opens (with the last loaded data and its timestamp) without signal.
// Cloudflare tells browsers to keep CSS/JS for 4 hours but the HTML for 10 minutes, so after a deploy
// a plain fetch can pair new HTML with old CSS/JS. Every request is revalidated with the server instead
// ("no-cache": a 304 when unchanged), and the install skips the HTTP cache entirely ("reload").
const CACHE = "kebencanaan-jabar-v4";
const SHELL = ["./", "index.html", "style.css", "manifest.webmanifest", "js/app.js", "js/model.js", "js/ui.js",
  "js/share.js", "js/views/overview.js", "js/views/topic.js", "js/views/cases.js", "js/views/case.js",
  "js/views/map.js", "js/views/recap.js", "../analytics.js", "../fonts/atkinson-next-latin.woff2", "../fonts/jakarta-latin.woff2",
  "icons/icon-192.png", "data/incidents.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  // Same-origin GETs only; map tiles, CDN libraries and analytics go straight to the network.
  if (req.method !== "GET" || url.origin !== location.origin) return;
  e.respondWith(fetch(req, { cache: "no-cache" }).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      // Data is fetched with ?t=<now>; store it under the bare URL so there is one copy.
      const key = url.pathname.endsWith(".json") ? url.origin + url.pathname : req;
      caches.open(CACHE).then((c) => c.put(key, copy));
    }
    return res;
  }).catch(() => caches.match(req, { ignoreSearch: true })
    .then((hit) => hit || (req.mode === "navigate" ? caches.match("index.html") : Response.error()))));
});
