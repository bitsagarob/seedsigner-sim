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
 * crossOriginIsolated, without which the sim silently dies, since the worker
 * blocks inside SeedSigner's controller loop and SharedArrayBuffer is the only
 * channel that can reach it.
 */
// Not bumped when the list below only grows, and that is deliberate. The cache
// is named after VERSION and activate deletes every other one, so a bump throws
// away the immutable half too -- twenty megabytes of Pyodide re-downloaded by
// everybody who already had it, to pick up a few kilobytes of script. A changed
// file here is enough to install this worker again, and install adds the new
// entries to the cache that is already there. Bump it when something cached
// must be thrown away, not when something new is added.
const VERSION = "sim-v7";
const CACHE = "seedsignersim-" + VERSION;

// Small enough to fetch up front so a first-run offline load still works.
//
// The analytics pair is deliberately not here and never will be. /mt.js and
// /mt.php live at the site root, outside this worker's scope, so nothing below
// can reach them anyway -- which is the reason they were put there: a cached
// tracker is a stale tracker, and a cached beacon is a visit that either never
// happened or happened again days later.
const SHELL = [
  "./",
  "./index.html",
  "./wallet.html",
  "./wallet-worker.js",
  "./wallet-camera.js",
  "./wallet-cards.js",
  "./wallet-coordinator.js",
  "./wallet-track.js",
  "./seedsigner-device.js",
  // The boot itself, and the placeholder that stands in for DOOM. Both are
  // small and both are on every load. What is deliberately not here is DOOM:
  // doom.js, doom.wasm and the WAD are about ten megabytes gzipped, and
  // precaching them would charge every visitor for a game before they had asked
  // for one -- including the ones who arrived at ?wallet and will never see it.
  // Nothing big is in this list and never has been, for exactly that reason:
  // Pyodide and the wallet zip are not here either. They are fetched when they
  // are actually wanted, and kept by the rules below.
  "./doom-boot.js",
  "./doom-run.js",
  "./jsQR.js",
  // The four the page loads once somebody does more than look at the device:
  // the coordinator beside it, the tutorial that drives it, and the two codecs
  // both of those need to put a QR on the screen and read one back. They are
  // fetched on use and so were cached on use, which is not the same thing --
  // the rule below is network-first, so a script nobody had reached yet was a
  // script that failed on the first bad connection. 127KB against a boot of
  // twenty megabytes, and it makes the offline claim on the page true rather
  // than nearly true.
  "./signet-coordinator.js",
  "./wallet-tutorial.js",
  "./qr-encode.js",
  "./ur-decode.js",
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
//
// The WAD belongs here for the opposite reason to the one that kept it out of
// the shell above: it is a published Freedoom release that never changes, and
// network-first would re-download twenty-eight megabytes on every visit to a
// page that is meant to be playable in a second. doom.wasm goes with it. The
// glue in doom.js does not: it is built here and moves when the build does.
const IMMUTABLE = /\/(pyodide\/|fonts\/|icon-|apple-touch-icon|doom\.wasm|freedoom\d*\.wad)/;

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
