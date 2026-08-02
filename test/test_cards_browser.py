"""
Drive the wallet's Smartcard Tools with the card tray, in a real browser.

The proof is the log. The wallet already announces every screen it displays, and
the simulated card layer announces every card that arrives in the reader with the
UID pysatochip will derive for it, so reaching Card Info with a given UID is a
statement about what the Python side actually saw -- not about what the tray was
clicked into.

Four things are being shown:
  a) an empty reader ends in "No smartcard detected", not a hang
  b) Card A gives the Python side a card with Card A's UID
  c) Card B gives it a different one, so the cards are genuinely distinct
  d) Card A, put back, is still the same card

This drives wallet.html itself rather than a cut-down harness page, so the tray
wiring under test is the wiring users get.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report

from playwright.sync_api import sync_playwright


# What the reader says with nothing in it.
EMPTY = "Card reader empty"


def press(page, key, times=1):
    for _ in range(times):
        page.keyboard.press(key)
        page.wait_for_timeout(220)


def open_card_info(page, log):
    """Home -> Tools -> Smartcard Tools -> Common Functions -> Card Info.

    The caller is responsible for being at home when this is called.
    """
    press(page, "ArrowDown")            # Scan -> Tools, a 2x2 grid
    press(page, "Enter")
    log.wait(r"display\(\) enter: ButtonListScreen", 30, "the Tools menu",
             max(0, log.mark() - 20))
    press(page, "ArrowDown", 3)         # new seed, new seed, passwords, [smartcard]
    press(page, "Enter")
    page.wait_for_timeout(600)
    press(page, "Enter")                # Common Functions, first in the list
    page.wait_for_timeout(600)
    press(page, "ArrowDown")            # Device Filter -> [Card Info]
    press(page, "Enter")


def back_to_home(page, log):
    """Climb the back stack until the home screen is up again.

    Up walks off the top of a list or a keyboard onto the top nav's back arrow,
    and a click there returns RET_CODE__BACK_BUTTON. It is the one gesture that
    works on every screen this test can land on, including the PIN keyboard the
    wallet offers for a blank card. Home is the one screen with no back arrow, so
    this has to notice it has arrived rather than pressing once more and diving
    back in.
    """
    for _ in range(12):
        if log.last_screen() == "MainMenuScreen":
            return
        press(page, "ArrowUp", 6)
        press(page, "Enter")
        page.wait_for_timeout(500)
    raise AssertionError("could not get back to the home screen\n  "
                         + "\n  ".join(log.lines[-30:]))


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 900, "height": 1100})
        page = context.new_page()
        log = Log(page)
        page.goto(harness.wallet_url())

        log.wait(r"display\(\) enter: MainMenuScreen", 300, "the wallet to boot")
        print("booted to the main menu")

        # The tray is mounted and the worker has been told about the buffer.
        log.wait(r"\[card\] tray attached", 30, "the card tray to reach Python")
        check("the tray reports three cards",
              page.locator(".cardtray-card").count() == 3)
        check("the reader starts empty",
              page.locator(".cardtray-slotlabel").inner_text() == EMPTY,
              page.locator(".cardtray-slotlabel").inner_text())
        check("every card is published as blank",
              page.locator(".cardtray-pill", has_text="blank").count() == 3,
              page.locator(".cardtray-row").inner_text().replace("\n", " | "))

        # (a) empty reader
        print("\n(a) no card in the reader")
        window = log.mark()
        open_card_info(page, log)
        log.wait(r"\[card\] asked for a card, reader is empty", 90,
                 "Python to notice the empty reader", window)
        check("Python reports an empty reader", True)
        check("and hands over no card at all",
              log.seen(r"\[card\] Card . inserted", window) is None)
        log.wait(r"display\(\) enter: WarningScreen", 90,
                 "the no-card warning", window)
        check("the wallet warns instead of hanging", True)
        page.screenshot(path=harness.artifact("cards-no-card.png"))
        back_to_home(page, log)
        check("the wallet is still driveable afterwards", True)

        # (b) Card A
        print("\n(b) Card A in the reader")
        page.locator(".cardtray-card").nth(0).click()
        check("the tray shows Card A inserted",
              "Card A inserted" in page.locator(".cardtray-slotlabel").inner_text(),
              page.locator(".cardtray-slotlabel").inner_text())
        window = log.mark()
        open_card_info(page, log)
        uid_a = log.wait(r"\[card\] Card A inserted, uid=([0-9a-f]{40})", 120,
                         "Python to see Card A", window).group(1)
        check("Python sees Card A", True, uid_a[:16] + "...")
        # pysatochip derives its own UID from the CPLC/IIN/CIN the card answered
        # with. That it lands on the same digest is the whole point.
        log.wait(r"Found Card: " + uid_a, 60, "pysatochip to agree", window)
        check("pysatochip derives the same UID", True)
        check("the reader was never reported empty",
              log.seen(r"\[card\] asked for a card, reader is empty", window) is None)
        check("no other card was reported",
              log.seen(r"\[card\] Card [BC] inserted", window) is None)
        # A blank Satochip is what the wallet finds, so this is where it stops.
        log.wait(r"display\(\) enter: WarningScreen", 60, "the wallet to react", window)
        page.screenshot(path=harness.artifact("cards-card-a.png"))
        back_to_home(page, log)

        # (c) Card B
        print("\n(c) Card A out, Card B in")
        page.locator(".cardtray-eject").click()
        check("ejecting empties the reader",
              page.locator(".cardtray-slotlabel").inner_text() == EMPTY,
              page.locator(".cardtray-slotlabel").inner_text())
        page.locator(".cardtray-card").nth(1).click()
        check("the tray shows Card B inserted",
              "Card B inserted" in page.locator(".cardtray-slotlabel").inner_text())
        window = log.mark()
        open_card_info(page, log)
        uid_b = log.wait(r"\[card\] Card B inserted, uid=([0-9a-f]{40})", 120,
                         "Python to see Card B", window).group(1)
        check("Card B's UID differs from Card A's", uid_a != uid_b,
              f"{uid_a[:16]}... vs {uid_b[:16]}...")
        log.wait(r"Found Card: " + uid_b, 60, "pysatochip to agree", window)
        check("pysatochip agrees it is a different card", True)
        page.screenshot(path=harness.artifact("cards-card-b.png"))
        back_to_home(page, log)

        # (d) Card A again
        print("\n(d) Card B out, Card A back in")
        page.locator(".cardtray-card").nth(0).click()
        check("clicking Card A swaps it for Card B",
              "Card A inserted" in page.locator(".cardtray-slotlabel").inner_text())
        window = log.mark()
        open_card_info(page, log)
        uid_again = log.wait(r"\[card\] Card A inserted, uid=([0-9a-f]{40})", 120,
                             "Python to see Card A again", window).group(1)
        check("Card A is the same card it was", uid_again == uid_a,
              uid_again[:16] + "...")
        log.wait(r"Found Card: " + uid_a, 60, "pysatochip to agree", window)
        check("and the wallet reads it as the same card", True)

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
