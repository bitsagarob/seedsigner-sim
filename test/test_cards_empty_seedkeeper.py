"""
Load a seed from a SeedKeeper that has not got one.

The card a visitor meets first is blank, so the first thing they do with "From
SeedKeeper" is set it up, and the card they then have is initialised and empty.
Upstream's SeedKeeperSelectView.run() reads self.seed at two points that both
come before the only line that assigns it -- the assignment is far down the
success path, after a secret has been exported -- so both of the ordinary ways
of leaving that screen raised AttributeError instead of leaving it: the card
holding nothing, which is this test, and back at the secret list. What the
visitor got was a System Error naming an attribute.

Still open on upstream's master, so wallet-worker.js gives the attribute the
value the assignment further down uses, at construction. The pinned tree is not
touched, the same as every other seam here.

The whole walk is by button, and it is the walk from the bug report: insert the
blank card, ask to load from it, set a PIN, and be told there is nothing on it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report

from playwright.sync_api import sync_playwright

SHOT = harness.artifact("seedkeeper-empty.png")

# Any four of one character is a PIN a Satochip accepts, and the keyboard opens
# on the first key, so four presses type one. KEY3 saves it. Same trick as
# test_cards_seed.py's type_pin, which explains it at length.
PIN = ["Enter"] * 4 + ["3"]

# Home -> Seeds -> From SeedKeeper. Seeds is to the right of Scan on the 2x2
# home grid, and From SeedKeeper is the fourth entry under Load a Seed.
TO_SEEDKEEPER = ["ArrowRight", "Enter", "ArrowDown", "ArrowDown", "ArrowDown", "Enter"]


def press(page, key, gap=1000):
    page.keyboard.press(key)
    page.wait_for_timeout(gap)


def answer_the_card(page, log, tries=25):
    """Answer whatever the card setup puts up, until the view is done with.

    Driven by what is on the screen rather than by a fixed run of presses,
    because how many times a blank card asks for a PIN on the way to being set
    up is not a fixed number, and a sequence that assumed one was a test that
    passed for the wrong reason the moment it changed.
    """
    for _ in range(tries):
        if log.seen(r"View\.run exit: SeedKeeperSelectView"):
            return "returned"
        if log.seen(r"View\.run RAISED SeedKeeperSelectView"):
            return "raised"
        if log.last_screen() == "SeedAddPassphraseScreen":
            for key in PIN:
                press(page, key)
        else:
            press(page, "Enter")
    return log.last_screen() or "nothing"


def main() -> int:
    if harness.FIRMWARE != "smartcard":
        print("  skipped: stock has no smartcard support")
        return 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1200, "height": 950})
        page = context.new_page()
        log = Log(page)

        page.goto(harness.wallet_url())
        log.wait(r"display\(\) enter: MainMenuScreen", 240, "the wallet to boot")
        check("the missing attribute is put back at boot",
              log.seen(r"patched in SeedKeeperSelectView\.seed") is not None)

        log.wait(r"\[card\] tray attached", 60, "the card tray to reach Python")
        page.locator(".cardtray-card").nth(0).click()
        page.wait_for_timeout(1500)

        for key in TO_SEEDKEEPER:
            press(page, key)
        log.wait(r"View\.run enter: SeedKeeperSelectView", 60, "the SeedKeeper view")
        check("asking to load from the card reaches the view", True)

        # The whole bug: does the view leave by returning a Destination, or by
        # raising? The trace says which outright, which is a better oracle than
        # naming the screen that comes next -- that depends on how many times
        # the card asked for a PIN on the way in.
        outcome = answer_the_card(page, log)
        harness.save_screen(page, SHOT)
        print(f"  screen: {SHOT}")
        check("an empty card leaves the view by returning, not by raising",
              outcome == "returned", outcome)
        check("nothing raised anywhere on the way",
              log.seen(r"View\.run RAISED") is None)
        check("and no System Error was shown",
              log.seen(r"display\(\) enter: UnhandledException") is None)

        browser.close()

    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
