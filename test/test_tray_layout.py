"""
Look at the tray itself: layout, colours, and that the reader visibly changes.

No Python runs here beyond the page's own boot, so this is the cheap browser
test: it says whether the control a user touches is the shape it should be, and
it says it in seconds rather than minutes.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import check, report

from playwright.sync_api import sync_playwright

# The tray accent, which is Bitcoin orange. Asserted as a colour rather than a
# class name because a class can be present and styled into invisibility.
ACCENT = "rgb(247, 147, 26)"

# What the reader says with nothing in it, quoted rather than matched loosely:
# this is a label a user reads, so a change to it is a change worth noticing.
EMPTY = "Card reader empty"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Deliberately narrow: the tray sits under a device that is wider than a
        # phone, on a page that must not grow a horizontal scrollbar because of
        # either of them.
        page = browser.new_context(viewport={"width": 720, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(harness.wallet_url())
        page.wait_for_selector(".cardtray-card")

        boxes = [page.locator(".cardtray-card").nth(i).bounding_box() for i in range(3)]
        check("three cards side by side",
              boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"] and
              abs(boxes[0]["y"] - boxes[2]["y"]) < 1,
              " ".join(f"{int(b['x'])},{int(b['y'])}" for b in boxes))
        check("each card is a readable size",
              all(b["width"] > 90 and b["height"] > 60 for b in boxes),
              f"{int(boxes[0]['width'])}x{int(boxes[0]['height'])}")
        check("labelled A, B, C",
              [page.locator(".cardtray-name").nth(i).inner_text() for i in range(3)]
              == ["Card A", "Card B", "Card C"])
        check("the page does not scroll sideways",
              page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))

        def border(i):
            return page.locator(".cardtray-card").nth(i).evaluate(
                "el => getComputedStyle(el).borderTopColor")

        def lifted(i):
            return page.locator(".cardtray-card").nth(i).evaluate(
                "el => getComputedStyle(el).transform")

        check("a card out of the reader is not accented", border(0) != ACCENT, border(0))

        page.locator(".cardtray-card").nth(0).click()
        page.wait_for_timeout(300)
        check("inserting accents the card in Bitcoin orange", border(0) == ACCENT, border(0))
        check("and lifts it into the slot", lifted(0) != "none", lifted(0))
        check("the reader says which card it is holding",
              page.locator(".cardtray-slotlabel").inner_text() == "Card A inserted")
        check("aria-pressed follows the reader",
              [page.locator(".cardtray-card").nth(i).get_attribute("aria-pressed")
               for i in range(3)] == ["true", "false", "false"])
        check("the eject control is live", not page.locator(".cardtray-eject").is_disabled())
        check("focus went back to the page so the wallet keeps the keyboard",
              page.evaluate("document.activeElement === document.body"),
              page.evaluate("document.activeElement.className"))
        tray = page.locator(".cardtray-row").bounding_box()
        page.screenshot(path=harness.artifact("cards-tray-a.png"),
                        clip={"x": 0, "y": max(0, tray["y"] - 20),
                              "width": 720, "height": tray["height"] + 80})

        # It is one reader: putting the next card in takes the last one out.
        page.locator(".cardtray-card").nth(2).click()
        page.wait_for_timeout(300)
        check("only one card can be in at a time",
              [page.locator(".cardtray-card").nth(i).get_attribute("aria-pressed")
               for i in range(3)] == ["false", "false", "true"])
        check("the reader follows",
              page.locator(".cardtray-slotlabel").inner_text() == "Card C inserted")

        # Clicking the card that is in ejects it.
        page.locator(".cardtray-card").nth(2).click()
        page.wait_for_timeout(300)
        check("clicking the inserted card ejects it",
              page.locator(".cardtray-slotlabel").inner_text() == EMPTY,
              page.locator(".cardtray-slotlabel").inner_text())
        check("the eject control goes dead with nothing to eject",
              page.locator(".cardtray-eject").is_disabled())

        # Tab reaches the tray, and Enter there does not leak to the wallet.
        page.evaluate("""() => {
          window.__walletKeys = 0;
          document.addEventListener("keydown", e => { if (e.key === "Enter") window.__walletKeys++; });
        }""")
        page.locator(".cardtray-card").nth(1).focus()
        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        check("Enter on a tabbed-to card inserts it",
              page.locator(".cardtray-slotlabel").inner_text() == "Card B inserted")
        check("and the wallet never saw that Enter",
              page.evaluate("window.__walletKeys") == 0,
              str(page.evaluate("window.__walletKeys")))

        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    return report()


if __name__ == "__main__":
    sys.exit(main())
