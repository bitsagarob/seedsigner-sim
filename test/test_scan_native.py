"""
Exercise the BarcodeDetector branch, which the jsQR run never reaches.

Chromium on desktop Linux ships no Shape Detection API, so test_scan.py always
falls through to jsQR and leaves the preferred path untested. Here a stand-in
BarcodeDetector is installed before any page script runs, so the page takes the
native branch. What this proves is the wiring, not Chrome's decoder.

The stub always answers "yes, a QR", and always with rubbish for rawValue. That
is not artificial: a real BarcodeDetector handed a CompactSeedQR returns mojibake,
because raw entropy bytes are not text and rawValue is all it can express.

Two phases, and the first is the one that matters:

  refuse  camera pointed at a blank wall. Native claims a QR on every frame. The
          wallet must load nothing at all. An earlier version fell back to
          rawValue here and reached a real-looking fingerprint, 17d9884b, from
          pure garbage -- a seed that was never in front of the camera. If this
          phase ever goes green by reporting a seed, the simulator is inventing
          keys, and that is the worst thing a bitcoin-adjacent tool can do.
  decode  the CompactSeedQR. Native claims a QR, rawValue is still rubbish, and
          the wallet must still arrive at the right seed, because jsQR re-read
          the frame for its actual bytes.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report

from playwright.sync_api import sync_playwright

SHOT = harness.firmware_artifact("scan-proof-native-compact.png")
# The device's screen on its own, which is what run.py's same_seed step compares
# against the two jsQR scans. The screenshot beside it is for looking at.
SCREEN = harness.firmware_artifact("scan-screen-native-compact.png")

# 24 bytes once UTF-8 encoded, which DecodeQR would accept as a CompactSeedQR
# length. Chosen to be exactly the shape of payload that used to slip through.
STUB = """
window.__detectCalls = 0;
window.BarcodeDetector = class {
  static getSupportedFormats() { return Promise.resolve(["qr_code", "code_128"]); }
  constructor(options) { this.formats = (options || {}).formats; }
  detect(source) {
    window.__detectCalls += 1;
    return Promise.resolve([{ rawValue: "\\uFFFD\\uFFFD\\uFFFDnot the seed\\uFFFD",
                             format: "qr_code" }]);
  }
};
"""

FINALIZE = r"display\(\) enter: SeedFinalizeScreen"

# Long enough for the fake camera to loop the blank video several times, so a
# wallet that was going to invent something has had every chance to.
REFUSE_WATCH_MS = 20000


def open_scanner(p, y4m):
    browser = p.chromium.launch(args=[
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-video-capture={y4m}",
    ])
    context = browser.new_context(permissions=["camera"],
                                  viewport={"width": 900, "height": 900})
    context.add_init_script(STUB)
    page = context.new_page()
    log = Log(page)

    page.goto(harness.wallet_url())
    log.wait(r"display\(\) enter: MainMenuScreen", 240, "boot")
    page.keyboard.press("Enter")
    line = log.wait(r"\[cam\] .*decoding with (\S+)", 90, "the camera")
    check("the stub is the decoder the page picked", "BarcodeDetector" in line.group(1),
          line.group(1))
    return browser, page, log


def phase_refuse(p) -> None:
    print("refuse: blank wall, native claiming a QR on every frame")
    y4m = harness.artifact("qr-blank.y4m")
    browser, page, log = open_scanner(p, y4m)

    page.wait_for_timeout(REFUSE_WATCH_MS)
    calls = page.evaluate("() => window.__detectCalls")

    check("the stub really was asked, repeatedly", calls > 0, f"{calls} detect() calls")
    check("no seed is loaded from a blank camera",
          log.seen(FINALIZE) is None,
          f"after {calls} fake detections")
    browser.close()


def phase_decode(p) -> None:
    print("decode: a real CompactSeedQR, native still returning rubbish")
    y4m = harness.artifact("qr-compact.y4m")
    browser, page, log = open_scanner(p, y4m)

    log.wait(FINALIZE, 180, "the decoded seed")
    calls = page.evaluate("() => window.__detectCalls")
    check("the real QR still decodes", True, f"after {calls} detect() calls")

    page.wait_for_timeout(1500)
    page.screenshot(path=SHOT)
    harness.save_screen(page, SCREEN)
    print(f"  screenshot: {SHOT}, must read b2269592")
    print(f"  screen:     {SCREEN}")
    browser.close()


def main() -> int:
    for name in ("qr-blank.y4m", "qr-compact.y4m"):
        if not os.path.exists(harness.artifact(name)):
            print(f"no {name}: run make_qr_y4m.py first", file=sys.stderr)
            return 2

    with sync_playwright() as p:
        phase_refuse(p)
        phase_decode(p)
    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
