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
downloads the Pyodide runtime and builds both wallet zips from their pinned
upstream commits, which takes a few minutes; later runs reuse all of it.

A subset, by substring on the step name -- the names are `leak_scan`, `cards`,
`tray_layout`, `scan_seedqr`, `scan_compact`, `scan_native`,
`stock_scan_seedqr`, `stock_scan_compact`, `stock_scan_native`, `cards_browser`,
`cards_seed`, `cards_seedkeeper`, `cards_descriptor`:

    python3 test/run.py scan          # everything with "scan" in the name
    python3 test/run.py stock         # the three stock-firmware scans

The three scan tests run once per firmware: the smartcard fork the simulator has
always run, and stock SeedSigner. The card tests are smartcard only, because the
menus they drive do not exist in stock. `SIM_FIRMWARE` picks the firmware for a
single test file run by hand.
    python3 test/run.py leak          # just the leak scanner
    python3 test/run.py cards tray    # the smartcard side only

Individual files run on their own too, against a server you start yourself:

    python3 test/serve.py --port 8770 src/web src/shims build/out &
    python3 test/make_qr_y4m.py
    python3 test/test_scan.py

Screenshots land in `test/artifacts/`. So do the QR videos, which `run.py`
deletes afterwards because they are 160MB and regenerate in seconds; set
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

**`leak_scan.py`**: no tracked file names the author's infrastructure: no
private or CGNAT address, no absolute home directory, no hostname that resolves
only on one LAN. A public repository should not publish its author's server
layout, and a human checking that once does not scale to every future commit.
Public URLs are deliberately untouched. The allowlist is at the top of the file
and every entry says why it is there.

**`test_cards.py`**: drives the pyscard stand-in in `src/smartcard` directly, no
browser: three distinct cards with distinct UIDs, an empty reader that raises
rather than inventing a card, state that survives a trip out of the reader, and
the right state published back to the tray. Then a seed: refused before the PIN
is verified, refused if it is too short, refused a second time, and once accepted,
answering with keys that pysatochip's own `CardDataParser` (read out of
`wallet.zip`, so it is the copy the browser runs) recovers a public key from.
The card's `m/84'/0'/0'` has to equal the same seed derived outside it, and its
master fingerprint has to be the test vector's. Then the SeedKeeper half: a
masterseed and a 448-character 2 of 3 multisig descriptor stored, listed,
exported and checked against pysatochip's own header parser, the export rights
of each enforced by the card, and the space each costs compared with upstream's
own `calculate_seedkeeper_secret_size`. Two seconds, and it says why the browser
tests failed before either has finished booting.

**`test_tray_layout.py`**: the card tray as a control: three cards side by side
at a narrow viewport with no horizontal scrollbar, the accent and lift that show
which card is in, one card in the reader at a time, and Enter on a focused card
inserting it *without* also reaching the wallet's key handler underneath.

**`test_scan.py`**: the whole scan path against Chromium's fake camera, run
twice. `qr.y4m` is the digit-based SeedQR; `qr-compact.y4m` is the raw-bytes
CompactSeedQR, which is the case that breaks first if any layer decides a payload
is text. Both encode the same seed, so both must reach `SeedFinalizeScreen` on
the same fingerprint. Both videos open on blank frames, and the test asserts
nothing is decoded during them.

**`test_scan_native.py`**: the `BarcodeDetector` branch, which the plain scan
test never reaches because desktop Chromium ships no Shape Detection API. A stub
detector is installed before any page script runs, and it always claims a QR and
always returns rubbish for `rawValue`, which is not artificial, since a real
`BarcodeDetector` handed a CompactSeedQR returns mojibake either way.

Two phases, and the first is the point of the file: **camera pointed at a blank
wall, native claiming a QR on every frame, and the wallet must load nothing at
all.** An earlier version fell back to `rawValue` here and reached a real-looking
fingerprint, `17d9884b`, from pure garbage: a seed that was never in front of
the camera. If that phase ever passes by reporting a seed, the simulator is
inventing keys, which is the worst thing a bitcoin-adjacent tool can do. The
second phase then holds up a real CompactSeedQR and requires the correct seed
anyway, because jsQR re-reads the frame for its actual bytes.

**`run.py`'s `same_seed` step**: after the scan tests, the screen each of the
three runs ended on is compared byte for byte with the other two, and then with a
committed baseline. One seed, encoded three ways and read down two different
decoder paths, must end on one rendered fingerprint. It has been identical across
every run so far, including runs against differently built `wallet.zip` files, so
a difference means something real changed.

What is compared is `scan-screen-*.png`: the 320x240 canvas SeedSigner's own
renderer drew, read back out of the canvas rather than photographed. The
whole-page `scan-proof-*.png` screenshots are still written and are the thing to
look at when this fails, but they are not what is asserted on. A page screenshot
also holds the title, the amber warning box, the tray labels and the hint line,
all drawn with whatever fonts the machine has and none of them anything
`wallet.zip` can influence, so comparing those went red on hosts where nothing
was wrong: once on a font difference across the whole header, once on five pixels
differing by one channel value at an antialiased corner of the warning box while
the device area was byte-identical.

Agreeing with each other is not enough; three runs of a wallet that derived the
seed wrongly would agree perfectly. `baseline/screen-b2269592.png` is the anchor:
the same capture, of `SeedFinalizeScreen` showing the BIP39 test vector's master
fingerprint `b2269592`. It is committed as a picture rather than as a digest so
that the anchor can be audited by opening it. Regenerate it only when the wallet
is meant to draw something different, or when the Chromium that encodes the PNG
changes underneath it:

    python3 test/run.py scan
    cp test/artifacts/scan-screen-qr.png test/baseline/screen-b2269592.png

and look at the file before committing it. A baseline nobody read anchors
nothing.

**`test_cards_browser.py`**: the same card story as `test_cards.py`, but through
`wallet.html` and the real tray: an empty reader ends in a warning rather than a
hang, Card A reaches the Python side with Card A's UID, Card B with a different
one, and Card A put back is still the same card. The UID in the log is the one
pysatochip derived from the APDUs the card answered, so it is evidence about what
the wallet saw rather than about what was clicked.

**`test_cards_seed.py`**: the whole save-a-seed path through the wallet's own
screens: initialise a blank card with a PIN, scan the BIP39 test vector, hand it
to the card, then come back later (new connector, new applet selection, new PIN)
and read extended keys back off it into a wallet descriptor. Two independent
oracles: the card announces the master fingerprint it derived, which has to be
the vector's `b2269592`, and the wallet announces the screens it reached, where
`SeedExportXpubDetailsScreen` is only reachable if pysatochip recovered the right
key from *both* signatures on *every* answer, since it raises otherwise.

One of its checks asserts a bug on purpose. At the pinned tag the import screen
cannot report success: `card_bip32_import_seed()` returns the authentikey and
the view unpacks it as `(response, sw1, sw2)`, so a successful import is what
raises `TypeError`. The card is seeded either way. The check is there so that the
day upstream fixes it, this fails and says so.

**`test_cards_seedkeeper.py`** -- the two flows the SeedSigner+ Smartcard is sold
for, on a **SeedKeeper**, end to end through the wallet's own screens: scan the
BIP39 test vector, save it to a blank card (which the wallet initialises with a
PIN on the way), discard it from the wallet entirely, and load it back off the
card.

Three oracles, and they are independent. The card announces what it stored and
what it exported, from the Python side of the APDU boundary, and the type,
subtype, label and length it reports have to be the `Masterseed` layout the wallet
claims to write. The wallet announces the screens it reached, and getting to the
seed screen at all means pysatochip recovered the card's authentikey from the
signature over the header and the secret, because it raises rather than returning
if it cannot. And the seed the wallet ends up holding is compared, as a digest of
the device canvas, against the one it held after the scan earlier in the same
run: one seed, in by camera and back off a card, on one rendered screen. The
digest is taken from the canvas rather than from a screenshot so that nothing
about the surrounding page or its fonts can enter into it.

It also reloads the page at the end and requires three factory-fresh SeedKeepers,
because card state living only in memory is a decision rather than an oversight.

**`test_cards_seedkeeper_descriptor.py`** -- the other flow the card is sold for,
a **multisig wallet descriptor** travelling on a SeedKeeper instead of being
scanned. Neither of the wallet's two screens for it can work at the pinned tag,
and this file is where that is pinned down rather than discovered again later.

A 2 of 3 over three published BIP39 vectors goes in by camera, so `2 of 3` and
three fingerprints on the wallet's own screen say embit really parsed it. Then
*Save MultiSig Descriptor* is driven to the wall: a SeedKeeper v2 files a
descriptor under a secret type pysatochip 0.17.0 has no name for, so
`make_header("Descriptor", ...)` raises `KeyError: 'Descriptor'` and the wallet
puts up an error reading exactly that. The checks are that the success screen is
unreachable and that **the card was never asked to store anything**: a simulator
that answered a request the wallet never made would be inventing the flow rather
than running it. *Load MultiSig Descriptor* is blocked by the same missing entry
from the other side, and ends on the wallet's own "No Descriptors" for a card it
did read.

Everything the card would need is proved next door, in `test_cards.py`: the same
descriptor stored as type `0xC1`, read back byte for byte, its header parsed by
pysatochip and its cost checked against upstream's own size arithmetic. The fix
is a line in somebody else's package, so on the day it lands these checks fail
and say so.

## Supporting files

- `harness.py`: where to point the tests, and the log reader they share.
- `serve.py`: a static server that sends COOP and COEP. Without cross-origin
  isolation `SharedArrayBuffer` is not constructible and the wallet hangs before
  it draws anything, so `python3 -m http.server` cannot serve this page at all.
  It overlays several directories so a checkout is served without being copied
  anywhere first.
- `make_qr_y4m.py`: writes the `.y4m` videos Chromium's fake camera plays,
  using the wallet's own vendored `qrcode` out of `wallet.zip` so the QR under
  test is drawn by the library SeedSigner draws one with. The seed is the
  standard BIP39 test vector "army van defense …", and the multisig descriptor
  is derived from that vector and two more out of BIP39's own test file, so it
  is visibly three published seeds rather than a string somebody typed. Nothing
  about any of them is secret and nothing should ever hold value.
- `run.py`: the runner described above.
