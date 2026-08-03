# Architecture

How the real SeedSigner firmware ends up running in a browser tab, and why the
code looks the way it does.

The short version: the wallet's own Python runs unmodified under Pyodide in a Web
Worker, and four hardware seams are replaced from the outside. One constraint —
the worker is permanently blocked inside the wallet's main loop and can never
answer a message — explains most of the rest.

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

Three buffers cross the boundary, one per input: keys, camera, card tray. Output
— the display frames — goes the other way as ordinary `postMessage`, because that
direction still works (see below).

`wallet.zip` is the wallet: the `seedsigner` package from the commit pinned in
[`UPSTREAM`](../UPSTREAM), the pure-Python dependencies it needs (embit,
pysatochip, qrcode, mnemonic, urtypes, ecdsa, …), and the simulated `smartcard`
package that stands in for pyscard. It is fetched and unpacked into Pyodide's
in-memory filesystem at `/wallet`, which becomes the working directory.

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
`Cross-Origin-Embedder-Policy: require-corp`) — without it `SharedArrayBuffer` is
not constructible and there is no way in at all. `wallet.html` checks
`crossOriginIsolated` before it even starts the worker and says so plainly if the
server got it wrong.

It is also why the wallet runs in a worker rather than on the page's thread: the
blocking is fine as long as it happens somewhere the UI is not.

Two of the three channels (`wallet-camera.js`, `wallet-cards.js`) put both halves —
page and worker — in one file, loaded by the page with a `<script>` tag and by the
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
has to stay claimable long enough for every check in that pass to see it — but not
forever, or a key nobody wants sits at the front hiding the press behind it. Hence
`_PENDING_KEYS`, where each pending press is offered up to four times and then
dropped.

## Seam 3: the camera and the QR decode

[`src/shims/browser_camera.py`](../src/shims/browser_camera.py) +
[`src/web/wallet-camera.js`](../src/web/wallet-camera.js)

Two things are faked here, not one: the video stream, and the decode.

The stream is easy to justify — a browser has `getUserMedia` and no picamera. The
decode is the interesting one. SeedSigner reads QR codes with **pyzbar**, a binding
to the zbar C library, and there is no zbar built for WebAssembly. Porting it is
not the answer, because the browser already has a QR decoder of its own. So the
fake sits exactly where SeedSigner reaches for hardware, and everything above it —
`ScanScreen`, `DecodeQR`'s parsing of SeedQR, CompactSeedQR, PSBT and UR payloads,
and every view that consumes them — runs unmodified.

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
3. If it says yes — or if there is no `BarcodeDetector` — **jsQR** decodes the same
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
it. Rather than reimplement it — it draws the progress bar for animated QRs, the
frame-accepted indicator and the translated instructions — the shim lets
SeedSigner's own loop body run exactly one pass per camera read: `keep_running`
answers `True` once, then `False`, so `run()` draws a single frame and returns.
One frame read, one frame drawn.

## Seam 4: the smartcard

[`src/smartcard/`](../src/smartcard)

Browsers have no smartcard API at all, so the only honest place to fake a card is
the transport. pysatochip reaches a physical card through **pyscard**; this package
*is* `smartcard`, the module name pyscard occupies, and it answers with cards
implemented in Python. Everything above it — the whole of pysatochip, the whole of
SeedSigner — runs unmodified, which is the point: the flows are exercised rather
than mocked.

[`simulated_card.py`](../src/smartcard/simulated_card.py) implements a Satochip at
the APDU level:

| Instruction | Behaviour |
| --- | --- |
| `SELECT` (`00 A4`) | 9000 for the Satochip AID, "file not found" for every other, which is how `card_select()` settles on Satochip |
| `GET DATA` (`80 CA`) | CPLC, IIN, CIN — the three blobs pysatochip hashes into a card UID |
| `GET STATUS` (`B0 3C`) | the 12-byte status blob: versions, PIN tries left, seeded, setup done, secure channel |
| `SETUP` (`B0 2A`) | takes the PIN and becomes an initialised card; once per card |
| `VERIFY PIN` (`B0 42`) | checks PIN0, spends a try when wrong, reports the count left in the `63Cx` status word |
| `BIP32 IMPORT SEED` (`B0 6C`) | takes a master seed and derives the BIP32 master key and the authentikey from it; once per card |
| `BIP32 RESET SEED` (`B0 77`) | forgets both again, if the PIN sent with the command is right |
| `BIP32 GET AUTHENTIKEY` (`B0 73`) | the authentikey's x coordinate, signed by the authentikey |
| `BIP32 GET EXTENDED KEY` (`B0 6D`) | a derived public key and chaincode, signed by the derived key and then by the authentikey |
| anything else | `6D00`, "not supported" |

The seed is where the interesting part is. A Satochip does not hand back what it
was given: it answers with a key and a signature, and pysatochip *recovers* the
signing key from that signature and keeps the answer only if it matches what the
message claimed. So the card has to hold real keys and really sign with them, and
it does — `embit` derives BIP32 and signs, and the authentikey's private key is
the first 32 bytes of `HmacSha512('Bitcoin seed2', seed)`, which is where a real
Satochip's comes from too. That makes the authentikey this card reports the one a
physical Satochip carrying the same seed would report.

Both keys live in the card object and nowhere else. There is no filesystem, no
`localStorage`, no `IndexedDB`: reloading the page is a factory-fresh card, which
is the behaviour a simulator that nobody should trust with a real seed ought to
have. The BIP32 instructions are also gated on a verified PIN, cleared whenever
the applet is selected, exactly as a JavaCard applet loses its PIN state when it
is deselected. Answering `9C06` there is not an error to the client: it is
`card_transmit()` being told to verify the PIN it cached and send the command
again.

There are three cards because a user needs to tell one from another: put a PIN on
Card A, check Card B is still blank, come back and find Card A as it was left. They
differ only in their CIN, which is enough — pysatochip hashes CPLC+IIN+CIN into the
UID it uses to distinguish cards. State lives in a module-level registry, so it
survives being taken out of the reader and put back.

Which card is in the reader is the user's business and the user is on the page, so
the tray is a third `SharedArrayBuffer`
([`wallet-cards.js`](../src/web/wallet-cards.js)): the page writes the inserted
index and bumps a sequence number, and the worker parks on it in slices while the
wallet waits for a card. One slot per card goes the other way, packed into an
`Int32`, so the tray can show blank / initialised / seeded and the PIN tries left.

Two smaller fakes complete the picture. pyscard notifies observers of insert and
remove events from a background thread; there is no such thread, so polling happens
on the caller's thread at every point the wallet reaches into the package. And a
connection is to a *card*, not to a reader: transmitting on a connection whose card
has been pulled raises, rather than letting a flow run to completion against a card
the user is holding in their hand.

**Not implemented:** signing (`SIGN_TRANSACTION`, `SIGN_MESSAGE`), private-key and
BIP85 export, PIN change and unblock, 2FA, card label and NDEF, factory reset, and
the secure channel — all still `6D00`, so the wallet reports them as unsupported
rather than being told a comfortable lie. The secure channel is the one that needs
saying twice: it would encrypt every APDU with a session key negotiated over ECDH
to protect the wire between reader and card, there is no wire here, and the card
says so in `GET STATUS` so pysatochip never wraps anything.

One flow does not work, and it is not this package's fault.
`ToolsSatochipImportSeedView` unpacks `card_bip32_import_seed()`'s return value
into `(response, sw1, sw2)`, but on the pysatochip backend that method returns the
card's authentikey — so a *successful* import is what raises `TypeError`. The card
takes the seed; the screen reports failure. See `test/test_cards_seed.py`, which
asserts it so the day upstream fixes it is not a silent one.

## The fifth module: screens that show a QR

[`src/shims/browser_qr.py`](../src/shims/browser_qr.py)

Not a hardware seam, but the same problem in a different place.
`QRDisplayScreen` puts every pixel of its output inside a thread and its `_run()`
does nothing but wait for a button. With no threads, every QR the wallet wants to
*show* comes out blank — exported xpubs, signed PSBTs, SeedQR backups, addresses.
The flow appears to work and hands back an empty screen.

The same trick as the camera preview applies: run SeedSigner's own loop body one
pass at a time. Its last statement is a sleep sized to hold each frame for a sixth
of a second, so one pass is exactly one animation frame at the intended rate, and
animated QRs advance on their own without a timer. The pump hangs off `wait_for`
rather than `_run`, because waiting for a button is all `_run` does — so the
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
- **`pycryptodomex` is `pycryptodome` under another name.** A meta path finder
  maps `Cryptodome.*` onto `Crypto.*`.
- **`hashlib.pbkdf2_hmac` does not exist.** It lives in `_hashlib`, the OpenSSL
  binding, which this build does not have — and it sits directly on the path from
  a mnemonic to seed bytes, so without it loading any seed at all fails. pycryptodome's
  PBKDF2 is borrowed rather than hand-rolling the derivation.
- **No processes.** Several helpers shell out to a faster native tool and fall back
  to pure Python when the binary is missing; `qr.py` does it with `qrencode`.
  Emscripten raises `OSError` where those fallbacks catch `FileNotFoundError`, so
  `subprocess` is patched to report the binary as absent — which is both true here
  and the case they already handle.
- **No OpenCV, no numpy.** `decode_qr` imports numpy inside a `try` that starts
  with `import cv2`, and opencv is not loaded, so `np` is `None` either way. The
  browser decode is what makes that harmless.

Under `?debug=1` the shim also traces the screen lifecycle — every `View.run`,
every `BaseScreen.display`, every thread start and every long sleep. That trace is
how you locate a stall, and it is what the browser tests assert against. Without
the flag, `js_log` builds nothing and posts nothing.

## Boot, in order

1. `wallet.html` checks `crossOriginIsolated`. If the headers are missing it stops
   here and says so.
2. It allocates the three shared buffers, mounts the device art and the card tray,
   starts the worker and posts one `init` message — the only message the worker
   will ever receive, because after this it is inside Python.
3. The worker loads Pyodide, then the three binary packages it needs: Pillow,
   pycryptodome, cryptography.
4. It fetches `wallet.zip` and unpacks it to `/wallet`, then fetches the three
   `browser_*.py` shims and writes them alongside.
5. It runs the boot shim: display config, threads, crypto aliases, the display
   driver, the button patches, the camera, the QR pump, the card tray.
6. It calls `Controller.get_instance().start()` — upstream's own entry point —
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
