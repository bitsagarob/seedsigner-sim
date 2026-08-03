# Contributing

Bug reports, fixes and honest documentation are all welcome. This is a small
repository with an unusual constraint or two; the notes below should save you the
hour it takes to discover them.

## Running it

```sh
./build/fetch-assets.sh                                  # Pyodide, pinned and hash-checked
./build/build-wallet-zip.sh                              # wallet.zip from the commit in UPSTREAM
python3 test/serve.py --port 8770 src/web src/shims build/out
```

Open <http://127.0.0.1:8770/>. The server overlays those three directories into one
document root, so there is no staging copy to keep in sync: edit a file in
`src/web` and reload. The first two steps take a few minutes once and are cached
afterwards; `docs/SELF-HOSTING.md` explains what ends up where.

**The gotcha, before you lose an afternoon to it:** the page must be served with
`Cross-Origin-Opener-Policy: same-origin` and
`Cross-Origin-Embedder-Policy: require-corp`. Without them the document is not
cross-origin isolated, `SharedArrayBuffer` does not exist, and every input channel
into the wallet is gone; the page says so instead of starting. `python3 -m
http.server` will not do; `test/serve.py` exists for exactly this.

The camera additionally needs a secure context, so use `127.0.0.1` or real https.
A LAN IP over plain http gives you a working wallet with no camera at all.

## Finding out what it is doing

Add `?debug=1` to the URL (there is a link on the page). The wallet then narrates
itself to the console: every `View.run`, every screen `display()`, every thread the
shim dropped or ran inline, every keypress and the decoder the camera settled on.
Off by default, because it is noise for anyone who is not debugging; `js_log`
builds nothing and posts nothing without the flag.

That trace is also the assertion surface for the tests, so keep it accurate. If you
add a shim that swallows something, say so in the log.

## Layout

```
src/web/       the page, the worker, the two shared-buffer channels, the device art
src/shims/     the Python that replaces SeedSigner's hardware (display, camera, QR)
src/smartcard/ a simulated Satochip that answers real APDUs, in pyscard's place
build/         fetches Pyodide, and rebuilds wallet.zip from the pinned commit
test/          the runner, the server, the fake-camera videos, the browser tests
docs/          architecture and self-hosting
UPSTREAM       the commit the wallet is pinned to
```

Read `docs/ARCHITECTURE.md` before changing anything in `src/`. The important part
is that the worker is permanently blocked inside SeedSigner's main loop, so it can
never receive a `postMessage`, which is why keys, camera frames and the card tray
all cross on shared memory, and why a change that "just posts a message to the
worker" cannot work.

## The rule that matters

**Do not patch the wallet.** `wallet.zip` is the upstream tree at the pinned
commit, rebuilt by a script anyone can run and diff. If SeedSigner reaches for
something this environment does not have, replace it *from the outside*, in a shim,
and leave a comment saying what it stands in for. The verifiability of the build is
the most interesting property this project has and it is trivially easy to lose.

If you genuinely need a newer upstream, change `UPSTREAM` in its own commit, with
the reason, and rebuild.

## Tests

```sh
pip install playwright==1.47.0 && playwright install chromium
python3 test/run.py            # builds what is missing, serves it, runs everything
python3 test/run.py scan       # or a subset, by substring on the step name
```

[test/README.md](test/README.md) says what each test proves; read it before adding
one. The short version: they drive a real browser, and they assert on the wallet's
own `?debug=1` narration rather than on pixels, because reaching
`SeedFinalizeScreen` with a known fingerprint is a statement about what the Python
side actually decoded. A screenshot is proof of nothing on its own.

Both CI workflows run on every pull request: the suite above, and `leak_scan.py`,
which fails the build if a tracked file names anybody's infrastructure. Run
`python3 test/leak_scan.py` before pushing; it takes a second.

If you touch the scan path, `test_scan_native.py` is the one to watch. It points
the camera at a blank wall, forces a stub `BarcodeDetector` to claim a QR on every
frame, and fails if the wallet loads any seed at all. An earlier version reached a
real-looking fingerprint from pure garbage there. A wrong seed presented as a right
one is the worst thing this project could do.

## Style

- **Comments say why, not what.** The code already says what. Every non-obvious
  line here exists because something in the browser, in Pyodide or in SeedSigner
  made it necessary, and that reason is the thing a reader cannot recover. Look at
  `src/web/wallet-camera.js` for the register to aim for.
- **No build step and no dependencies on the page.** Plain ES5-ish JavaScript,
  loaded with script tags, in a page whose CSP allows nothing external. If you find
  yourself wanting a bundler, that is a sign to write less JavaScript.
- **Keep both halves of a shared buffer in one file.** `wallet-camera.js` and
  `wallet-cards.js` each contain the page half and the worker half because they
  must agree byte-for-byte on the layout, and a layout written down twice drifts.
- **Python runs under Pyodide** (CPython 3.12, WebAssembly): no threads, no
  processes, no OpenSSL bindings, no native extensions. If your change needs one of
  those, it needs a different design.
- **Keep it honest.** Anything user-facing that could read as "this is a wallet"
  is a bug. It is a simulator; the pages say so, and they should keep saying so.

## Pull requests

One change per PR, with the reasoning in the message rather than only in the diff.
If it fixes something that used to be broken, say what was broken and how you
reproduced it, ideally as a test that fails without the change.

By contributing you agree that your work is licensed under the MIT licence in
[LICENSE](LICENSE).
