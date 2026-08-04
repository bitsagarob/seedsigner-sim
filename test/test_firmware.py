"""
The firmware switch: that it switches, and that the page says what it is running.

Two firmwares are built and the page runs one of them, which changes what the
page can honestly show. Stock SeedSigner has no smartcard code at all, so a card
tray under it would be a control for a thing that does not exist, and the
sentence in the technical details panel is where a visitor who followed a shared
link finds out which firmware they are looking at, in full: the switch itself
carries only the name each build calls itself.

Nothing here waits for the wallet to boot: every claim it makes is decided by
the page before Python starts, so this runs in seconds like the tray layout test
rather than in minutes like the ones that drive the wallet.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import check, report

from playwright.sync_api import sync_playwright

# Both firmwares are visited by name rather than by whatever SIM_FIRMWARE says,
# because this file is about the switch between them and not about either one.
FORK = "the 3rdIteration smartcard fork"
STOCK = "stock SeedSigner 0.8.7"


def switch_state(page):
    """Which firmware each button on the switch offers, and which is pressed."""
    return [(b.inner_text(), b.get_attribute("aria-pressed"))
            for b in page.locator("#firmware-switch button").all()]


def firmware_line(page):
    """The sentence saying what is running, which lives inside the panel now.

    Shut again afterwards. The panel hangs over the page rather than pushing it
    down, and under stock the page is short enough that an open one covers the
    firmware switch in the footer and swallows the click meant for it.
    """
    panel = page.locator("#about")
    panel.evaluate("node => { node.open = true; }")
    said = page.locator("#firmware-line").inner_text()
    panel.evaluate("node => { node.open = false; }")
    return said


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # The same narrow viewport the tray layout test uses: the switch sits
        # under a device wider than a phone and must not push the page sideways.
        page = browser.new_context(viewport={"width": 720, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and harness.page_error(m) else None)

        page.goto(harness.wallet_url(firmware="smartcard"))
        page.wait_for_selector("#firmware-switch button")

        check("the panel says it is running the fork, and calls it a fork",
              FORK in firmware_line(page), firmware_line(page))
        # The sentence belongs to the panel, not to the open page: it is the
        # same material as the rows under it.
        check("and the open page does not carry that sentence as well",
              page.locator("#build #firmware-line").count() == 1,
              str(page.locator("#firmware-line").count()))
        check("the switch offers both firmwares by name, with the fork pressed",
              switch_state(page) == [("ShieldSigner", "true"),
                                     ("SeedSigner", "false")],
              str(switch_state(page)))
        page.wait_for_selector(".cardtray-card")
        check("the card tray is there under the fork",
              page.locator(".cardtray-card").count() == 3,
              str(page.locator(".cardtray-card").count()))
        check("the page does not scroll sideways",
              page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
        page.screenshot(path=harness.artifact("firmware-smartcard.png"), full_page=True)

        page.locator("#firmware-switch button[data-firmware=stock]").click()
        page.wait_for_url(re.compile(r"firmware=stock"))
        page.wait_for_selector("#firmware-switch button")

        check("switching reloads on the other firmware",
              "firmware=stock" in page.url, page.url)
        # Tracing is carried across rather than dropped: the switch edits the
        # query string it was given instead of replacing it, and every test in
        # this suite would go blind if it did not.
        check("and keeps the rest of the query string", "debug=1" in page.url, page.url)
        check("the panel says it is running stock, and calls it stock",
              STOCK in firmware_line(page), firmware_line(page))
        check("the switch follows",
              switch_state(page) == [("ShieldSigner", "false"),
                                     ("SeedSigner", "true")],
              str(switch_state(page)))
        # Absent, not disabled and not greyed. Stock has no card support at all,
        # so there is nothing for a tray to control and nothing to explain.
        check("the card tray is gone under stock",
              page.locator(".cardtray-card").count() == 0,
              str(page.locator(".cardtray-card").count()))
        check("and leaves no empty box behind",
              not page.locator("#cardtray").is_visible())
        page.screenshot(path=harness.artifact("firmware-stock.png"), full_page=True)

        page.locator("#firmware-switch button[data-firmware=smartcard]").click()
        page.wait_for_url(re.compile(r"firmware=smartcard"))
        page.wait_for_selector(".cardtray-card")
        check("switching back comes back on the fork, tray and all",
              FORK in firmware_line(page)
              and page.locator(".cardtray-card").count() == 3,
              firmware_line(page))

        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    return report()


if __name__ == "__main__":
    sys.exit(main())
