"""
New seed from a photo, which is the camera's other half.

The hardware Camera has two modes and this port only ever replaced one of them.
The video stream is what a QR scan reads, and it was shimmed from the start; the
single-frame mode is what "new seed" uses to take one still, and it was not, so
it fell through to the real `from picamera import PiCamera` and the flow died on
`No module named 'picamera'` at camera.py line 63.

It went unseen because the two firmwares differ here: nothing in the fork calls
the single-frame API at all, and the fork was the firmware this page booted
until stock became the default. So the flow was broken on stock for as long as
it has been offered, and became the first thing a visitor could reach the moment
the default moved.

Stock only, for that reason. Driven to the picture and no further: what is being
asserted is that the camera opens, takes a frame and hands back an image, which
is the part that was raising.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report

from playwright.sync_api import sync_playwright

SHOT = harness.artifact("image-entropy.png")

# Home -> Tools -> New seed (the camera one, first in the list).
TO_THE_CAMERA = ["ArrowDown", "Enter", "Enter"]


def press(page, key, gap=1000):
    page.keyboard.press(key)
    page.wait_for_timeout(gap)


def main() -> int:
    if harness.FIRMWARE != "stock":
        print("  skipped: the fork does not use the single-frame camera at all")
        return 0

    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ])
        context = browser.new_context(
            permissions=["camera"], viewport={"width": 1100, "height": 900})
        page = context.new_page()
        log = Log(page)

        page.goto(harness.wallet_url())
        log.wait(r"display\(\) enter: MainMenuScreen", 240, "the wallet to boot")

        for key in TO_THE_CAMERA:
            press(page, key)
        log.wait(r"display\(\) enter: ToolsImageEntropyLivePreviewScreen", 90,
                 "the live preview")
        check("the camera opens for the preview", True)

        # Select takes the picture, which is the call that used to raise.
        page.wait_for_timeout(2000)
        press(page, "Enter", gap=3000)
        log.wait(r"display\(\) enter: ToolsImageEntropyFinalImageScreen", 90,
                 "the picture it took")
        check("it takes a still and shows it back", True)

        harness.save_screen(page, SHOT)
        print(f"  screen: {SHOT}")

        # Named, not any ModuleNotFoundError: stock's controller imports numpy at
        # boot purely to time how long it takes, which fails here and is logged
        # and swallowed, and has nothing to do with the camera or with anything
        # this build uses -- the only numpy code in the tree is commented out.
        check("picamera is never reached",
              log.seen(r"No module named 'picamera") is None)
        check("and nothing raised on the way", log.seen(r"View\.run RAISED") is None)
        check("no System Error",
              log.seen(r"display\(\) enter: UnhandledException") is None)

        browser.close()

    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
