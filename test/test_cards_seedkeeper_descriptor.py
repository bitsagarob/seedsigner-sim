"""
Take a 2 of 3 multisig wallet descriptor to a simulated SeedKeeper through the
wallet's own screens, and find out how far it gets.

This is the flow after the seed one: a multisig setup that travels on a card
instead of being scanned, so a signer picks up the quorum, the xpubs and the
derivation paths from the SeedKeeper it already carries. The wallet has two
screens for it, `ToolsSeedkeeperSaveDescriptorView` and
`ToolsSeedkeeperLoadDescriptorView`, and they now end in different places:
**save works, load does not.**

This file used to assert that neither could work, and that was our fault rather
than upstream's. A SeedKeeper v2 files a descriptor under its own secret type,
0xC1, and the save screen asks for that type by name:

    header = Satochip_Connector.make_header("Descriptor", ...)

PyPI's pysatochip 0.17.0, which this fork's requirements.txt asks for and which
this repository used to ship, has no name for 0xC1: its SEEDKEEPER_DIC_TYPE
stops at 0xC0 'Data', so that line raised `KeyError: 'Descriptor'` before a byte
reached the card. But the device never runs that package. The SeedSigner OS
image builds 3rdIteration/pysatochip from GitHub at the tag 0.6a and deletes
requirements.txt from the rootfs, and that tree has `0xC1: 'Descriptor'`. So the
simulator was failing at a wall that does not exist on the hardware it claims to
be, which is the one kind of wrong this project cannot afford. The build now
pins what the device builds; see the pysatochip note in
build/build-wallet-zip.sh.

**Saving is a real flow now**, and it is driven to the end here: the card is
handed a 0xC1 secret carrying the whole descriptor behind a two-byte length, and
the wallet reaches its own Success screen.

**Loading hits a different wall, and this one is upstream's.** Every view that
reads a SeedKeeper's headers by name -- the load screen among them -- uses
`SEEDKEEPER_DIC_TYPE`, and `seedsigner/views/smartcard_views.py` never binds it:

    try:
        from pysatochip import satochip
        from pysatochip.exception import UnexpectedSW12Error
        from pysatochip.JCconstants import SEEDKEEPER_DIC_TYPE, ...
        from pysatochip.satochip_protocol_helper import format_sw_error
    except ImportError:
        pass

Three of those four modules exist in no published pysatochip: not in PyPI
0.17.0, not at the tag the device builds, not on the fork's own default branch.
So the block always raises, `pass` swallows it, and the names in it -- including
the one JCconstants really does have -- are never defined. The load screen
raises `NameError: name 'SEEDKEEPER_DIC_TYPE' is not defined` on the first
header it reads, and its own `except` puts that sentence on a warning screen.
That is asserted below, and the missing modules are checked out of the wallet
zip so the reason is evidence rather than a story.

Save survives only because it reads the headers of a card it has just found
empty, so its loop body never runs. Saving a second descriptor to the same card
raises the same NameError.

Nothing here works around any of it, and nothing patches `seedsigner/`. The day
upstream fixes that import, the load check fails and says so.

The three seeds behind the descriptor are published BIP39 test vectors, derived
in make_qr_y4m.multisig_descriptor(). Nothing about them is secret and nothing
should ever hold value.
"""

import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import make_qr_y4m
from harness import Log, check, report
# Same wallet, same tray, same gestures; there is no reason for a second copy.
from test_cards_browser import press
from test_cards_seed import kind, pill, pill_becomes, type_pin, wait_screen

from playwright.sync_api import sync_playwright

# The 2 of 3 the camera holds up, and the same string test_cards.py puts on a
# card at the APDU level.
DESCRIPTOR = make_qr_y4m.multisig_descriptor()
Y4M = harness.artifact("descriptor.y4m")

# What the descriptor label keyboard is given. It opens empty, unlike the seed
# flow's, which offers the seed's fingerprint, so the label is four presses of
# whichever key the cursor started on.
LABEL = "aaaa"

# The modules smartcard_views.py asks for at import time. Checked against the
# zip the browser is about to run, because "the import fails" is the whole
# explanation for the load screen's NameError and it should not be taken on
# trust.
IMPORTED_AT_MODULE_SCOPE = [
    "pysatochip/satochip.py",
    "pysatochip/exception.py",
    "pysatochip/JCconstants.py",
    "pysatochip/satochip_protocol_helper.py",
]


def open_seedkeeper_menu(page, log):
    """Home -> Tools -> Smartcard Tools -> SeedKeeper Functions."""
    press(page, "ArrowDown")            # Scan -> Tools, a 2x2 grid
    press(page, "Enter")
    log.wait(r"display\(\) enter: ButtonListScreen", 30, "the Tools menu",
             max(0, log.mark() - 20))
    press(page, "ArrowDown", 3)         # new seed, new seed, passwords, [smartcard]
    press(page, "Enter")
    page.wait_for_timeout(600)
    press(page, "ArrowDown")            # Common -> [SeedKeeper]
    press(page, "Enter")
    page.wait_for_timeout(600)


def main() -> int:
    if not os.path.exists(Y4M):
        print(f"no {Y4M}: run make_qr_y4m.py first", file=sys.stderr)
        return 2

    wallet_zip = harness.find_asset("wallet-smartcard.zip")
    if wallet_zip is None:
        print("no wallet-smartcard.zip: build it first", file=sys.stderr)
        return 2
    with zipfile.ZipFile(wallet_zip) as archive:
        shipped = set(archive.namelist())
    absent = [name for name in IMPORTED_AT_MODULE_SCOPE if name not in shipped]
    check("three of the four modules smartcard_views.py imports do not exist, "
          "so none of the names in that import are ever defined",
          absent == ["pysatochip/satochip.py",
                     "pysatochip/exception.py",
                     "pysatochip/satochip_protocol_helper.py"],
          f"absent from the wallet zip: {absent}")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-video-capture={Y4M}",
        ])
        context = browser.new_context(
            permissions=["camera"],
            viewport={"width": 900, "height": 1100},
        )
        page = context.new_page()
        log = Log(page)
        page.goto(harness.wallet_url())

        log.wait(r"display\(\) enter: MainMenuScreen", 300, "the wallet to boot")
        log.wait(r"\[card\] tray attached", 30, "the card tray to reach Python")
        check("the tray offers a SeedKeeper without being asked",
              kind(page, 0) == "SeedKeeper", kind(page, 0))
        page.locator(".cardtray-card").nth(0).click()
        check("Card A starts blank", pill(page, 0) == "blank", pill(page, 0))

        # --- a quorum, by camera, so there is something to save ---------------
        print("\nscanning the 2 of 3")
        print(f"  {len(DESCRIPTOR)} characters: {DESCRIPTOR[:48]}...")
        window = log.mark()
        press(page, "Enter")                # Home -> Scan, first in the 2x2 grid
        window = wait_screen(log, "MultisigWalletDescriptorScreen", window, 240,
                             "the descriptor the camera read")
        # Reaching this screen is the assertion: ScanView only routes here after
        # embit's Descriptor.from_string() has parsed the payload, and the screen
        # itself asks get_multisig_policy() for the threshold and the key count,
        # which raises rather than guessing if the descriptor is not a multisig.
        check("the camera loads a 2 of 3 into the wallet", True)
        page.screenshot(path=harness.artifact("descriptor-scanned.png"))
        press(page, "Enter")                # OK, which goes home

        # --- save it to the card ----------------------------------------------
        print("\nsaving it to the SeedKeeper")
        window = wait_screen(log, "MainMenuScreen", window, 120, "the wallet to come home")
        open_seedkeeper_menu(page, log)
        press(page, "ArrowDown", 4)         # secrets, password, delete, load -> [save]
        saving = log.mark()
        press(page, "Enter")

        # The label the descriptor is filed under. The keyboard opens empty here,
        # unlike the seed flow's, which offers the fingerprint.
        window = wait_screen(log, "SeedAddPassphraseScreen", saving, 180,
                             "the descriptor label keyboard")
        press(page, "Enter", 4)             # four of whichever key it opened on
        press(page, "3")                    # save

        # Only now does the wallet reach for the card, and it finds a blank one:
        # PIN, told there is none, then a new one twice over. Upstream's order.
        window = type_pin(page, log, window)
        window = wait_screen(log, "WarningScreen", window, 180,
                             "the uninitialised-card warning")
        press(page, "Enter")
        window = type_pin(page, log, window)         # new PIN
        window = type_pin(page, log, window)         # and again, to confirm
        set_up = wait_screen(log, "LargeIconStatusScreen", window, 120,
                             "the card to be set up")
        check("the wallet puts a PIN on the blank SeedKeeper", True)
        check("the tray shows Card A initialised", pill_becomes(page, 0, "initialised"),
              pill(page, 0))
        press(page, "Enter")

        # What the card was actually handed, from the Python side of the APDU
        # boundary. A v2 descriptor is its own secret type with a two-byte
        # big-endian length in front of the utf-8, which is why the payload is
        # two bytes longer than the descriptor: the v1 layout is a Password with
        # one length byte, and 448 characters do not fit behind one.
        stored = log.wait(r"\[card\] Card A stored secret (\d+), type 0x(\w+) subtype 0x(\w+), "
                          r"label '([^']*)', (\d+) bytes, fingerprint ([0-9a-f]{8})",
                          240, "the card to take the descriptor", saving)
        check("the card stores it as a Descriptor, type 0xC1",
              stored.group(2) == "c1", f"type 0x{stored.group(2)}")
        check("under the label the wallet asked for", stored.group(4) == LABEL,
              stored.group(4))
        check("carrying the whole descriptor behind a two-byte length",
              int(stored.group(5)) == len(DESCRIPTOR) + 2,
              f"{stored.group(5)} bytes for {len(DESCRIPTOR)} characters")

        window = wait_screen(log, "LargeIconStatusScreen", set_up, 240,
                             "the wallet to report the descriptor saved")
        check("the wallet reaches its own success screen", True)
        check("nothing failed on the way",
              log.seen(r"display\(\) enter: WarningScreen", set_up) is None)
        page.screenshot(path=harness.artifact("descriptor-saved.png"))
        # The tray repaints on a timer rather than on a message, so a pill read
        # the instant the wallet finished with the card can still be the old one.
        check("the tray shows Card A carrying something",
              pill_becomes(page, 0, "seeded"), pill(page, 0))
        press(page, "Enter")

        # --- and try to read it back off it -----------------------------------
        # Here is the wall, and see this file's header for why it is upstream's
        # rather than ours: the view reads SEEDKEEPER_DIC_TYPE on the first
        # header the card hands it, that name was never bound, and its own except
        # puts the NameError on the screen.
        print("\nloading it back off the SeedKeeper")
        window = wait_screen(log, "ButtonListScreen", window, 180, "the SeedKeeper menu")
        press(page, "ArrowDown", 3)         # secrets, password, delete -> [load]
        reading = log.mark()
        press(page, "Enter")

        window = wait_screen(log, "WarningScreen", reading, 240,
                             "the wallet's answer about the card")
        check("the load screen cannot read back what the save screen just wrote "
              "(upstream bug, see header)", True,
              "the wallet puts up: name 'SEEDKEEPER_DIC_TYPE' is not defined")
        check("it never got as far as asking the card for the descriptor",
              log.seen(r"\[card\] Card A exporting secret", reading) is None)
        check("nor was one refused, which would be a different story",
              log.seen(r"\[card\] Card A refused", reading) is None)
        page.screenshot(path=harness.artifact("descriptor-load-failed.png"))
        page.locator("#screen").screenshot(
            path=harness.artifact("descriptor-load-error.png"))

        # --- and forgets all of it on reload -----------------------------------
        # A card here holds its secrets in one Python object and nowhere else: no
        # storage, no filesystem, nothing that outlives the tab.
        print("\nand forgets it on reload")
        window = log.mark()
        page.reload()
        log.wait(r"display\(\) enter: MainMenuScreen", 300, "the wallet to boot again", window)
        check("reloading the page hands back three factory-fresh cards",
              page.locator(".cardtray-pill", has_text="blank").count() == 3,
              page.locator(".cardtray-row").inner_text().replace("\n", " | "))
        check("and they are SeedKeepers again, which is the default",
              [kind(page, i) for i in range(3)] == ["SeedKeeper"] * 3,
              str([kind(page, i) for i in range(3)]))

        print("\ncard log:")
        log.dump("[card]")

        browser.close()

    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
