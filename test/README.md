# Tests

The simulator runs unmodified SeedSigner firmware under Pyodide, with four
hardware seams faked from outside it. These tests exist to check the seams,
because that is where a browser port can quietly start lying: a camera that
reports a QR nobody held up, a card reader that hands over the wrong card.

Everything here reads the wallet's own log as its oracle. The wallet narrates
every screen it puts up (`display() enter: SeedFinalizeScreen`), the camera says
which decoder it chose, and the simulated card layer says which card the Python
side saw. Asserting on those lines is a statement about what the wallet actually
did; a screenshot is not. That narration only happens when the page is loaded
with `?debug=1`, which is why every URL the tests build carries it.

## Running them

Prerequisites:

    pip install playwright==1.47.0
    playwright install chromium

Then, from a fresh clone:

    python3 test/run.py

That builds what is missing, generates the QR videos, starts a server, runs
everything against it, and stops the server afterwards. The first run also
downloads the Pyodide runtime and builds `wallet.zip` from the pinned upstream
commit, which takes a few minutes; later runs reuse both.

A subset, by substring on the step name -- the names are `leak_scan`, `cards`,
`tray_layout`, `scan_seedqr`, `scan_compact`, `scan_native`, `cards_browser`:

    python3 test/run.py scan          # everything with "scan" in the name
    python3 test/run.py leak          # just the leak scanner
    python3 test/run.py cards tray    # the smartcard side only

Individual files run on their own too, against a server you start yourself:

    python3 test/serve.py --port 8770 src/web src/shims build/out &
    python3 test/make_qr_y4m.py
    python3 test/test_scan.py

Screenshots land in `test/artifacts/`. So do the QR videos, which `run.py`
deletes afterwards because they are 115MB and regenerate in seconds; set
`SIM_KEEP_VIDEOS=1` to keep them.

| variable | default | what it does |
| --- | --- | --- |
| `SIM_PORT` | `8770` | port the test server listens on |
| `SIM_URL` | `http://127.0.0.1:$SIM_PORT` | where the tests look for the simulator |
| `SIM_ARTIFACT_DIR` | `test/artifacts` | screenshots and videos |
| `SIM_ASSETS` | `build/out`, `src/web` | where `wallet.zip` and `pyodide/` are |
| `QR_KIND` | `qr` | which QR `test_scan.py` holds up: `qr` or `qr-compact` |

`SIM_URL` is the useful one: point it at a deployed copy and the same tests prove
the page that is actually serving people decodes a QR, rather than that its files
return 200.

## What each test proves

**`leak_scan.py`** — no tracked file names the author's infrastructure: no
private or CGNAT address, no absolute home directory, no hostname that resolves
only on one LAN. A public repository should not publish its author's server
layout, and a human checking that once does not scale to every future commit.
Public URLs are deliberately untouched. The allowlist is at the top of the file
and every entry says why it is there.

**`test_cards.py`** — drives the pyscard stand-in in `src/smartcard` directly, no
browser: three distinct cards with distinct UIDs, an empty reader that raises
rather than inventing a card, state that survives a trip out of the reader, and
the right state published back to the tray. Two seconds, and it says why
`test_cards_browser.py` failed before that one has finished booting.

**`test_tray_layout.py`** — the card tray as a control: three cards side by side
at a narrow viewport with no horizontal scrollbar, the accent and lift that show
which card is in, one card in the reader at a time, and Enter on a focused card
inserting it *without* also reaching the wallet's key handler underneath.

**`test_scan.py`** — the whole scan path against Chromium's fake camera, run
twice. `qr.y4m` is the digit-based SeedQR; `qr-compact.y4m` is the raw-bytes
CompactSeedQR, which is the case that breaks first if any layer decides a payload
is text. Both encode the same seed, so both must reach `SeedFinalizeScreen` on
the same fingerprint. Both videos open on blank frames, and the test asserts
nothing is decoded during them.

**`test_scan_native.py`** — the `BarcodeDetector` branch, which the plain scan
test never reaches because desktop Chromium ships no Shape Detection API. A stub
detector is installed before any page script runs, and it always claims a QR and
always returns rubbish for `rawValue` — which is not artificial, since a real
`BarcodeDetector` handed a CompactSeedQR returns mojibake either way.

Two phases, and the first is the point of the file: **camera pointed at a blank
wall, native claiming a QR on every frame, and the wallet must load nothing at
all.** An earlier version fell back to `rawValue` here and reached a real-looking
fingerprint, `17d9884b`, from pure garbage — a seed that was never in front of
the camera. If that phase ever passes by reporting a seed, the simulator is
inventing keys, which is the worst thing a bitcoin-adjacent tool can do. The
second phase then holds up a real CompactSeedQR and requires the correct seed
anyway, because jsQR re-reads the frame for its actual bytes.

**`run.py`'s `same_seed` step** — after the scan tests, the three proof
screenshots are compared byte for byte. One seed, encoded three ways and read
down two different decoder paths, must end on one rendered fingerprint. It has
been identical across every run so far, including runs against differently built
`wallet.zip` files, so a difference means something real changed.

**`test_cards_browser.py`** — the same card story as `test_cards.py`, but through
`wallet.html` and the real tray: an empty reader ends in a warning rather than a
hang, Card A reaches the Python side with Card A's UID, Card B with a different
one, and Card A put back is still the same card. The UID in the log is the one
pysatochip derived from the APDUs the card answered, so it is evidence about what
the wallet saw rather than about what was clicked.

## Supporting files

- `harness.py` — where to point the tests, and the log reader they share.
- `serve.py` — a static server that sends COOP and COEP. Without cross-origin
  isolation `SharedArrayBuffer` is not constructible and the wallet hangs before
  it draws anything, so `python3 -m http.server` cannot serve this page at all.
  It overlays several directories so a checkout is served without being copied
  anywhere first.
- `make_qr_y4m.py` — writes the `.y4m` videos Chromium's fake camera plays,
  using the wallet's own vendored `qrcode` out of `wallet.zip` so the QR under
  test is drawn by the library SeedSigner draws one with. The seed is the
  standard BIP39 test vector "army van defense …"; nothing about it is secret and
  nothing should ever hold value.
- `run.py` — the runner described above.
