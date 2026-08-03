# SeedSigner simulator

[![try it live](https://img.shields.io/badge/try%20it-live-f7931a?style=flat-square)](https://bitsaga.be/seedsigner-simulator/)
[![smartcard fork](https://img.shields.io/badge/smartcard%20fork-SeSi--0.8.7%2BShSi--B11-blue?style=flat-square)](UPSTREAM)
[![stock](https://img.shields.io/badge/stock-0.8.7-blue?style=flat-square)](UPSTREAM)
[![reproducible-build](https://img.shields.io/github/actions/workflow/status/bitsagarob/seedsigner-sim/reproducible-build.yml?branch=main&label=reproducible%20build&style=flat-square)](https://github.com/bitsagarob/seedsigner-sim/actions/workflows/reproducible-build.yml)
[![tests](https://img.shields.io/github/actions/workflow/status/bitsagarob/seedsigner-sim/test.yml?branch=main&label=tests&style=flat-square)](https://github.com/bitsagarob/seedsigner-sim/actions/workflows/test.yml)
[![upstream tests](https://img.shields.io/github/actions/workflow/status/bitsagarob/seedsigner-sim/upstream-tests.yml?branch=main&label=upstream%20tests&style=flat-square)](https://github.com/bitsagarob/seedsigner-sim/actions/workflows/upstream-tests.yml)
[![release](https://img.shields.io/github/v/release/bitsagarob/seedsigner-sim?style=flat-square)](https://github.com/bitsagarob/seedsigner-sim/releases/latest)
[![licence](https://img.shields.io/github/license/bitsagarob/seedsigner-sim?style=flat-square)](LICENSE)

Real [SeedSigner](https://seedsigner.com) device firmware, the actual Python off
the device, running in a browser tab. Its screen is a canvas, its buttons are
your keyboard, and its camera is your webcam.

Two firmwares are built, and the page says which one it is running and switches
between them: **stock SeedSigner**, which is what a plain SeedSigner runs, and
the **3rdIteration smartcard fork**, a third party fork that adds SeedKeeper and
Satochip support and is what this page runs by default.

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
trust. Each firmware here is tied to one specific published release of the device
software (a git tag, not a moving branch), rather than to whatever happened to be
newest. Anyone can rebuild the file this site serves and get one that is
identical, byte for byte (a reproducible build, compared by sha256 hash), and a
machine now redoes that on every change on a computer that has never seen the
project before (a CI job on a clean GitHub runner, which also has GitHub sign a
statement about what it built: a build provenance attestation). The behaviour,
meanwhile, is checked by the tests the device's own authors wrote (upstream's
pytest suite, run against the dependency versions this repository pins), and not
only by tests we wrote ourselves about somebody else's code.

## Try it

```sh
git clone https://github.com/bitsagarob/seedsigner-sim.git
cd seedsigner-sim
./build/fetch-assets.sh              # Pyodide, pinned and hash-checked (~26 MB, once)
./build/build-wallet-zip.sh smartcard   # wallet-smartcard.zip, from the pinned commit
./build/build-wallet-zip.sh stock       # wallet-stock.zip, from the pinned commit
python3 test/serve.py --port 8770 src/web src/shims build/out
```

Then open <http://127.0.0.1:8770/>.

Neither fetched artefact is committed: Pyodide is 26 MB of someone else's release,
and the wallet zips are built rather than shipped so that what you run is provably
the pinned commit and not something a maintainer pasted in. Both steps verify what
they download before using it.

The two headers that server sends are not optional: without cross-origin isolation
the page cannot use `SharedArrayBuffer` and the wallet never starts.
[docs/SELF-HOSTING.md](docs/SELF-HOSTING.md) is the short version. After the first
load the page runs offline.

Arrow keys move, Enter selects, `1` `2` `3` are the three side buttons. You can
also press the buttons drawn on the device. The screen is not one of them: a
SeedSigner has no touchscreen, and neither has this.

On a phone, **Fill the screen** under the device gives it the whole viewport and
lays it along the phone's long side, which is the only way a landscape shell
gets keys a thumb can hit: about 46 pixels instead of 21. Held upright it is
turned across the screen, so turning the phone is right whether or not rotation
is locked. The same control, or Escape, comes back out.

The page opens on the smartcard fork. The **Firmware** control under the device
switches to stock and back, and `?firmware=stock` is a link straight to it.

Running your own SeedSigner fork in it takes no edit to `UPSTREAM`: `SS_REPO` and
`SS_COMMIT` override the pin for one build, and
[CONTRIBUTING.md](CONTRIBUTING.md#running-your-own-fork-of-seedsigner-in-it) has
the worked example and what the resulting hashes mean.

## What it is, and how to verify it

- **It is the firmware, not a re-creation.** `wallet-<firmware>.zip` holds that
  firmware's upstream Python tree and the wallet's own `Controller.start()` runs
  it. Menus, seed handling, PSBT parsing, QR encoders: all theirs, unmodified.
- **Nothing patches the wallet.** The places it reaches for hardware are replaced
  from the outside, by the modules in [`src/shims/`](src/shims): four of them in
  the fork, three in stock, which has no cards to reach for. That is the one
  claim worth checking rather than believing.
- **What is configured is configured, not patched.** The simulator comes up on
  Testnet, where SeedSigner's own default is Mainnet, because nothing that runs
  in a browser tab should start out pointed at real coins. That is a value
  written into the `settings.json` the device reads at boot, the way a configured
  device would have it, and Mainnet is still in Settings > Advanced > Bitcoin
  network exactly as on hardware.
- **Mainnet really works, which is exactly why it is dangerous.** On Mainnet the
  wallet exports the right mainnet account keys and really signs a mainnet
  transaction: `test/test_mainnet.py` checks both against BIP32, BIP143 and
  ECDSA worked out from the specifications, with no coins, no network and
  nothing broadcast. So a mainnet key derived in a browser tab is a real key,
  and should be treated as public from the moment it appears there.
- **Pinned to a release, not a branch tip.** [`UPSTREAM`](UPSTREAM) has a section
  per firmware. Stock is SeedSigner's own tag `0.8.7` (`e0a80d4b…`). The fork is
  3rdIteration's `SeSi-0.8.7+ShSi-B11` (`662d9dba…`), which is also the tag the
  official pi0-smartcard device image is built from, so the fork here and that
  physical device run the same code; stock makes no such claim, because there is
  no such image. A branch tip can be rebased out from under a pin; a tag cannot.
  Where that image and `requirements.txt` disagree, the image wins: pysatochip is
  built from the GitHub tag the buildroot recipe names, not from the PyPI version
  a file the device deletes asks for. The two differ, and the difference showed
  up here as a card failure that does not exist on hardware.
- **You can rebuild either and compare.** `build/build-wallet-zip.sh` reproduces
  a wallet zip byte for byte: fixed timestamps, fixed order, no build host in the
  output. If your hash matches the file a page served you, what you ran was that
  firmware's pin, its pinned dependencies and this repository's stand-ins, and
  nothing else.
- **A machine re-derives both hashes on every push**, on a runner that has never
  seen this repository and shares no cache with anything, and GitHub signs the
  result:
  `gh attestation verify wallet-smartcard.zip --repo bitsagarob/seedsigner-sim`.
- **Upstream's own tests run against our pinned versions**, both firmwares, each
  against its own pin ([`upstream-tests.yml`](.github/workflows/upstream-tests.yml)):
  949 tests from the fork's 22,000-line suite, and the whole of stock's smaller
  one. Nothing is vendored and none of it goes near a wallet zip. One file of the
  fork's, 50 tests of it, is not collected: it is hardware-in-the-loop, wants a
  physical card reader, and skips itself entirely without one.
- **The webcam really is the camera.** Same `DecodeQR`, same SeedQR / CompactSeedQR
  / PSBT / UR parsing the device does. Only the decoder underneath is the
  browser's, because there is no zbar in WebAssembly.
- **The wallet has no network at all, and the page has one host.** There is no
  backend, and the content security policy names exactly one origin beyond this
  one: `signet.bitsaga.be`, the faucet and the two read-only proof endpoints of
  Bitsaga Signet. Nothing asks it anything unless you start the multisig
  tutorial below, and no part of the wallet can reach it in any case. With the
  tutorial closed, the page still runs with the network off.

> If the zip hashes differ but the **contents** hash matches, the two builds hold
> the same files and differ only in compression: some distributions ship zlib-ng.
> That is packaging, not code; the zip's `.manifest` lists a sha256 per file.

## The multisig tutorial

One button under the device: **Start multisig walkthrough**. It does a whole
2 of 3, in the page, and a visitor never navigates away and never installs
anything.

Three published BIP39 test seeds go onto the three SeedKeeper cards, one each,
with the card's real PIN ceremony every time. The three account public keys come
back off the cards, a 2 of 3 wallet is built from them, the faucet on **Bitsaga
Signet** pays its first address, and a spend is signed by two of the three cards
and confirmed on that chain. Bitsaga Signet is our own Bitcoin test network, with
a block every thirty seconds. **These are not real bitcoin. They exist only on
our test network, cannot be sold or sent to anyone, and are worth nothing.**

**Two modes, one machine.** A step is a list of actions, and an action is a
sentence saying what has to happen, the keys that make it happen, and how we know
it did. Press play and it performs the middle one, narrated; take over at any
point and you press the buttons instead, against the same evidence, so the panel
keeps pace either way. There is one description of the flow, not two.

**It runs at reading speed**, which is not the speed the wallet can go: it waits
for the sentence it has just put up to be read before the device moves, and a
step's opening paragraph gets longer than an instruction. **Pause** stops it
between actions, never inside one, and **Step** takes exactly one action and
stops again.

**The coordinator is on the page**, drawn as a phone beside the device, because
that is what it is: the thing that knows what the wallet owns and what a fee is,
which a signing device does not.
[`signet-coordinator.js`](src/web/signet-coordinator.js) builds the descriptor,
derives the addresses, builds the PSBT and puts the two signatures into a
finished transaction, all in the browser. It is a second, independent
implementation of BIP32 public derivation, sortedmulti, P2WSH, bech32 and
BIP174, and `test/test_tutorial.py` checks every value it produces against the
wallet's own embit rather than against itself.

**The QR exchange is shown rather than hidden**, with a caption per transfer
saying what moved and which way. It is also not faked past the optics: a code
the phone holds up is drawn from real modules by
[`qr-encode.js`](src/web/qr-encode.js) onto the phone's screen and read by the
wallet's own unmodified decoder from those pixels, and a code the device shows is
read back off the device's canvas the same way. **Your webcam is never involved**,
and the panel says so where you can see it.

The one thing the network does not yet offer is a way to send a transaction:
[docs/SIGNET-API.md](docs/SIGNET-API.md) has the single endpoint to add and why
it is the only one.

Worth saying once, and the tutorial says it too: holding all three keys on one
device is fine for a demo and wrong for real funds, where the point of multisig
is keys in different places and different hands.

## What works, and what does not

Everything about cards below is the smartcard fork's, because cards are what the
fork adds. Stock SeedSigner has no card code at all, so under stock there is no
card tray on the page: none of it is missing there, it was never there.

**Works, on either firmware**

- The full menu tree, seed loading by QR or by hand, passphrases, xpub export,
  PSBT loading and signing, SeedQR backup, settings, every screen that draws a QR.

**Works, on the smartcard fork**

- Three card slots. Each holds a **SeedKeeper** or a **Satochip**: your choice
  before you insert it, SeedKeeper by default, since that is the card the device
  ships with. Either takes a PIN and checks it.
- **SeedKeeper, end to end:** *Backup seed → To SeedKeeper* puts a seed on the
  card, *Seeds → Load a seed → From SeedKeeper* reads it back off.
- **A multisig descriptor onto a SeedKeeper.** *Save MultiSig Descriptor* files a
  2 of 3, 448 characters of it, on the card under the descriptor secret type a
  v2 card uses. Reading one back is upstream's problem, below.
- **Satochip:** holds a real BIP32 master key and a real authentikey, and
  pysatochip verifies every answer it signs.

**Does not, on either firmware**

- **microSD.** No slot to emulate, so settings reset on reload and the firmware
  update flows do not happen.
- **Anything drawn from a background thread.** No threads here: no spinner, no
  scrolling long text, no pulsing warning border. The two animations that carry
  information (camera preview, animated QR) are pumped by hand. Thread-based
  work such as brute-force address verification never completes.
- **Timing-based behaviour.** No wipe timer, no screensaver, no battery readings.
- **Real security properties.** A browser tab is not an air gap, and Pyodide's
  filesystem is not a secure element.

**Does not, on the smartcard fork**

- **"Initialise with Seed" on the Satochip side: upstream's bug, not ours.**
  `ToolsSatochipImportSeedView` unpacks three values from
  `card_bip32_import_seed()`, which returns one, so a *successful* import is what
  raises. Still present on their `dev` tip; nothing here works around it. The card
  is seeded regardless and reading it back works.
- **Reading a multisig descriptor back off a card: upstream's bug too.** Saving
  one works, and the card really is carrying it (`test_cards.py` reads the same
  448 characters back byte for byte at the APDU level). But every view that reads
  a SeedKeeper's headers by name, *Load MultiSig Descriptor* among them, needs
  `SEEDKEEPER_DIC_TYPE`, and `smartcard_views.py` never binds it: the name is
  imported inside a `try`/`except ImportError` next to three modules that exist
  in no published pysatochip, so the whole import is swallowed. The screen puts
  up `name 'SEEDKEEPER_DIC_TYPE' is not defined`. Saving survives it only because
  it reads the headers of a card it just found empty; a second descriptor onto
  the same card raises the same thing. Nothing here works around any of it.
- **Copying a secret from one SeedKeeper to another**, an encrypted exchange
  needing a second card to negotiate a session key with. Refused rather than
  quietly done in the clear.
- **Card signing**, PIN change and unblock, 2FA, factory reset and the
  card-management screens: all answer "not supported".
- **Cards that remember.** State is in memory only, so a reload gives factory-fresh
  cards. Deliberate: nothing you do here should outlive the tab.

## How it works

The wallet's Python runs under [Pyodide](https://pyodide.org) (CPython compiled to
WebAssembly) inside a Web Worker. Four hardware seams are replaced, three of them
under stock, which has no smartcard code to reach for the fourth:

| Seam | Replaced by | Why |
| --- | --- | --- |
| Display | [`src/shims/browser_display.py`](src/shims/browser_display.py) | Swaps the panel driver underneath SeedSigner's own unmodified `Renderer`; raw RGB frames go to a canvas. |
| Buttons | patched in [`src/web/wallet-worker.js`](src/web/wallet-worker.js) | The worker is blocked inside the wallet's main loop and can never answer a `postMessage`, so keys cross on a `SharedArrayBuffer` and wake it with `Atomics`. |
| Camera + QR | [`src/shims/browser_camera.py`](src/shims/browser_camera.py) + [`src/web/wallet-camera.js`](src/web/wallet-camera.js) | pyzbar is a C library with no WebAssembly build, so the browser decodes and hands the bytes to SeedSigner's unmodified decoder. |
| Smartcard | [`src/smartcard/`](src/smartcard) | Browsers have no smartcard API, so simulated SeedKeeper and Satochip cards answer real APDUs and pysatochip runs against them unchanged. |

A fifth module, [`src/shims/browser_qr.py`](src/shims/browser_qr.py), makes the
screens that *display* a QR draw one: their drawing lives in a thread this
environment cannot run.

That single constraint (a worker that is permanently blocked and cannot service a
message) explains most of the architecture, including why keys, camera frames and
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

MIT. See [LICENSE](LICENSE).

Almost none of the code that runs here was written for this repository: the wallet
is upstream SeedSigner (MIT, Copyright (c) 2021 SeedSigner), the interpreter is
Pyodide, the QR decoder is jsQR, and everything the wallet imports is somebody
else's library. [THIRD-PARTY.md](THIRD-PARTY.md) lists all of it: version,
origin, licence, and how to check each one.

## Credits

- [SeedSigner](https://github.com/SeedSigner/seedsigner), the device and the
  firmware this runs. All of the interesting parts are theirs.
- [3rdIteration/seedsigner](https://github.com/3rdIteration/seedsigner), the fork
  this is pinned to, which adds the smartcard support the simulated cards
  answer.
- [Pyodide](https://pyodide.org) and [jsQR](https://github.com/cozmo/jsQR), the two
  pieces of other people's work that make the browser side possible.

This is an independent project. It is not affiliated with or endorsed by the
SeedSigner project, and running it proves nothing about a real device.
