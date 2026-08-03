"""
Take a 2 of 3 multisig wallet descriptor to a simulated SeedKeeper through the
wallet's own screens, and find out how far it gets.

This is the flow after the seed one: a multisig setup that travels on a card
instead of being scanned, so a signer picks up the quorum, the xpubs and the
derivation paths from the SeedKeeper it already carries. The wallet has the
screens for it, `ToolsSeedkeeperSaveDescriptorView` and
`ToolsSeedkeeperLoadDescriptorView`, and the simulated card has everything they
need: test_cards.py stores this exact descriptor on one, reads it back byte for
byte and checks the header with pysatochip's own parser.

**Neither screen can work at the pinned tag, and the reason is one missing
dictionary entry.** A SeedKeeper v2 files a descriptor under its own secret
type, and pysatochip 0.17.0 -- the version this fork's requirements.txt pins,
and the newest one published -- has no name for that type:

    SEEDKEEPER_DIC_TYPE = {0x10: 'Masterseed', ..., 0xC0: 'Data'}

So the save screen, which asks for that name by string:

    File "seedsigner/views/smartcard_views.py", line 2555, in run
      header = Satochip_Connector.make_header(secret_type, "Plaintext export allowed", secret_label)
    File "pysatochip/CardConnector.py", line 2774, in make_header
      itype = dict_swap_keys_values(SEEDKEEPER_DIC_TYPE)[secret_type]
    KeyError: 'Descriptor'

raises on every v2 card, before a single byte of the descriptor reaches one.
`secret_type` is "Descriptor" because line 2484 read `protocol_minor_version`
off the card and got 2; a v1 card takes the other branch and stores the
descriptor as a Password, which pysatochip does have a name for. The simulated
card reports 2 because that is what the card this is a simulator of reports, and
what the seed flow needs it to report.

The load screen is blocked by the same missing entry from the other side: it
selects a secret with `stype == "Descriptor"`, and `SEEDKEEPER_DIC_TYPE.get()`
cannot return that string for any type byte any card could send.

Nothing here works around either. The fix is a line in somebody else's package,
so the flows are driven to the wall and the wall is asserted: the wallet must
put a warning up where its success screen belongs, and the card must not have
been asked to store anything. The day pysatochip learns the name, these checks
fail and say so.

What is proved on the way is still worth having. The descriptor is scanned in as
a QR, so `Descriptor.from_string` and `get_multisig_policy` really did parse a 2
of 3 over three keys; the card really is initialised with a PIN by the wallet
and really is listed by both screens; and the "No Descriptors" the load screen
ends on is the wallet's own answer about a card it read, not a card it failed to
talk to.

The three seeds behind the descriptor are published BIP39 test vectors, derived
in make_qr_y4m.multisig_descriptor(). Nothing about them is secret and nothing
should ever hold value.
"""

import os
import sys

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

        # --- try to save it to the card ---------------------------------------
        print("\nsaving it to the SeedKeeper")
        window = wait_screen(log, "MainMenuScreen", window, 120, "the wallet to come home")
        open_seedkeeper_menu(page, log)
        press(page, "ArrowDown", 4)         # secrets, password, delete, load -> [save]
        saving = log.mark()
        press(page, "Enter")

        # The label the descriptor would be filed under. The keyboard opens
        # empty here, unlike the seed flow's, which offers the fingerprint.
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

        # And here is the wall. See this file's header: make_header() is asked
        # for a secret type pysatochip 0.17.0 has no number for, so a warning
        # goes up where the success screen belongs. That success screen is
        # another LargeIconStatusScreen, so the mark taken while the card-setup
        # one was up is what makes its absence say anything.
        window = wait_screen(log, "WarningScreen", set_up, 240,
                             "the wallet to finish with the save")
        check("the wallet's own success screen is unreachable (upstream bug, see header)",
              log.seen(r"display\(\) enter: LargeIconStatusScreen", set_up) is None)
        check("and the card was never asked to store anything",
              log.seen(r"\[card\] Card A stored secret", saving) is None,
              "a simulator that answered a request the wallet never made would "
              "be inventing the flow rather than running it")
        page.screenshot(path=harness.artifact("descriptor-save-failed.png"))
        # The tray repaints on a timer rather than on a message, so a pill read
        # the instant the wallet finished with the card can still be the old one.
        # Here that would be a pass for the wrong reason, so wait for a repaint.
        page.wait_for_timeout(800)
        check("the card is still carrying nothing", pill(page, 0) == "initialised",
              pill(page, 0))
        press(page, "Enter")

        # --- and try to load one back off it ----------------------------------
        # The card is empty, so this is the wallet's own answer about a card it
        # read: the load screen lists the headers, finds no descriptor among
        # them, and says so. It is as far as this half goes at the pinned tag.
        print("\nloading one back off the SeedKeeper")
        window = wait_screen(log, "ButtonListScreen", window, 180, "the SeedKeeper menu")
        press(page, "ArrowDown", 3)         # secrets, password, delete -> [load]
        reading = log.mark()
        press(page, "Enter")

        window = wait_screen(log, "WarningScreen", reading, 240,
                             "the wallet's answer about the card")
        check("the wallet reports no descriptors on a card that has none", True)
        check("and it never got one off the card",
              log.seen(r"\[card\] Card A exporting secret", reading) is None)
        check("nor was one refused, which would be a different story",
              log.seen(r"\[card\] Card A refused", reading) is None)
        page.screenshot(path=harness.artifact("descriptor-load-empty.png"))

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
