"""
A camera that goes quiet mid-scan, and whether the page admits it.

This is the one shape of camera trouble the device cannot report for itself.
ScanScreen's loop reads a frame and then, inside `if frame is not None`, polls
its buttons; there is no else. So a scan screen that stops receiving frames is
also a scan screen nobody can leave: every press is ignored, nothing is drawn,
and it looks exactly like the wallet has hung. Failing to *open* the camera is
already covered -- the shim raises CameraConnectionError and the device draws
its own error screen -- and this test asserts that difference rather than
assuming it, because the fix would be pointless if the device already spoke up.

Frames are stopped by stretching the one setTimeout the publish loop schedules
itself with. Stretched rather than dropped, because the loop reschedules from
inside its own callback and a dropped tick would end it permanently, which no
real fault does and which nothing could then recover from. The stall watch runs
on setInterval and is deliberately untouched by this, which is also why it is on
a separate timer: the loop stopping is one of the things it has to survive.

Both halves of the message are checked, not just the message. Saying "it will
not answer a press" is a claim about the firmware, so the test presses a button
and requires the device to ignore it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report

from playwright.sync_api import sync_playwright

SHOT = harness.firmware_artifact("camera-stall.png")

# 66 is the camera loop's own tick, and nothing else on the page schedules one.
FREEZE = """
window.__freeze = false;
const realTimeout = window.setTimeout;
window.setTimeout = function (fn, delay, ...rest) {
  if (window.__freeze && delay === 66) return realTimeout(fn, 20000, ...rest);
  return realTimeout(fn, delay, ...rest);
};
"""

HINT = "() => document.getElementById('camera-hint').textContent"


def press(page, key):
    page.keyboard.press(key)
    page.wait_for_timeout(700)


def wait_for_hint(page, want, seconds):
    """The hint, once it has settled. Polled because it is on a timer of its own."""
    for _ in range(seconds):
        page.wait_for_timeout(1000)
        if bool(page.evaluate(HINT)) == want:
            break
    return page.evaluate(HINT)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ])
        context = browser.new_context(
            permissions=["camera"], viewport={"width": 1100, "height": 900})
        page = context.new_page()
        page.add_init_script(FREEZE)
        log = Log(page)

        page.goto(harness.wallet_url())
        log.wait(r"display\(\) enter: MainMenuScreen", 240, "the wallet to boot")

        # Scan is the first button on the home screen and starts selected.
        press(page, "Enter")
        log.wait(r"display\(\) enter: ScanScreen", 90, "the scan screen")
        log.wait(r"\[cam\] .*decoding with (\S+)", 60, "the camera to open")
        page.wait_for_timeout(2500)
        check("nothing is said while the camera is delivering",
              page.evaluate(HINT) == "")

        page.evaluate("() => { window.__freeze = true; }")
        hint = wait_for_hint(page, True, 12)
        check("a camera that goes quiet is reported", bool(hint), repr(hint))

        # The claim that message makes about the device, tested rather than
        # trusted: left is what leaves a scan screen, and it must do nothing.
        press(page, "ArrowLeft")
        page.wait_for_timeout(4000)
        check("and the device really is deaf to its buttons while it is",
              log.last_screen() == "ScanScreen", f"on {log.last_screen()}")

        page.screenshot(path=SHOT)
        print(f"  screenshot: {SHOT}")

        page.evaluate("() => { window.__freeze = false; }")
        # Up to one stretched tick before the loop comes back round.
        check("it is taken back once frames return",
              wait_for_hint(page, False, 25) == "")

        press(page, "ArrowLeft")
        page.wait_for_timeout(4000)
        check("after which the button works again",
              log.last_screen() != "ScanScreen", f"on {log.last_screen()}")

        browser.close()

    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
