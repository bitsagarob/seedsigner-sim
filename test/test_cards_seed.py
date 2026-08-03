"""
Put a seed on a simulated Satochip through the wallet's own screens, and read it
back off the card afterwards.

The tray defaults to a SeedKeeper, so this starts by swapping Card A for a
Satochip -- the type is chosen before the card goes in, because a card is one
thing or the other.

The whole of the save-a-seed path runs here: the wallet initialises a blank card
with a PIN, scans the BIP39 test vector with the camera, hands the seed to the
card, and then -- on a later trip through the menus, with a fresh CardConnector
and a fresh applet selection -- asks the card for extended keys and builds a
descriptor out of what comes back. Nothing between the seed and the descriptor is
faked at a higher level than the APDU.

Two oracles, and they are independent of each other. The card announces the
master fingerprint it derived from the bytes it was handed, which is checkable
against the published test vector; the wallet announces the screens it reached,
and reaching SeedExportXpubDetailsScreen means pysatochip verified both
signatures on every extended key the card answered with, because it raises
rather than returning if it cannot. The screenshots are evidence, not proof.

One assertion here is deliberately an assertion about a bug. The import screen
cannot report success at the pinned tag:

    File "seedsigner/views/smartcard_views.py", line 3643, in run
      _resp, sw1, sw2 = Satochip_Connector.card_bip32_import_seed(...)
    TypeError: cannot unpack non-iterable ECPubkey object

CardConnector.card_bip32_import_seed() returns the card's authentikey, not a
(response, sw1, sw2) triple -- that is the Keycard backend's signature, and
ToolsSatochipImportSeedView unpacks every backend's return value the same way.
So on a Satochip it is a *successful* import that raises. Nothing here works
around it: the card is seeded either way, because the APDU succeeded before the
wallet mishandled the answer, and the rest of this test goes on to use it. It is
present on upstream's dev tip too, so it is not something a newer pin fixes.

The seed is the standard BIP39 test vector "army van defense ...", the same one
the scan tests hold up. Nothing about it is secret and nothing should ever hold
value.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report
# Same wallet, same tray, same gestures; there is no reason for a second copy.
from test_cards_browser import press

from playwright.sync_api import sync_playwright

# The video the scan tests use. This test needs a seed in the wallet before it
# has anything to put on a card, and scanning one is how a user gets it there.
Y4M = harness.artifact("qr.y4m")

# What the wallet shows for that vector, and so what a card holding it has to
# derive. Anything else means the card was given the wrong bytes or derived from
# them wrongly.
FINGERPRINT = "b2269592"


def wait_screen(log, name, since, timeout, what):
    """Wait for a screen, and hand back a mark taken while it is still up.

    Marking here rather than after the keypress that leaves it is the difference
    between a reliable test and a flaky one: a screen that comes and goes inside
    the 220ms press() spends waiting would otherwise be marked past and waited
    for forever.
    """
    log.wait(r"display\(\) enter: " + name, timeout, what, since)
    return log.mark()


def type_pin(page, log, since):
    """Answer whichever PIN prompt is next.

    The keyboard opens with the cursor on the first key of the lowercase set, so
    four presses type four of that one character and KEY3 saves -- four being the
    shortest PIN a Satochip accepts. Which character it is does not matter as
    long as every prompt gets the same one.
    """
    window = wait_screen(log, "SeedAddPassphraseScreen", since, 120, "the PIN keyboard")
    press(page, "Enter", 4)
    press(page, "3")
    return window


def open_satochip_menu(page, log):
    """Home -> Tools -> Smartcard Tools -> Satochip Functions."""
    press(page, "ArrowDown")            # Scan -> Tools, a 2x2 grid
    press(page, "Enter")
    log.wait(r"display\(\) enter: ButtonListScreen", 30, "the Tools menu",
             max(0, log.mark() - 20))
    press(page, "ArrowDown", 3)         # new seed, new seed, passwords, [smartcard]
    press(page, "Enter")
    page.wait_for_timeout(600)
    press(page, "ArrowDown", 2)         # Common, SeedKeeper -> [Satochip]
    press(page, "Enter")
    page.wait_for_timeout(600)


def pill(page, index):
    return page.locator(".cardtray-card").nth(index).locator(".cardtray-pill").inner_text()


def pill_becomes(page, index, want, timeout=6.0):
    """Wait for a card's pill to say something, then answer whether it does.

    The tray repaints on a timer rather than on a message -- nothing waits on it,
    so it costs nothing to be a quarter of a second late -- which makes reading a
    pill the instant the wallet finished with the card a race.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pill(page, index) == want:
            return True
        page.wait_for_timeout(200)
    return False


def kind(page, index):
    return page.locator(".cardtray-kind").nth(index).inner_text()


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
        # The tray offers a SeedKeeper by default, which is the card the product
        # ships; this flow is the Satochip one, so swap the card before it goes
        # in. A card's type cannot change once it is in the reader.
        check("Card A is a SeedKeeper until told otherwise", kind(page, 0) == "SeedKeeper",
              kind(page, 0))
        page.locator(".cardtray-kind").nth(0).click()
        check("and the tray swaps it for a Satochip", kind(page, 0) == "Satochip", kind(page, 0))
        page.locator(".cardtray-card").nth(0).click()
        check("Card A starts blank", pill(page, 0) == "blank", pill(page, 0))
        check("its type is fixed while it is in the reader",
              page.locator(".cardtray-kind").nth(0).is_disabled())

        # --- initialise the card ---------------------------------------------
        print("\ngiving the blank card a PIN")
        window = log.mark()
        open_satochip_menu(page, log)
        press(page, "Enter")                # Initialise with Seed, first in the list

        # A blank card is asked for its PIN, told it has none, and then given
        # one twice over. That is upstream's order, not this test's.
        window = type_pin(page, log, window)
        window = wait_screen(log, "WarningScreen", window, 120,
                             "the uninitialised-card warning")
        press(page, "Enter")
        window = type_pin(page, log, window)         # new PIN
        window = type_pin(page, log, window)         # and again, to confirm
        window = wait_screen(log, "LargeIconStatusScreen", window, 120,
                             "the card to be set up")
        check("the wallet puts a PIN on the blank card", True)
        press(page, "Enter")
        check("the tray shows Card A initialised", pill_becomes(page, 0, "initialised"),
              pill(page, 0))

        # --- scan the seed the card is going to be given ----------------------
        print("\nscanning the test vector")
        window = wait_screen(log, "ButtonListScreen", window, 120, "the seed list")
        check("nothing has been put on the card yet",
              log.seen(r"\[card\] Card A seeded") is None)
        press(page, "Enter")                # Scan a seed, first with no seeds loaded
        window = wait_screen(log, "SeedFinalizeScreen", window, 240, "the scanned seed")
        check("the camera loads a seed into the wallet", True)
        press(page, "Enter")                # Done

        # --- put it on the card ----------------------------------------------
        print("\nsaving it to the card")
        importing = wait_screen(log, "ButtonListScreen", window, 180, "the seed list again")
        press(page, "Enter")                # the one loaded seed, now first in the list

        seeded = log.wait(r"\[card\] Card A seeded with (\d+) bytes, master fingerprint "
                          r"([0-9a-f]{8})", 240, "the card to take the seed", importing)
        check("the card derives the test vector's master key from what it was handed",
              seeded.group(2) == FINGERPRINT, f"{seeded.group(1)} bytes -> {seeded.group(2)}")

        # And then the wallet drops it. See this file's header: the import screen
        # cannot report success at the pinned tag, and this check exists so that
        # stops being silent -- if it starts failing, upstream fixed it.
        window = wait_screen(log, "WarningScreen", importing, 180,
                             "the wallet to finish with the import")
        check("the wallet's own success screen is unreachable (upstream bug, see header)",
              log.seen(r"TypeError: cannot unpack non-iterable ECPubkey object",
                       importing) is not None)
        page.screenshot(path=harness.artifact("cards-seed-imported.png"))
        press(page, "Enter")
        check("the card is seeded anyway, because the APDU succeeded",
              pill_becomes(page, 0, "seeded"), pill(page, 0))
        check("the other two cards are untouched",
              (pill(page, 1), pill(page, 2)) == ("blank", "blank"),
              f"{pill(page, 1)}, {pill(page, 2)}")

        # --- read it back ------------------------------------------------------
        print("\nreading it back off the card")
        window = wait_screen(log, "MainMenuScreen", window, 60, "the wallet to come home")
        open_satochip_menu(page, log)
        press(page, "ArrowDown", 2)         # Initialise, Export Xpub -> [Load as Descriptor]
        window = log.mark()
        press(page, "Enter")
        reading = wait_screen(log, "ButtonListScreen", window, 180, "the script type list")
        press(page, "Enter")                # Native Segwit, first in the list
        # Coming home from the last flow dropped the cached PIN, which is what
        # SETTING__CACHE_SCARD_PIN being off means, so the card asks again.
        window = type_pin(page, log, reading)

        window = wait_screen(log, "SeedExportXpubDetailsScreen", window, 300,
                             "the key the card derived")
        # Getting here at all is the assertion: card_bip32_get_xpub asks the card
        # for the authentikey and then for two extended keys, and pysatochip
        # raises rather than returning if it cannot recover the right key from
        # either signature on either answer.
        check("the wallet shows an xpub it read back off the card", True)
        check("nothing failed on the way",
              log.seen(r"display\(\) enter: WarningScreen", reading) is None)
        page.screenshot(path=harness.artifact("cards-seed-xpub.png"))
        press(page, "Enter")                # Confirm
        window = wait_screen(log, "LargeIconStatusScreen", window, 180,
                             "the descriptor to load")
        check("and builds a wallet descriptor out of it", True)
        press(page, "Enter")

        # --- and refuses to overwrite it ---------------------------------------
        print("\nand refuses to overwrite it")
        window = wait_screen(log, "MainMenuScreen", window, 60, "the wallet to come home")
        open_satochip_menu(page, log)
        window = log.mark()
        press(page, "Enter")                # Initialise with Seed, again
        window = type_pin(page, log, window)
        wait_screen(log, "WarningScreen", window, 240,
                    "the wallet to refuse a second seed")
        check("a seeded card is not offered a second seed", True)
        page.screenshot(path=harness.artifact("cards-seed-already.png"))

        # --- and forgets all of it on reload -----------------------------------
        # A card here holds its seed in one Python object and nowhere else: no
        # storage, no filesystem, nothing that outlives the tab. That is the
        # product decision, not an oversight, so it is worth a check.
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
