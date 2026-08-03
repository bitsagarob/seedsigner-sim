"""
Change a setting through the wallet's own menus and see that it takes.

This exists because it did not, and nothing here noticed. Settings.save()
debounces its write behind a threading.Timer, the worker shimmed Thread but not
Timer, and so every settings change died on a System Error screen: the network
selector among them, which is the first thing anyone testing against a test
network has to touch. Fifteen tests passed throughout.

The wallet's settings live in an in-memory filesystem, so nothing here survives
a reload, and that is the point: this asks whether the change works at all, not
whether it persists.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import check, report

from playwright.sync_api import sync_playwright

# Home is a two by two grid, Scan and Seeds above Tools and Settings, so down
# then right is Settings whichever tile the wallet starts on.
TO_SETTINGS = ("ArrowDown", "ArrowRight", "Enter")

# Settings, then six down to Advanced, then the first entry inside it, which is
# the Bitcoin network. Chosen deliberately over the first setting in the list:
# this one has options the device is not already on, so accepting one is a real
# change, and it is the setting anybody pointing the simulator at a test network
# has to reach.
TO_NETWORK = ("ArrowDown",) * 6 + ("Enter", "Enter")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        log = harness.Log(page)
        page.goto(harness.wallet_url())
        log.wait("MainMenuScreen", 240, "the wallet to boot")

        for key in TO_SETTINGS:
            page.keyboard.press(key)
            page.wait_for_timeout(400)
        check("the settings menu opens", log.last_screen() == "ButtonListScreen",
              log.last_screen())

        since = log.mark()
        for key in TO_NETWORK:
            page.keyboard.press(key)
            page.wait_for_timeout(700)
        check("the network setting opens for editing",
              log.last_screen() == "SettingsEntryUpdateSelectionScreen",
              log.last_screen())

        # Mainnet to Testnet, which is a real change, so the view calls
        # set_value, which calls Settings.save(), which is where the missing
        # Timer used to raise.
        for key in ("ArrowDown", "Enter"):
            page.keyboard.press(key)
            page.wait_for_timeout(900)

        # The entry screen stays open with the new value marked, rather than
        # popping back to the list, so "still here and not an error screen" is
        # what accepting the change looks like.
        check("the change is accepted rather than raising",
              log.last_screen() == "SettingsEntryUpdateSelectionScreen",
              log.last_screen())
        check("no error screen", not log.seen("SystemError|Traceback", since=since),
              "the settings write raised")
        check("the debounced write ran inline",
              log.seen("timer inline", since=since) is not None,
              "Settings.save()'s Timer never fired, so nothing was written")

        harness.save_screen(page, harness.firmware_artifact("settings-changed.png"))
        browser.close()

    return report()


if __name__ == "__main__":
    sys.exit(main())
