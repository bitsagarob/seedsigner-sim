"""
Save a seed onto a simulated SeedKeeper and load it back, through the wallet's
own screens.

This is the flow the SeedSigner+ Smartcard is sold for: the seed lives on the
card, and the signer picks it up from there instead of from a QR or a keyboard.
Nothing between the mnemonic and the card is faked at a higher level than the
APDU -- the wallet lays the secret out itself, pysatochip frames it, and the
simulated card stores the bytes and hands them back under the export rights they
were stored with.

Three oracles, and they are independent of each other:

  * the card announces what it stored, and what it exported, from the Python
    side of the APDU boundary;
  * the wallet announces the screens it reached, and the export screen is only
    reachable if pysatochip recovered the card's authentikey from the signature
    over the header and the secret, because it raises rather than returning if
    it cannot;
  * the seed the wallet ends up holding is compared, as rendered pixels of the
    device's own screen, against the one it held after scanning the QR earlier
    in the same run. One seed goes in by camera and comes back off a card, and
    the two SeedFinalizeScreens have to be the same screen.

That last one is why the seed is scanned rather than typed: the scan gives a
before-image taken from the same page, in the same run, so the comparison needs
no baseline file and cannot drift with a font.

The seed is the standard BIP39 test vector "army van defense ...", the same one
the scan tests hold up. Nothing about it is secret and nothing should ever hold
value.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report
# Same wallet, same tray, same gestures; there is no reason for a second copy.
from test_cards_browser import press
from test_cards_seed import kind, pill, pill_becomes, type_pin, wait_screen

from playwright.sync_api import sync_playwright

# The video the scan tests use. This test needs a seed in the wallet before it
# has anything to put on a card, and scanning one is how a user gets it there.
Y4M = harness.artifact("qr.y4m")

# What the wallet shows for that vector, and so what the card is labelled with:
# SaveToSeedkeeperView offers the seed's fingerprint as the secret's label.
FINGERPRINT = "b2269592"


def device(page, name):
    """A digest of the device's screen, and a screenshot of it to look at.

    The digest is taken from the canvas itself rather than from the screenshot,
    because the canvas holds the 320x240 the wallet drew while the screenshot
    holds whatever CSS scaled that to -- and the scaled edge pixels move by one
    when anything else on the page changes height, which the tray does.

    It waits first. A frame crosses from the worker as a message and is painted
    on the page's own thread, while the line saying a screen was entered is
    posted before that screen has drawn anything, so reading the instant the log
    says so can catch the previous frame or half of this one.
    """
    page.wait_for_timeout(1000)
    page.locator("#screen").screenshot(path=harness.artifact(name))
    return page.evaluate("""async () => {
      const canvas = document.getElementById("screen");
      const pixels = canvas.getContext("2d").getImageData(
        0, 0, canvas.width, canvas.height).data;
      const digest = await crypto.subtle.digest("SHA-256", pixels);
      return Array.from(new Uint8Array(digest))
        .map(b => b.toString(16).padStart(2, "0")).join("");
    }""")


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

        # --- a seed, by camera, so there is something to save -----------------
        print("\nscanning the test vector")
        window = log.mark()
        press(page, "Enter")                # Home -> Scan, first in the 2x2 grid
        window = wait_screen(log, "SeedFinalizeScreen", window, 240, "the scanned seed")
        scanned = device(page, "seedkeeper-scanned.png")
        check("the camera loads the vector into the wallet", True)
        press(page, "Enter")                # Done

        # --- save it to the card ----------------------------------------------
        print("\nsaving it to the SeedKeeper")
        window = wait_screen(log, "SeedOptionsScreen", window, 180, "the seed's menu")
        press(page, "ArrowDown", 3)         # scan psbt, xpub, explorer -> [backup]
        press(page, "Enter")
        window = wait_screen(log, "ButtonListScreen", window, 120, "the backup menu")
        press(page, "ArrowDown")            # view words -> [to SeedKeeper]
        press(page, "Enter")

        # A blank card is asked for its PIN, told it has none, and then given one
        # twice over. That is upstream's order, not this test's.
        window = type_pin(page, log, window)
        window = wait_screen(log, "WarningScreen", window, 180,
                             "the uninitialised-card warning")
        press(page, "Enter")
        window = type_pin(page, log, window)         # new PIN
        window = type_pin(page, log, window)         # and again, to confirm
        window = wait_screen(log, "LargeIconStatusScreen", window, 120,
                             "the card to be set up")
        check("the wallet puts a PIN on the blank SeedKeeper", True)
        check("the tray shows Card A initialised", pill_becomes(page, 0, "initialised"),
              pill(page, 0))
        press(page, "Enter")

        # The label the secret is filed under. The keyboard opens with the seed's
        # own fingerprint already in it, so KEY3 accepts what the wallet offered.
        saving = wait_screen(log, "SeedAddPassphraseScreen", window, 120,
                             "the label keyboard")
        press(page, "3")

        stored = log.wait(r"\[card\] Card A stored secret (\d+), type 0x(\w+) subtype 0x(\w+), "
                          r"label '([^']*)', (\d+) bytes, fingerprint ([0-9a-f]{8})",
                          240, "the card to take the secret", saving)
        check("the card stores it as a Masterseed, subtype 1",
              (stored.group(2), stored.group(3)) == ("10", "01"),
              f"type 0x{stored.group(2)} subtype 0x{stored.group(3)}")
        check("labelled with the seed's own fingerprint",
              stored.group(4) == FINGERPRINT, stored.group(4))
        check("carrying the master seed, the entropy behind it and a passphrase",
              int(stored.group(5)) == 1 + 64 + 1 + 1 + 16 + 1,
              f"{stored.group(5)} bytes")

        window = wait_screen(log, "LargeIconStatusScreen", saving, 180,
                             "the wallet to report the secret saved")
        check("nothing failed on the way",
              log.seen(r"display\(\) enter: WarningScreen", saving) is None)
        page.screenshot(path=harness.artifact("seedkeeper-saved.png"))
        check("the tray shows Card A carrying something",
              pill_becomes(page, 0, "seeded"), pill(page, 0))
        check("the other two cards are untouched",
              (pill(page, 1), pill(page, 2)) == ("blank", "blank"),
              f"{pill(page, 1)}, {pill(page, 2)}")
        press(page, "Enter")

        # --- forget it, so that what comes back can only come off the card ----
        print("\ndiscarding the seed the wallet is holding")
        window = wait_screen(log, "SeedOptionsScreen", window, 120, "the seed's menu again")
        press(page, "ArrowDown", 5)         # psbt, xpub, explorer, backup, bip85 -> [discard]
        press(page, "Enter")
        window = wait_screen(log, "WarningScreen", window, 120, "the discard warning")
        press(page, "ArrowDown")            # keep -> [discard]
        press(page, "Enter")
        window = wait_screen(log, "MainMenuScreen", window, 120, "the wallet to come home")
        check("the wallet is holding no seed", True)

        # --- and read it back off the card ------------------------------------
        print("\nloading it back off the SeedKeeper")
        press(page, "ArrowRight")           # Scan -> Seeds, a 2x2 grid
        press(page, "Enter")                # no seeds loaded, so straight to Load a Seed
        window = wait_screen(log, "ButtonListScreen", window, 120, "the load-a-seed menu")
        press(page, "ArrowDown", 3)         # SeedQR, 12-word, 24-word -> [From SeedKeeper]
        reading = log.mark()
        press(page, "Enter")
        # Coming home from the last flow dropped the cached PIN, which is what
        # SETTING__CACHE_SCARD_PIN being off means, so the card asks again.
        window = type_pin(page, log, reading)

        window = wait_screen(log, "ButtonListScreen", window, 180, "the card's list of secrets")
        page.screenshot(path=harness.artifact("seedkeeper-secrets.png"))
        press(page, "Enter")                # the one secret on the card

        exported = log.wait(r"\[card\] Card A exporting secret (\d+) in the clear, label '([^']*)'",
                            240, "the card to hand the secret back", reading)
        check("the card exports the secret it was asked for",
              exported.group(2) == FINGERPRINT, exported.group(2))
        check("and it never refused one",
              log.seen(r"\[card\] Card A refused", reading) is None)

        window = wait_screen(log, "SeedFinalizeScreen", window, 240,
                             "the seed the card gave back")
        # Getting here at all says pysatochip accepted the export: it recovers a
        # key from the signature over the header and the secret and raises unless
        # that key is the authentikey the card answered with earlier.
        check("the wallet reaches its own seed screen with what the card sent", True)
        check("nothing failed on the way",
              log.seen(r"display\(\) enter: WarningScreen", reading) is None)
        loaded = device(page, "seedkeeper-loaded.png")
        check("and it is the same seed the camera loaded, pixel for pixel",
              loaded == scanned, f"{scanned[:16]} vs {loaded[:16]}")

        # --- and forgets all of it on reload -----------------------------------
        # A card here holds its secrets in one Python object and nowhere else: no
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
