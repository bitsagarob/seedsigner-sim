# Self-hosting

Everything here is static files. There is no backend, no database and nothing
running at request time, but there are two headers, and without them the page
loads and then does nothing.

## The one thing that breaks every first attempt

The page **must** be served with both of these:

```http
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

They are what make the document *cross-origin isolated*, which is what makes
`SharedArrayBuffer` constructible. Every input the wallet has (keypresses, camera
frames, the card tray) crosses into the worker on shared memory, because the
worker is blocked inside SeedSigner's main loop and can never answer a
`postMessage` (see
[ARCHITECTURE.md](ARCHITECTURE.md#the-constraint-everything-follows-from)).
No shared memory, no wallet.

`wallet.html` checks for this before it starts the worker and puts a message on the
page rather than failing silently. If you see

> this page needs cross-origin isolation, which the server is not sending

it is the headers, always. `python3 -m http.server` does not send them, which is
why this repository ships its own server.

The second rule is smaller but just as confusing when you hit it: **the camera
needs a secure context.** That means `https://`, or `http://localhost` /
`http://127.0.0.1`. On a plain `http://` LAN address there is no
`navigator.mediaDevices` to ask, so scanning fails with "no camera API here; needs
https or localhost". Everything else still works.

## Getting the pieces

Two of the three pieces are not in the repository, on purpose: a 26 MB WebAssembly
blob and a wallet you are being asked to trust are both things better fetched and
verified than committed.

```sh
./build/fetch-assets.sh       # Pyodide 0.26.4 -> src/web/pyodide/, sha256-checked
./build/build-wallet-zip.sh   # -> build/out/wallet.zip, built from the pinned commit
```

`fetch-assets.sh --check` re-verifies what is already on disk, and
`sha256sum -c build/checksums.txt` covers the third-party files that *are*
committed (jsQR). Both scripts explain their trust chain in their own header
comments; they are worth a read before you run them.

## Running it locally

`test/serve.py` overlays several directories into one document root, so a checkout
is served without being copied anywhere first:

```sh
python3 test/serve.py --port 8770 src/web src/shims build/out
```

Then open <http://127.0.0.1:8770/>. It binds to `127.0.0.1` by default (`--host` to
change that) and sends exactly what the page needs:

```http
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
Cache-Control: no-store
```

It also serves `.wasm` as `application/wasm`, which Pyodide's streaming
instantiation insists on. `127.0.0.1` is a secure context, so the camera works
there without a certificate.

## Deploying it

A real web server has one document root, so flatten the same three sources into one
directory:

```sh
mkdir -p /srv/seedsigner-sim
cp -r src/web/.  /srv/seedsigner-sim/     # page, scripts, icons, and pyodide/
cp src/shims/browser_*.py /srv/seedsigner-sim/
cp build/out/wallet.zip   /srv/seedsigner-sim/
```

What ends up there, and why each piece has to be exactly where it is (the page,
the worker and the shims all fetch each other by relative path):

| In the served root | From | Notes |
| --- | --- | --- |
| `index.html`, `wallet.html` | `src/web/` | landing page and the wallet itself |
| `wallet-worker.js`, `wallet-camera.js`, `wallet-cards.js`, `seedsigner-device.js` | `src/web/` | |
| `jsQR.js` | `src/web/` | must be same-origin; a CDN is refused by both COEP and the page's CSP |
| `sw.js`, `manifest.json`, `icon-*.png`, `apple-touch-icon.png` | `src/web/` | offline cache and PWA install; optional, the wallet runs without them |
| `pyodide/` | `fetch-assets.sh` | ~26 MB: the runtime plus the wheels for Pillow, pycryptodome and cryptography |
| `browser_display.py`, `browser_camera.py`, `browser_qr.py` | `src/shims/` | fetched at boot and written into Pyodide's filesystem |
| `wallet.zip` | `build/out/` | ~4 MB: the pinned `seedsigner` tree, its pure-Python dependencies, and the simulated `smartcard` package |

The shims sit next to the page rather than inside `wallet.zip` deliberately: it
keeps the zip exactly what the build script produced, with the seams visibly
outside it.

## nginx

```nginx
server {
    listen 443 ssl;
    server_name sim.example.org;

    root /srv/seedsigner-sim;
    index index.html;

    # Both are required, on every response -- the worker, the wasm and
    # wallet.zip included. Hence `always`.
    add_header Cross-Origin-Opener-Policy   same-origin  always;
    add_header Cross-Origin-Embedder-Policy require-corp always;
    add_header Cross-Origin-Resource-Policy same-origin  always;
}
```

Two nginx-specific traps:

- **`add_header` does not inherit into a nested block that has its own.** If any
  `location` in this server adds a header of its own, it drops every `add_header`
  from the parent (including these two), and the page silently loses isolation.
  Repeat them in that location.
- **`.wasm` must be served as `application/wasm`.** Recent `mime.types` include it;
  older ones do not, and Pyodide's streaming compilation refuses the wrong type.
  Check with `curl -I …/pyodide/pyodide.asm.wasm`.

## Caddy

```caddyfile
sim.example.org {
    root * /srv/seedsigner-sim
    header {
        Cross-Origin-Opener-Policy   same-origin
        Cross-Origin-Embedder-Policy require-corp
        Cross-Origin-Resource-Policy same-origin
    }
    file_server
}
```

## Somewhere that cannot set headers

Static hosts that do not let you set response headers (GitHub Pages among them)
cannot serve this page as-is. There are service-worker shims that re-inject
COOP/COEP from inside the browser; this repository does not ship one, and its own
`sw.js` is an offline cache rather than a header trick. Use a host you can
configure.

## Checking it worked

From the outside, before you even open a browser:

```sh
curl -sI https://sim.example.org/wallet.html | grep -i cross-origin
```

In the page's console:

```js
crossOriginIsolated   // must be true
```

Then on the page itself: the status line under the device clears when the wallet
draws its first frame. Add `?debug=1` to the URL (there is a link on the page),
and the console narrates every screen, thread and keypress, plus which QR decoder
the camera settled on.

The test suite can be pointed at a deployment rather than a local checkout, which
is a stronger check than any of the above:

```sh
SIM_URL=https://sim.example.org python3 test/run.py
```

## When it does not work

| What you see | What it is |
| --- | --- |
| "this page needs cross-origin isolation…" | COOP/COEP missing, or dropped by a proxy or a nested `location` |
| Stuck on "loading python…" | `pyodide/` is incomplete or 404ing; check the network tab |
| Stuck on "unpacking wallet…" | `wallet.zip` missing or truncated |
| Wallet draws, but scanning says "no camera API here" | not a secure context: use https or localhost |
| Camera permission prompt never appears | the wallet only opens the camera when you enter a scan screen; that is intended |
| Old wallet, new page, weird errors | a stale service-worker cache; bump `VERSION` in `sw.js` and reload |

## Verifying what you are serving

The point of the pin is that nobody has to take "it is the real firmware" on trust.
Anyone can rebuild and compare:

```sh
./build/build-wallet-zip.sh
sha256sum build/out/wallet.zip
curl -s https://sim.example.org/wallet.zip | sha256sum   # must be the same
```

The build is reproducible (fixed timestamps, fixed entry order, nothing about the
build host in the output), so the hashes match or something differs. If they
differ, the script also writes `wallet.zip.manifest`, a `(sha256, path)` line per
file in the zip: diffing two manifests says *which* files differ, and rules out the
boring answer of two zlib versions compressing the same bytes differently.

If you host this for other people, keeping `UPSTREAM` and the served `wallet.zip`
in step is most of your obligation to them: that, and not quietly editing the
`seedsigner` tree inside the zip, because the seams are outside it precisely so
that nobody has to.

## A note on what you are hosting

It is a simulator. Anyone who lands on it should be able to tell that within a few
seconds; both pages here say so, and the wallet page says it again in its "how
this works" panel. If you re-skin it, keep that. A page that runs real SeedSigner
firmware and *looks* like a real wallet is exactly the thing worth not shipping.
