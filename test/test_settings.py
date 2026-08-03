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

It also checks the network indicator under the device, and this is the file to
check it in: the indicator is only worth anything if it follows a change made
through the wallet's own menus, which is what the route below is.
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

# The selection screen opens on the current value, which is Testnet, and Mainnet
# is the entry above it. Deliberately that way round: mainnet is the answer the
# page has to shout about, and the only way to it is the device's own menu.
TO_MAINNET = ("ArrowUp", "Enter")


def indicator(page):
    """What the page says the network is, and whether it is saying it loudly.

    The loud half is a class on the body rather than on the indicator, because
    mainnet changes more than one thing: the warning gets heavier and the
    invitation to our own test network is taken away.
    """
    return (page.locator("#network").inner_text(),
            page.evaluate("document.body.classList.contains('mainnet')"))


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        log = harness.Log(page)
        page.goto(harness.wallet_url())
        log.wait("MainMenuScreen", 240, "the wallet to boot")

        # SeedSigner's own default is Mainnet. This one comes up on Testnet
        # because settings.json says so before the wallet reads it, which is
        # configuration and not a patch, and it is the whole reason a visitor
        # can be pointed at a test network without being told to go and change
        # something first. Read off the page, which was told by the wallet.
        check("a fresh page comes up on Testnet",
              indicator(page) == ("Bitcoin network: Testnet", False),
              str(indicator(page)))
        check("and offers our test network while it is on one",
              page.locator(".note").is_visible())
        page.screenshot(path=harness.firmware_artifact("network-testnet.png"), full_page=True)

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

        # Testnet to Mainnet, which is a real change, so the view calls
        # set_value, which calls Settings.save(), which is where the missing
        # Timer used to raise.
        changed = log.mark()
        for key in TO_MAINNET:
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
        # And the wallet came back from it, which it did not until the locks
        # were made reentrant. Running the debounced write inline runs it inside
        # the lock save() holds while scheduling it, and one thread taking a
        # plain Lock twice waits for itself forever: the value was stored, the
        # screen stayed up looking correct, and nothing ever drew again.
        check("and the wallet came back from it rather than wedging",
              log.seen(r"display\(\) enter", since=changed) is not None,
              "nothing drew after the settings write")

        # The indicator is fed by the worker reading Settings back after the
        # wallet writes them, so this is the wallet's new value arriving rather
        # than the page guessing what the keypresses above meant.
        check("the network indicator follows the wallet, loudly",
              indicator(page) == ("Bitcoin network: Mainnet", True),
              str(indicator(page)))
        # A visitor who has gone to mainnet on purpose is not being taught
        # anything, and should not be handed a test network to play on.
        check("and the page stops offering our test network",
              not page.locator(".note").is_visible())
        page.screenshot(path=harness.firmware_artifact("network-mainnet.png"), full_page=True)

        harness.save_screen(page, harness.firmware_artifact("settings-changed.png"))
        browser.close()

    return report()


if __name__ == "__main__":
    sys.exit(main())
