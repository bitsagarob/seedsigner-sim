# Architecture

How real SeedSigner device firmware ends up running in a browser tab, and why the
code looks the way it does. Two firmwares are built and the page runs one of
them: stock SeedSigner, and the 3rdIteration smartcard fork. Everything below is
true of both unless it says otherwise, and where they differ the difference is
almost always the smartcard.

The short version: the wallet's own Python runs unmodified under Pyodide in a Web
Worker, and four hardware seams are replaced from the outside, three of them
under stock, which has no smartcard code to reach for. One constraint (the
worker is permanently blocked inside the wallet's main loop and can never answer a
message) explains most of the rest.

## Contents

- [The shape of it](#the-shape-of-it)
- [The constraint everything follows from](#the-constraint-everything-follows-from)
- [Seam 1: the display](#seam-1-the-display)
- [Seam 2: the buttons](#seam-2-the-buttons)
- [Seam 3: the camera and the QR decode](#seam-3-the-camera-and-the-qr-decode)
- [Seam 4: the smartcard](#seam-4-the-smartcard)
- [The fifth module: screens that show a QR](#the-fifth-module-screens-that-show-a-qr)
- [The rest of the environment](#the-rest-of-the-environment)
- [Boot, in order](#boot-in-order)
- [Where the seams are not](#where-the-seams-are-not)

## The shape of it

```
  page thread (wallet.html)                worker thread (wallet-worker.js)
  ─────────────────────────                ────────────────────────────────
  canvas  ◀── postMessage ─────────────────  Pyodide
  keydown ──▶ ┐                              └─ /wallet  (wallet.zip, unpacked)
  webcam  ──▶ ├─ SharedArrayBuffer ──▶  ┐        ├─ seedsigner/…   unmodified
  card tray ──┘   (Atomics.wait/notify)  ├──────▶│  ├─ smartcard/    simulated
                                         │       │  └─ vendored deps
                                         └───────┴─ browser_display.py
                                                    browser_camera.py
                                                    browser_qr.py
```

Three buffers cross the boundary, one per input: keys, camera, card tray. Output,
the display frames, goes the other way as ordinary `postMessage`, because that
direction still works (see below).

`wallet-<firmware>.zip` is the wallet: the `seedsigner` package from the commit
that firmware's section of [`UPSTREAM`](../UPSTREAM) pins, the pure-Python
dependencies it needs (embit, pysatochip, qrcode, mnemonic, urtypes, ecdsa, …),
and, for the fork, the simulated `smartcard` package that stands in for pyscard.
Stock's zip carries no `smartcard` package, because stock has no code that could
import one. `six` is in the fork's zip too, at the version upstream pins, rather
than left to Pyodide to provide: ecdsa imports it, and a dependency the build
does not name is a dependency nobody can check. The zip is fetched and unpacked
into Pyodide's in-memory filesystem at `/wallet`, which becomes the working
directory.

The three `browser_*.py` shims are **not** in the zip. They are fetched separately
and written into `/wallet` at boot, so the zip stays exactly what the build script
produced from upstream and the seams stay visibly outside it.

## The constraint everything follows from

SeedSigner's main loop blocks the CPU waiting for a button. On the device that is
correct: there is nothing else to do. In a browser it means the thread running it
never returns to its event loop.

That has one consequence, and it is asymmetric:

- **Out of the worker works.** Python calls a JS callback, the callback calls
  `postMessage`, the page gets it. Frames and log lines travel this way.
- **Into the worker is impossible.** A `postMessage` sent to the worker sits in a
  queue that will never be drained, because draining it requires the wallet's
  blocking loop to return, which it does not do until the user presses the button
  it is waiting for. Which is the thing we are trying to deliver.

So every input crosses on a `SharedArrayBuffer` instead: shared memory the worker
can read synchronously, and park on with `Atomics.wait` until the page bumps it
with `Atomics.notify`. That is why the page needs cross-origin isolation
(`Cross-Origin-Opener-Policy: same-origin` plus
`Cross-Origin-Embedder-Policy: require-corp`); without it `SharedArrayBuffer` is
not constructible and there is no way in at all. `wallet.html` checks
`crossOriginIsolated` before it even starts the worker and says so plainly if the
server got it wrong.

It is also why the wallet runs in a worker rather than on the page's thread: the
blocking is fine as long as it happens somewhere the UI is not.

Two of the three channels (`wallet-camera.js`, `wallet-cards.js`) put both halves,
page and worker, in one file, loaded by the page with a `<script>` tag and by the
worker with `importScripts`. They have to agree byte-for-byte on the buffer layout,
and a layout written down twice is a layout that drifts.

## Seam 1: the display

[`src/shims/browser_display.py`](../src/shims/browser_display.py)

SeedSigner draws through a `Renderer` onto a driver for whichever panel is
configured. `BrowserDisplay` is another driver: same `BaseDisplayDriver` base
class, same `show_image` contract, but where a real one clocks pixels out over SPI
this one hands the image's raw RGB bytes to a callback.

`install()` replaces `DisplayDriverFactory.instantiate_display_driver` so any
configured display type produces a `BrowserDisplay`. Nothing above the driver knows:
the `Renderer`, every `Screen`, every `View`, the fonts and the layout code are all
unmodified SeedSigner.

The frame then goes worker → page as a transferable `Uint8Array`, and the page
expands RGB to RGBA into an `ImageData` and does one `putImageData`. The canvas
sits in the cutout of an SVG device drawn by
[`seedsigner-device.js`](../src/web/seedsigner-device.js), positioned in
percentages so the two stay registered at any viewport width.

The emulated panel is 320×240 (the `st7789_320x240` config, which the boot shim
writes into `settings.json` before the wallet reads it). The page believes the
worker about the size: `js_report_size` reports the renderer's real canvas
dimensions, and the page resizes the canvas and re-renders the device art to match.

## Seam 2: the buttons

Patched inline in [`src/web/wallet-worker.js`](../src/web/wallet-worker.js).

An 8-byte `SharedArrayBuffer` viewed as `Int32Array`: slot 0 is "a key is
waiting", slot 1 is which one. The page stores the keycode, stores 1, notifies.
The worker's `js_wait_for_key` parks on `Atomics.wait(keyBuffer, 0, 0)`, reads the
code, clears the flag.

On the Python side that becomes `HardwareButtons.wait_for`, which is what the whole
wallet calls to read a button. The buffer carries an index into `BUTTON_NAMES`
rather than a raw value, because this fork identifies buttons by name (`KEY_UP`)
where older ones use GPIO numbers; resolving through `HardwareButtonsConstants`
works for either.

There is a second, non-blocking path. The scan screen cannot block on a button: it
has camera frames to pull at the same time, so it polls with `check_for_low`
instead. `js_peek_key` is the same channel without the parking.

Polling introduced one problem worth knowing about. A single pass of the scan
loop asks about several keys in turn (`KEY_RIGHT` before `KEY_LEFT`), so a press
has to stay claimable long enough for every check in that pass to see it, but not
forever, or a key nobody wants sits at the front hiding the press behind it. Hence
`_PENDING_KEYS`, where each pending press is offered up to four times and then
dropped.

## Seam 3: the camera and the QR decode

[`src/shims/browser_camera.py`](../src/shims/browser_camera.py) +
[`src/web/wallet-camera.js`](../src/web/wallet-camera.js)

Two things are faked here, not one: the video stream, and the decode.

The stream is easy to justify: a browser has `getUserMedia` and no picamera. The
decode is the interesting one. SeedSigner reads QR codes with **pyzbar**, a binding
to the zbar C library, and there is no zbar built for WebAssembly. Porting it is
not the answer, because the browser already has a QR decoder of its own. So the
fake sits exactly where SeedSigner reaches for hardware, and everything above it
(`ScanScreen`, `DecodeQR`'s parsing of SeedQR, CompactSeedQR, PSBT and UR payloads,
and every view that consumes them) runs unmodified.

### The buffer

One `SharedArrayBuffer`, laid out in `wallet-camera.js`:

| Region | Size | Direction |
| --- | --- | --- |
| header (`CMD`, `STATE`, `FRAME_SEQ`, `FRAME_W`, `FRAME_H`, `QR_LEN`, `ERR_LEN`) | 64 B | both |
| decoded QR payload | 8192 B | page → worker |
| error string | 256 B | page → worker |
| preview frame, RGB | 240 × 240 × 3 | page → worker |

`CMD` is the worker asking for the camera on or off. `STATE` is the page answering
(idle / starting / running / failed). The page runs a ~15 fps loop; the Python
decode loop is slower than that, so frames published in between are simply
overwritten and the page is never the bottleneck.

Two ordering rules matter. `FRAME_SEQ` is bumped **last**, after the pixels, because
it is what tells the worker the bytes are worth reading; a worker that reads
mid-write gets a torn preview frame and nothing worse, since the decode never looks
at those bytes. And `QR_LEN` is a one-slot mailbox: the page will not decode another
payload while one is still unclaimed, and the worker clearing it is what unlocks
the next. That is also what stops a QR held in front of the camera from flooding
the decoder with repeats of itself.

### The path from webcam to seed

1. The page draws the video into a 640×480 capture canvas (full size, where the QR
   is still sharp) and a 240×240 preview canvas (small, because every byte of it
   gets copied into a PIL image on the Python side).
2. `BarcodeDetector`, if the browser has it, is asked whether there is a QR in the
   capture at all. On almost every frame the answer is no, and native code says no
   faster than JavaScript can.
3. If it says yes, or if there is no `BarcodeDetector`, **jsQR** decodes the same
   `ImageData` and returns `binaryData`: the codewords themselves, as bytes.
4. Those bytes are written into the buffer and `QR_LEN` is set.
5. In the worker, `DecodeQR.extract_qr_data` (the pyzbar call) ignores the image it
   was handed and returns whatever the page last published, as `bytes`.
6. From there it is all upstream code: `DecodeQR` sniffs the payload type, decides
   it is a SeedQR, a CompactSeedQR, a PSBT fragment or a UR, and the matching view
   takes over.

Frames and payloads are deliberately not tied to each other. The decode ran in
JavaScript against the page's own copy of the frame; the image that reaches Python
matters only for the preview.

### Why `rawValue` is never trusted

`BarcodeDetector` only ever exposes `rawValue`, a **string**. A CompactSeedQR is
raw entropy bytes, not text, and those do not survive being decoded as characters
and re-encoded. So the native detector is used as a gate and nothing more: jsQR,
which returns the codewords, is the only thing allowed to produce a payload.

There is deliberately no fallback to `rawValue` when jsQR comes up empty, and the
reason is worth stating plainly. A mis-read string can still be a *plausible
length*, and 16, 20, 24, 28 or 32 bytes is all it takes for `DecodeQR` to accept it
as a CompactSeedQR. The wallet would then load, display and offer to back up a seed
that was never in front of the camera. A scan that fails and retries is
recoverable; a wrong seed presented as a right one is not.

This is not theoretical: the regression test that covers it
(`test_scan_native.py`) reached a valid-looking fingerprint from pure garbage
before the fallback was removed. It now points the fake camera at a blank video and
fails if the wallet reports any seed at all.

### The preview

`ScanScreen` draws its live preview from a thread, and this environment has no
threads (see below), so the preview would be frozen on whatever was drawn before
it. Rather than reimplement it (it draws the progress bar for animated QRs, the
frame-accepted indicator and the translated instructions), the shim lets
SeedSigner's own loop body run exactly one pass per camera read: `keep_running`
answers `True` once, then `False`, so `run()` draws a single frame and returns.
One frame read, one frame drawn.

## Seam 4: the smartcard

[`src/smartcard/`](../src/smartcard)

Browsers have no smartcard API at all, so the only honest place to fake a card is
the transport. pysatochip reaches a physical card through **pyscard**; this package
*is* `smartcard`, the module name pyscard occupies, and it answers with cards
implemented in Python. Everything above it (the whole of pysatochip, the whole of
SeedSigner) runs unmodified, which is the point: the flows are exercised rather
than mocked.

[`simulated_card.py`](../src/smartcard/simulated_card.py) implements two applets at
the APDU level, because this fork talks to two different cards and they are not the
same product. A **Satochip** holds one BIP32 master key and derives from it. A
**SeedKeeper** holds a list of labelled secrets and hands them back under the export
rights each was stored with, which is the flow the SeedSigner+ Smartcard is sold
for, so it is the tray's default.

The two share a base class, because pysatochip sends the identity, the setup, the
PIN and the status blob to either card in the same shape and a second copy of those
would drift.

| Instruction | Applet | Behaviour |
| --- | --- | --- |
| `SELECT` (`00 A4`) | both | 9000 for the card's own AID, "file not found" for any other, which is how `card_select()` settles on the right applet, and what leaves a card of the wrong type unselected and answering nothing |
| `GET DATA` (`80 CA`) | both | CPLC, IIN, CIN: the three blobs pysatochip hashes into a card UID |
| `GET STATUS` (`B0 3C`) | both | the 12-byte status blob: versions, PIN tries left, carrying something, setup done, secure channel |
| `SETUP` (`B0 2A`) | both | takes the PIN and becomes an initialised card; once per card |
| `VERIFY PIN` (`B0 42`) | both | checks PIN0, spends a try when wrong, reports the count left in the `63Cx` status word |
| `BIP32 GET AUTHENTIKEY` (`B0 73`) | both | the authentikey's x coordinate, signed by the authentikey |
| `BIP32 IMPORT SEED` (`B0 6C`) | Satochip | takes a master seed and derives the BIP32 master key and the authentikey from it; once per card |
| `BIP32 RESET SEED` (`B0 77`) | Satochip | forgets both again, if the PIN sent with the command is right |
| `BIP32 GET EXTENDED KEY` (`B0 6D`) | Satochip | a derived public key and chaincode, signed by the derived key and then by the authentikey |
| `SEEDKEEPER GET STATUS` (`B0 A7`) | SeedKeeper | how many secrets, how much memory, and how much of it is left |
| `SEEDKEEPER IMPORT SECRET` (`B0 A1`) | SeedKeeper | plaintext only: the header, then the bytes 128 at a time, then a commit that answers with the id the card filed it under and the card's own hash of what it stored |
| `SEEDKEEPER EXPORT SECRET` (`B0 A2`) | SeedKeeper | plaintext only, and only if that secret's export rights allow it: the header, then the bytes, then a signature over both |
| `SEEDKEEPER LIST HEADERS` (`B0 A6`) | SeedKeeper | one header per call, `9C12` when there are no more |
| `SEEDKEEPER RESET SECRET` (`B0 A5`) | SeedKeeper | forgets one, and gives back the space it took |
| anything else | | `6D00`, "not supported" |

### The Satochip's seed

A Satochip does not hand back what it was given: it answers with a key and a
signature, and pysatochip *recovers* the signing key from that signature and keeps
the answer only if it matches what the message claimed. So the card has to hold
real keys and really sign with them, and it does: `embit` derives BIP32 and signs,
and the authentikey's private key is the first 32 bytes of
`HmacSha512('Bitcoin seed2', seed)`, which is where a real Satochip's comes from
too. That makes the authentikey this card reports the one a physical Satochip
carrying the same seed would report.

### The SeedKeeper's secrets

A secret is two things: a **header** and a payload. The header is 15 bytes plus a
label, and the split in it is the whole design. The client proposes what the secret
*is* (its type, its export rights, its subtype and its label), and the card owns
everything else: the id it files it under, the fact that it arrived in plaintext,
how many times it has been exported, and the fingerprint, which is the first four
bytes of SHA-256 over the payload. A client that could write its own fingerprint
could hand a secret back that was not the one it stored, and both
`seedkeeper_import_secret()` and `seedkeeper_export_secret()` check that fingerprint
against a hash they compute themselves.

Export rights are the point of the product, so they are enforced where a card would
enforce them. `Plaintext export allowed` is the only value this card can satisfy,
because plaintext is the only way out it implements; a secret stored `Export
forbidden`, `Encrypted export only` or `Authenticated export only` is refused with
`9C31`, which pysatochip reports as "export not allowed by SeedKeeper policy" rather
than as a failure to read. The check happens on the first APDU of the exchange,
before any of the secret has left the card.

Saving a seed writes a `Masterseed`, subtype 1: the 64-byte master seed, the
wordlist its entropy is in, that entropy, and the passphrase. Loading it back reads
the same layout and rebuilds the mnemonic from the entropy. Both halves are the
wallet's own code (`SaveToSeedkeeperView` and `SeedKeeperSelectView`), and the card
only stores and returns bytes.

A multisig wallet descriptor is the other secret the product is sold to carry, and
to the card it is only a different type byte and a longer payload: `0xC1` rather
than `0x10`, and a couple of hundred bytes of quorum, xpubs and derivation paths
rather than 84. Nothing had to be added for it. A secret is a type, a policy, a
label and some bytes, which is what a SeedKeeper is at the APDU level, so the only
thing a descriptor changes is that both 128-byte loops run several passes instead
of none: `test_cards.py` stores a real 2 of 3 in five APDUs and reads it back in
five, and charges the card the same number of bytes
`seedkeeper_utils.calculate_seedkeeper_secret_size` predicts it will be charged.
The wallet's two descriptor screens still cannot use any of that, and that is not
this package's fault either; see below.

A SeedKeeper has an authentikey too, and it signs each export with it over the
header *and* the payload together, so a card cannot hand back the right bytes under
somebody else's label. There is no seed here to derive that key from (a real card
generates it on the card at setup), so this one derives it from the card's own UID,
which makes a given card the same card on every run and so lets a test say which
card signed something.

### State, and where it is not

Everything a card holds lives in the card object and nowhere else. There is no
filesystem, no `localStorage`, no `IndexedDB`: reloading the page is a factory-fresh
card, which is the behaviour a simulator that nobody should trust with a real seed
ought to have.

Every applet instruction is gated on a verified PIN, cleared whenever the applet is
selected, exactly as a JavaCard applet loses its PIN state when it is deselected.
Answering `9C06` there is not an error to the client: it is `card_transmit()` being
told to verify the PIN it cached and send the command again. Card-edge instructions
are also gated on the applet being *selected* at all, because what is on the other
end of a failed `SELECT` is the card manager, which has never heard of a Satochip;
that is what stops a Satochip being half-driven through a SeedKeeper flow, and given
a PIN, before the first instruction it does not have.

There are three tray slots because a user needs to tell one card from another: put a
PIN on Card A, check Card B is still blank, come back and find Card A as it was
left. Each slot holds one card of each type, and choosing the type in the tray is
choosing which card is in your hand, so the two have different CINs and so different
UIDs: pysatochip hashes CPLC+IIN+CIN into the UID it uses to distinguish cards.
State lives in a module-level registry, so it survives being taken out of the reader
and put back, and survives its slot being switched to the other type and back.

Which card is in the reader, and which type it is, are the user's business and the
user is on the page, so the tray is a third `SharedArrayBuffer`
([`wallet-cards.js`](../src/web/wallet-cards.js)): the page writes the inserted
index and the type of each slot, and bumps a sequence number, and the worker parks
on it in slices while the wallet waits for a card. Six slots go the other way, one
per card, packed into an `Int32` each, so the tray can show blank / initialised /
seeded and the PIN tries left, for both of a slot's cards, because the user can
switch type with the wallet not looking and the page has no way to ask Python about
the card that is not in the reader.

The type control is a button under each card rather than something inside it. A card
is a SeedKeeper or a Satochip, not a card with a setting on it, so the choice is
made before it goes in and is disabled while it is in the reader, the same reason
the eject control goes dead with nothing to eject.

Two smaller fakes complete the picture. pyscard notifies observers of insert and
remove events from a background thread; there is no such thread, so polling happens
on the caller's thread at every point the wallet reaches into the package. And a
connection is to a *card*, not to a reader: transmitting on a connection whose card
has been pulled raises, rather than letting a flow run to completion against a card
the user is holding in their hand.

**Not implemented:** signing (`SIGN_TRANSACTION`, `SIGN_MESSAGE`), private-key and
BIP85 export, PIN change and unblock, 2FA, card label and NDEF, factory reset, the
SeedKeeper's encrypted import and export and its log, and the secure channel. All
still `6D00`, so the wallet reports them as unsupported rather than being told a
comfortable lie. Two need saying twice. The secure channel would encrypt every APDU
with a session key negotiated over ECDH to protect the wire between reader and card;
there is no wire here, and the card says so in `GET STATUS` so pysatochip never wraps
anything. And a SeedKeeper's *encrypted* export is what moves a secret to a second
card without it ever appearing in the clear; it needs a session key negotiated with
that card's public key, and there is no second card here to negotiate with, so
clone-to-another-card is refused rather than quietly done in plaintext.

Two flows still do not work, and neither is this package's fault.

`ToolsSatochipImportSeedView` unpacks `card_bip32_import_seed()`'s return value
into `(response, sw1, sw2)`, but on the pysatochip backend that method returns the
card's authentikey, so a *successful* import is what raises `TypeError`. The card
takes the seed; the screen reports failure. See `test/test_cards_seed.py`, which
asserts it so the day upstream fixes it is not a silent one.

`ToolsSeedkeeperLoadDescriptorView` cannot read a descriptor back, and every other
view that reads a SeedKeeper's headers by name is stopped in the same place.
`seedsigner/views/smartcard_views.py` imports the names it needs inside a
`try: … except ImportError: pass`, and three of the four modules that block asks
for exist in no published pysatochip: `pysatochip.satochip`,
`pysatochip.exception` and `pysatochip.satochip_protocol_helper` are absent from
PyPI 0.17.0, from the tag the device builds, and from the fork's own default
branch. So the import always raises, the `pass` swallows it, and
`SEEDKEEPER_DIC_TYPE` (which `JCconstants` really does define) is never bound.
The load screen reads it on the first header the card hands back and raises
`NameError: name 'SEEDKEEPER_DIC_TYPE' is not defined`, which its own `except`
puts on a warning screen.

`ToolsSeedkeeperSaveDescriptorView` **works**, and it survives that same missing
name only because it reads the headers of a card it has just found empty, so the
loop that would touch it never runs. Saving a second descriptor to the same card
raises the same `NameError`. The save screen files the descriptor as secret type
`0xC1` because it read `protocol_minor_version` off the card and got 2; a v1
SeedKeeper takes the other branch and stores a descriptor as a `Password`. This
card reports 2 because that is what the card it stands in for reports, and what
the seed layout above depends on.

Until recently the save half was blocked here too, by `make_header("Descriptor",
…)` raising `KeyError`, and that one was ours: PyPI's pysatochip 0.17.0 stops at
`0xC0: 'Data'` and has no name for `0xC1`. The device does not use PyPI. Its OS
image builds `3rdIteration/pysatochip` at the tag `0.6a` through buildroot and
deletes `requirements.txt` from the rootfs, and that tree has
`0xC1: 'Descriptor'`. The build now pins what the device builds, which is what
made a real save flow appear where a wall used to be. See the pysatochip note in
[`build/build-wallet-zip.sh`](../build/build-wallet-zip.sh).
`test/test_cards_seedkeeper_descriptor.py` drives the save to the end and the
load to its wall, and checks the missing modules out of the wallet zip so the
`NameError` is evidence rather than a story.

## The fifth module: screens that show a QR

[`src/shims/browser_qr.py`](../src/shims/browser_qr.py)

Not a hardware seam, but the same problem in a different place.
`QRDisplayScreen` puts every pixel of its output inside a thread and its `_run()`
does nothing but wait for a button. With no threads, every QR the wallet wants to
*show* comes out blank: exported xpubs, signed PSBTs, SeedQR backups, addresses.
The flow appears to work and hands back an empty screen.

The same trick as the camera preview applies: run SeedSigner's own loop body one
pass at a time. Its last statement is a sleep sized to hold each frame for a sixth
of a second, so one pass is exactly one animation frame at the intended rate, and
animated QRs advance on their own without a timer. The pump hangs off `wait_for`
rather than `_run`, because waiting for a button is all `_run` does, so the
brightness adjustment, the tip toast, the encoder's frame sequence and the exit
conditions all stay upstream's.

## The rest of the environment

The boot shim in `wallet-worker.js` also patches over the ways Pyodide is not a
Raspberry Pi. These are small, but each one is a hard failure without it:

- **No threads.** `threading.Thread` is replaced. SeedSigner's own `BaseThread`
  subclasses loop on `keep_running` to animate something; running one synchronously
  would never return, so they are dropped. Everything else is a one-shot helper
  whose work the caller may be waiting on, so those run inline on `start()`.
  `BackgroundImportThread` is the named exception: the controller blocks waiting
  for it to set up storage, and without it the wallet hangs forever after the
  splash.
- **No timers either, and so no locks worth the name.** `threading.Timer` runs
  its callback inline on `start()`, which is the only way it will ever run: the
  wallet's one Timer is `Settings.save()`'s debounced write, and without it every
  settings change ends on a System Error. But running it inline runs it inside
  the lock `save()` is holding while it schedules it, and one thread taking a
  plain `Lock` twice waits for itself forever, which wedged the wallet after the
  change had already been stored. There is one thread here, so the only acquire
  that can block is a thread blocking on itself; `threading.Lock` is therefore
  the reentrant one.
- **Testnet, not mainnet.** Settings comes up on whatever `settings.json` holds,
  so the boot shim writes `network: T` there next to the display config, before
  the wallet reads it. Configuration rather than a patch: it is the file a
  configured device would have, `seedsigner/` is untouched, and Mainnet is still
  in Settings > Advanced > Bitcoin network where it always was. What changes is
  where a visitor starts, not what they can reach.
- **`pycryptodomex` is `pycryptodome` under another name.** A meta path finder
  maps `Cryptodome.*` onto `Crypto.*`.
- **`hashlib.pbkdf2_hmac` does not exist.** It lives in `_hashlib`, the OpenSSL
  binding, which this build does not have, and it sits directly on the path from
  a mnemonic to seed bytes, so without it loading any seed at all fails. pycryptodome's
  PBKDF2 is borrowed rather than hand-rolling the derivation.
- **No processes.** Several helpers shell out to a faster native tool and fall back
  to pure Python when the binary is missing; `qr.py` does it with `qrencode`.
  Emscripten raises `OSError` where those fallbacks catch `FileNotFoundError`, so
  `subprocess` is patched to report the binary as absent, which is both true here
  and the case they already handle.
- **No OpenCV, no numpy.** `decode_qr` imports numpy inside a `try` that starts
  with `import cv2`, and opencv is not loaded, so `np` is `None` either way. The
  browser decode is what makes that harmless.

Under `?debug=1` the shim also traces the screen lifecycle: every `View.run`,
every `BaseScreen.display`, every thread start and every long sleep. That trace is
how you locate a stall, and it is what the browser tests assert against. Without
the flag, `js_log` builds nothing and posts nothing.

## Boot, in order

1. `wallet.html` checks `crossOriginIsolated`. If the headers are missing it stops
   here and says so.
2. It allocates the three shared buffers, mounts the device art and the card tray,
   starts the worker and posts one `init` message, the only message the worker
   will ever receive, because after this it is inside Python.
3. The worker loads Pyodide, then the three binary packages it needs: Pillow,
   pycryptodome, cryptography.
4. It fetches the wallet zip for the firmware the page asked for and unpacks it
   to `/wallet`, then fetches the three `browser_*.py` shims and writes them
   alongside.
5. It runs the boot shim: settings (display config and network), threads, crypto
   aliases, the display driver, the button patches, the camera, the QR pump, and
   under the fork the card tray.
6. It calls `Controller.get_instance().start()` (upstream's own entry point),
   which blocks for the lifetime of the worker.

The camera stays idle until the wallet asks for it, so nothing prompts for
permission before the user chooses to scan.

## Where the seams are not

Worth being explicit, because "the real firmware" is a claim that deserves a
boundary:

- Nothing in `wallet.zip` is patched. The `seedsigner` package there is the pinned
  upstream tree; `build/build-wallet-zip.sh` rebuilds it so you can diff.
- All four seams are installed by replacing attributes at runtime, from modules
  outside the zip, after the wallet is unpacked and before it starts.
- The traces described above are wrappers around upstream methods. They call
  through, and with `?debug=1` off they do nothing but call through.
