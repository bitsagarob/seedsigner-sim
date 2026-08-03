/*
 * Offline cache for the SeedSigner simulator.
 *
 * The page already claims you can pull the plug once it has loaded; this makes
 * that true across a restart, and takes the 26MB Pyodide download off every
 * visit after the first.
 *
 * Two rules, because the payload splits cleanly in two:
 *   - Pyodide, wallet.zip, fonts and icons are large and effectively immutable.
 *     Cache-first, fetched once and kept until VERSION changes.
 *   - The pages and our own scripts change every deploy. Network-first, so a
 *     deploy is visible on the next load rather than whenever the cache expires.
 *
 * Nothing cross-origin and nothing but GET is touched. Same-origin cached
 * responses keep their headers, so COOP/COEP survive and the page stays
 * crossOriginIsolated — without which the sim silently dies, since the worker
 * blocks inside SeedSigner's controller loop and SharedArrayBuffer is the only
 * channel that can reach it.
 */
const VERSION = "sim-v3";
const CACHE = "seedsignersim-" + VERSION;

// Small enough to fetch up front so a first-run offline load still works.
const SHELL = [
  "./",
  "./index.html",
  "./wallet.html",
  "./wallet-worker.js",
  "./wallet-camera.js",
  "./wallet-cards.js",
  "./seedsigner-device.js",
  "./jsQR.js",
  "./browser_camera.py",
  "./browser_qr.py",
  "./browser_display.py",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png",
  "./apple-touch-icon.png",
];

// Genuinely immutable things only. wallet.zip used to be listed here and is
// not: it is rebuilt whenever the Python side changes, and cache-first with
// no revalidation meant a returning visitor kept the old wallet forever while
// getting fresh JS around it -- the worst version, mismatched halves.
const IMMUTABLE = /\/(pyodide\/|fonts\/|icon-|apple-touch-icon)/;

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // One at a time: addAll rejects the whole install if a single URL 404s,
    // and a stale entry in this list should not cost us the service worker.
    await Promise.all(SHELL.map((url) =>
      cache.add(new Request(url, { cache: "reload" })).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter((n) => n.startsWith("seedsignersim-") && n !== CACHE)
      .map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (IMMUTABLE.test(url.pathname)) {
    event.respondWith((async () => {
      const hit = await caches.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) (await caches.open(CACHE)).put(req, res.clone());
      return res;
    })());
    return;
  }

  event.respondWith((async () => {
    try {
      const res = await fetch(req);
      if (res.ok) (await caches.open(CACHE)).put(req, res.clone());
      return res;
    } catch (err) {
      const hit = await caches.match(req);
      if (hit) return hit;
      throw err;
    }
  })());
});
