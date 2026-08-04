"""
Generate a diceware password on the fork, all the way to the words.

The fork's password generator is upstream's, and at the pinned tag it is broken:
password_generator_views.py calls _format_word_password() without importing it,
so every word-based password ends in a System Error naming line 893 instead of a
password. The name is defined in tools_views.py and never brought across.
Upstream's own master fixes it with exactly that import; there is no released tag
carrying the fix, and UPSTREAM pins tags rather than branch tips for reasons that
file explains. So wallet-worker.js puts the name back from outside, the way every
other seam here is replaced, and this walks the whole flow to prove it took.

Fork only. Stock has no password generator at all.

The dice are rolled with varied faces on purpose. The device checks its rolls
for randomness and rejects a run of identical ones with "Poor Entropy", which is
the device working correctly and would look exactly like this test failing.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report

from playwright.sync_api import sync_playwright

SHOT = harness.artifact("password-generated.png")

# Home to the dice pad: Tools, Password Generator, Diceware-EFF Short, 128 bits,
# Dice. The roll count follows from the type and the strength, so the device
# works it out rather than asking.
TO_THE_DICE = ["ArrowDown", "Enter",              # home -> Tools
               "ArrowDown", "ArrowDown", "Enter",  # Tools -> Password Generator
               "ArrowDown", "Enter",               # type -> Diceware-EFF Short
               "Enter",                            # strength -> 128 bits
               "ArrowDown", "Enter"]               # entropy -> Dice

ROLLS = 52   # what 128 bits of EFF-short costs, and what the device asks for


def press(page, key, gap=650):
    page.keyboard.press(key)
    page.wait_for_timeout(gap)


def roll(page, cursor, face):
    """One roll on the 1 2 3 over 4 5 6 pad, which keeps the cursor where it was.

    Stepped to rather than named: the pad has no way to jump to a face, so the
    only way to enter one is to walk the cursor there and select it.
    """
    target = face - 1
    while cursor // 3 < target // 3:
        press(page, "ArrowDown"); cursor += 3
    while cursor // 3 > target // 3:
        press(page, "ArrowUp"); cursor -= 3
    while cursor % 3 < target % 3:
        press(page, "ArrowRight"); cursor += 1
    while cursor % 3 > target % 3:
        press(page, "ArrowLeft"); cursor -= 1
    press(page, "Enter")
    return cursor


def main() -> int:
    if harness.FIRMWARE != "smartcard":
        print("  skipped: stock has no password generator")
        return 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1100, "height": 900})
        page = context.new_page()
        log = Log(page)

        page.goto(harness.wallet_url())
        log.wait(r"display\(\) enter: MainMenuScreen", 240, "the wallet to boot")

        check("the missing name is put back at boot",
              log.seen(r"patched in password_generator_views\._format_word_password")
              is not None)

        for key in TO_THE_DICE:
            press(page, key)
        check("the password generator opens", log.last_screen() == "ToolsDiceEntropyEntryScreen",
              log.last_screen())

        # Fixed seed: the point is varied faces, not unpredictable ones, and a
        # run that fails should fail the same way twice.
        rng = random.Random(11)
        cursor = 0
        for i in range(ROLLS):
            cursor = roll(page, cursor, rng.choice([1, 2, 3, 4, 5, 6]))
            if log.last_screen() != "ToolsDiceEntropyEntryScreen":
                break
        check("all the rolls are taken", i + 1 == ROLLS, f"{i + 1} of {ROLLS}")

        # The separator menu, and then the line that used to raise.
        press(page, "Enter", gap=900)
        log.wait(r"View\.run enter: ToolsPasswordGenerateView", 60, "the generate step")

        log.wait(r"display\(\) enter: ToolsTextQRReviewTextScreen", 60, "the password")
        check("a password is produced rather than a System Error", True,
              log.last_screen())
        check("nothing raised on the way", log.seen(r"View\.run RAISED") is None)
        check("and the device is not showing an error",
              log.seen(r"display\(\) enter: (ErrorScreen|UnhandledException)") is None)

        harness.save_screen(page, SHOT)
        print(f"  screen: {SHOT}")
        browser.close()

    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
