# SeedSigner simulator

The real [SeedSigner](https://seedsigner.com) firmware — the actual Python from the
device — running in a browser tab. Its screen is a canvas, its buttons are your
keyboard, and its camera is your webcam.

> **This is a simulator, not a wallet.**
> Everything it does happens in a browser tab, on a general-purpose computer, with
> no secure element and no air gap. Treat every key it shows you as public.
> **Never enter a seed phrase you rely on.** Use a public test seed instead: a
> throwaway phrase that holds no bitcoin and never will, published so that people
> have something safe to test with.

![The simulator running the wallet's home screen](docs/img/device.png)

A SeedSigner is an open-source Bitcoin signing device you build yourself. This is
the software off one of them, running as a web page: not a video of it, and not a
lookalike rebuilt to resemble it, but the same Python, drawing the same screens
and doing the same work.

That is an easy thing to say and a hard thing to believe, which is why most of
what follows is about making it checkable rather than asking you to take it on
trust. The copy here is tied to one specific published release of the device
software, rather than to whatever happened to be newest. Anyone can rebuild the
file this site serves and get one that is identical, byte for byte — and a machine
now redoes that on every change, on a computer that has never seen the project
before. The behaviour, meanwhile, is checked by the tests the device's own authors
wrote, and not only by tests we wrote ourselves about somebody else's code.

## Try it

```sh
git clone https://github.com/bitsagarob/seedsigner-sim.git
cd seedsigner-sim
./build/fetch-assets.sh              # Pyodide, pinned and hash-checked (~26 MB, once)
./build/build-wallet-zip.sh          # builds wallet.zip from the pinned commit
python3 test/serve.py --port 8770 src/web src/shims build/out
```

Then open <http://127.0.0.1:8770/>.

Neither fetched artefact is committed: Pyodide is 26 MB of someone else's release,
and `wallet.zip` is built rather than shipped so that what you run is provably the
pinned commit and not something a maintainer pasted in. Both steps verify what they
download before using it.

The two headers that server sends are not optional — without cross-origin isolation
the page cannot use `SharedArrayBuffer` and the wallet never starts.
[docs/SELF-HOSTING.md](docs/SELF-HOSTING.md) is the short version. After the first
load the page runs offline.

Arrow keys move, Enter selects, `1` `2` `3` are the three side buttons. You can
also click the buttons on the device.

## What makes it different

**It is the firmware, not a re-creation.** There is no reimplementation of
SeedSigner here — no rewritten screens, no mock flows. `wallet.zip` contains the
upstream Python tree, and the wallet's own `Controller.start()` is what runs. The
menus, the seed handling, the PSBT parsing, the QR encoders: all theirs, unmodified.

**You can check that for yourself.** The wallet is pinned in
[`UPSTREAM`](UPSTREAM) to a single commit of
[3rdIteration/seedsigner](https://github.com/3rdIteration/seedsigner), the release
tag `SeSi-0.8.7+ShSi-B11` (`662d9dba2327eb77d6924ae9bd62d4902bf24634`), and
`build/build-wallet-zip.sh` rebuilds `wallet.zip` from it reproducibly — fixed
timestamps, fixed order, no build host anywhere in the output. So you can rebuild
it yourself and compare the sha256 against the `wallet.zip` a page just served
you. If they match, what you ran was the pinned upstream tree plus its pinned
dependencies plus this repository's simulated smartcard package, and nothing else.

[![reproducible-build](https://github.com/bitsagarob/seedsigner-sim/actions/workflows/reproducible-build.yml/badge.svg)](https://github.com/bitsagarob/seedsigner-sim/actions/workflows/reproducible-build.yml)

That rebuild also runs in CI, on every push, on a runner that has never seen this
repository before and shares no cache with anything else — and it fails if either
hash in [`UPSTREAM`](UPSTREAM) no longer comes back out. So the numbers you are
being asked to compare against are re-derived by a machine rather than by
whoever last remembered to.

A released tag rather than a branch tip, for two reasons: a tip can be rebased out
from under the pin, leaving a sha nobody can fetch any more, and that tag is the
one the official pi0-smartcard device image is built from — so the code here and
the code on a physical device are the same code.

If the zip hashes differ but the *contents* hash printed alongside matches, the
two builds hold the same files and differ only in compression — some
distributions ship zlib-ng, whose deflate output is not byte-identical to stock
zlib. That is a packaging difference, not a code difference, and
`wallet.zip.manifest` lists a sha256 per file so you can find out which it is.

Nothing here patches the wallet: the four places it reaches for hardware are
replaced from the outside, by the modules in [`src/shims/`](src/shims). That is
the whole point of the build script, and it is the one claim worth checking
rather than believing.

**Upstream's own tests run here, against our dependency versions.** SeedSigner
ships a 22,000-line test suite. CI clones it from the same pinned commit the
wallet is built from and runs it
([`upstream-tests.yml`](.github/workflows/upstream-tests.yml)) — nothing is
vendored, no test file lives in this repository, and none of it goes anywhere
near `wallet.zip`. Two things make that worth doing. The wallet's behaviour is
checked by the tests written by the people who wrote the wallet, rather than only
by tests we invented for code we did not write. And because
`build/build-wallet-zip.sh` chooses its own versions of embit, ecdsa, pysatochip
and the rest, this is what catches those pins drifting away from what upstream's
code actually expects: the day they do, one of upstream's own tests goes red and
names the behaviour that changed.

To be exact about what that covers, since a vague claim here would be worse than
none: one upstream file of 50 tests is not collected. It is
`tests/test_smartcard_hardware.py`, which its own docstring calls
hardware-in-the-loop and which wants a physical card reader and a blank JavaCard,
so every one of its tests skips itself on a machine that has neither. The other
949 run. A few of those skip themselves where something optional is missing — the
translation files this build does not ship, for instance — and the job prints
every skip with its reason rather than folding it into a total.

**The webcam really is the camera.** Point it at a SeedQR and the wallet loads the
seed — the same `DecodeQR`, the same SeedQR / CompactSeedQR / PSBT / UR parsing the
device does. Only the decoder underneath it is the browser's, because SeedSigner
reads QR codes with pyzbar and there is no zbar in WebAssembly.

**Nothing leaves the tab.** The page's content security policy allows no outbound
connections, and there is no backend to send anything to. Once loaded, it runs with
the network off.

## What works, and what does not

Working: the full menu tree, seed loading by QR or by hand, passphrases, xpub
export, PSBT loading and signing, SeedQR backup, settings, and every screen that
draws a QR.

The smartcard tray holds three cards, and each can be a **SeedKeeper** or a
**Satochip** — you choose before you put it in, and SeedKeeper is the default
because that is the card the device ships with. Either can be initialised with a
PIN and will check that PIN when asked.

- **A SeedKeeper** stores labelled secrets and gives them back under the export
  rights they were stored with. The two flows the product exists for both work
  end to end through the wallet's own screens: **Backup seed → To SeedKeeper**
  saves a seed onto the card, and **Seeds → Load a seed → From SeedKeeper** reads
  one back off it.
- **A Satochip** takes a seed and derives from it. The card holds a real BIP32
  master key and a real authentikey, and pysatochip verifies every answer it
  signs.

Not working, and worth knowing before you go looking:

- **The wallet's own "Initialise with Seed" screen**, on the Satochip side. The
  card takes the seed and derives from it correctly, and the tray says so, but the
  screen that asked for it reports a failure: at the pinned tag
  `ToolsSatochipImportSeedView` unpacks `card_bip32_import_seed()`'s return value
  into three, and on this backend that method returns one thing — the
  authentikey. So a successful import is what raises. That is upstream's bug,
  still present on their `dev` tip, and nothing here works around it. Reading the
  seed back off the card afterwards works.
- **Moving a secret from one SeedKeeper to another.** That is an *encrypted*
  export, done under a session key negotiated with the second card's public key,
  and there is no second card in the reader to negotiate with. It comes back "not
  supported" rather than being quietly done in the clear.
- **Card signing.** PSBT and message signing on the card, PIN change and unblock,
  2FA, factory reset and the card-management screens all come back "not
  supported" — see [THIRD-PARTY.md](THIRD-PARTY.md) for the GlobalPlatform half.
- **A card that remembers.** Card state is in memory only, so a page reload gives
  you factory-fresh cards. Deliberately: nothing you do here should outlive
  the tab.
- **microSD.** There is no card slot to emulate, so anything routed through one —
  settings surviving a reload, firmware update flows — does not happen. Settings
  live in an in-memory filesystem and reset when you reload the page.
- **Anything drawn from a background thread.** This environment has no threads, so
  animations do not animate: no spinner, no scrolling long text, no pulsing warning
  border. The two that carry information — the live camera preview and the
  animated-QR display — are pumped by hand instead. Features that do their work in
  a thread, such as brute-force address verification, will not complete.
- **Timing-based behaviour.** No wipe timer, no screensaver, no battery readings.
- **Real security properties.** Obviously. A browser tab is not an air gap, and
  Pyodide's filesystem is not a secure element.

## How it works

The wallet's Python runs under [Pyodide](https://pyodide.org) (CPython compiled to
WebAssembly) inside a Web Worker. Four hardware seams are replaced:

| Seam | Replaced by | Why |
| --- | --- | --- |
| Display | [`src/shims/browser_display.py`](src/shims/browser_display.py) | Swaps the panel driver underneath SeedSigner's own unmodified `Renderer`; raw RGB frames go to a canvas. |
| Buttons | patched in [`src/web/wallet-worker.js`](src/web/wallet-worker.js) | The worker is blocked inside the wallet's main loop and can never answer a `postMessage`, so keys cross on a `SharedArrayBuffer` and wake it with `Atomics`. |
| Camera + QR | [`src/shims/browser_camera.py`](src/shims/browser_camera.py) + [`src/web/wallet-camera.js`](src/web/wallet-camera.js) | pyzbar is a C library with no WebAssembly build, so the browser decodes and hands the bytes to SeedSigner's unmodified decoder. |
| Smartcard | [`src/smartcard/`](src/smartcard) | Browsers have no smartcard API, so simulated SeedKeeper and Satochip cards answer real APDUs and pysatochip runs against them unchanged. |

A fifth module, [`src/shims/browser_qr.py`](src/shims/browser_qr.py), makes the
screens that *display* a QR draw one: their drawing lives in a thread this
environment cannot run.

That single constraint — a worker that is permanently blocked and cannot service a
message — explains most of the architecture, including why keys, camera frames and
the card tray all travel over shared memory.
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) is the long version, including the
data flow from webcam to decoded seed and why the browser's `BarcodeDetector` is
deliberately never trusted to produce a payload.

## Self-hosting

Two things trip everyone up, both covered in
[docs/SELF-HOSTING.md](docs/SELF-HOSTING.md):

1. The page **must** be served with `Cross-Origin-Opener-Policy: same-origin` and
   `Cross-Origin-Embedder-Policy: require-corp`. Without them `SharedArrayBuffer`
   does not exist and the wallet never starts.
2. The camera needs a secure context: `https`, or `localhost`. Over plain `http` on
   a LAN address there is simply no camera API to ask.

## Development

[CONTRIBUTING.md](CONTRIBUTING.md) covers running it locally, the tests and the
one rule about comments; [test/README.md](test/README.md) explains what each test
proves. `python3 test/run.py` builds what is missing and runs the lot in a real
browser, including a scan against a fake camera pointed at a blank wall that fails
if the wallet reports any seed at all.

Two things to know before debugging: `?debug=1` turns on tracing of every screen,
thread and keypress (the tests read it, and so should you when something stalls),
and the page must be served with the two isolation headers or nothing starts.

## Licence

MIT — see [LICENSE](LICENSE).

Almost none of the code that runs here was written for this repository: the wallet
is upstream SeedSigner (MIT, Copyright (c) 2021 SeedSigner), the interpreter is
Pyodide, the QR decoder is jsQR, and everything the wallet imports is somebody
else's library. [THIRD-PARTY.md](THIRD-PARTY.md) lists all of it — version,
origin, licence, and how to check each one.

## Credits

- [SeedSigner](https://github.com/SeedSigner/seedsigner) — the device and the
  firmware this runs. All of the interesting parts are theirs.
- [3rdIteration/seedsigner](https://github.com/3rdIteration/seedsigner) — the fork
  this is pinned to, which adds the smartcard support the simulated cards
  answer.
- [Pyodide](https://pyodide.org) and [jsQR](https://github.com/cozmo/jsQR) — the two
  pieces of other people's work that make the browser side possible.

This is an independent project. It is not affiliated with or endorsed by the
SeedSigner project, and running it proves nothing about a real device.
