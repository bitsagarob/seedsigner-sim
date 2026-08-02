"""
Drive the wallet through a scan with Chromium's fake camera.

getUserMedia needs a secure context, so this talks to loopback rather than the
machine's LAN address, and it feeds a file instead of a device so the run is
deterministic and needs no hardware.

The wallet already logs every screen it displays, so that log is the oracle:
reaching SeedFinalizeScreen means the QR was decoded, parsed as a SeedQR and
turned into a seed. The screenshot is taken there.

Screens rather than views, because most views override run() and so never reach
the traced View.run.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report

from playwright.sync_api import sync_playwright

# Which QR to hold up: qr.y4m is the digit-based SeedQR, qr-compact.y4m is the
# raw-bytes CompactSeedQR. Both encode the same seed, so both must land on the
# same fingerprint.
KIND = os.environ.get("QR_KIND", "qr")
Y4M = harness.artifact(f"{KIND}.y4m")
SHOT = harness.artifact(f"scan-proof-{KIND}.png")
PREVIEW_SHOT = harness.artifact(f"scan-preview-{KIND}.png")


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
            viewport={"width": 900, "height": 900},
        )
        page = context.new_page()
        log = Log(page)

        page.goto(harness.wallet_url())

        log.wait(r"display\(\) enter: MainMenuScreen", 240, "the wallet to boot")
        check("the wallet boots to the main menu", True)

        # Scan is the first button on the home screen and starts selected.
        page.keyboard.press("Enter")

        log.wait(r"display\(\) enter: ScanScreen", 90, "the scan screen")
        check("Enter on the home screen opens the scanner", True)

        # Caught during the video's blank lead-in, so this is the live preview
        # with nothing to decode yet.
        decoder = log.wait(r"\[cam\] .*decoding with (\S+)", 60, "the camera to open")
        check("the camera opens and picks a decoder", True, decoder.group(1))
        page.wait_for_timeout(900)
        page.screenshot(path=PREVIEW_SHOT)

        # Nothing has been held up yet, so anything loaded at this point was
        # invented rather than read.
        check("nothing is decoded from the blank lead-in",
              log.seen(r"display\(\) enter: SeedFinalizeScreen") is None)

        line = log.wait(r"display\(\) enter: SeedFinalizeScreen", 180, "the decoded seed")
        check("the QR decodes into a seed", True, line.group(0))

        page.wait_for_timeout(1500)
        page.screenshot(path=SHOT)
        print(f"  screenshots: {PREVIEW_SHOT}\n               {SHOT}")

        log.dump("[cam]")
        browser.close()

    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
