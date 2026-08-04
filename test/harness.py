"""
Shared plumbing for the tests: where to point them, and how to read the wallet's
own log.

The log is the oracle for almost everything here. The wallet narrates every
screen it puts up ("display() enter: <ScreenName>"), the camera says which
decoder it chose, and the simulated card layer says which card the Python side
saw. Asserting on those lines is a statement about what the wallet actually did,
which a screenshot is not.

The page reads that narration itself now and so always asks the worker for it,
but only ?debug=1 puts it on the console, which is where these tests read it
from, so wallet_url() always adds it. Without it every test in this suite would
sit and time out against a wallet that is working perfectly.
"""

import base64
import os
import re
import time
from urllib.parse import urlencode

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PORT = int(os.environ.get("SIM_PORT", "8770"))

# Loopback rather than the machine's LAN address, deliberately: getUserMedia
# only runs in a secure context, and 127.0.0.1 counts as one without anybody
# having to produce a certificate.
BASE_URL = os.environ.get("SIM_URL", f"http://127.0.0.1:{PORT}").rstrip("/")

# Videos and screenshots. Everything written here is generated; nothing in it is
# an input to anything else, so it can be deleted at any time.
ARTIFACT_DIR = os.environ.get("SIM_ARTIFACT_DIR", os.path.join(REPO, "test", "artifacts"))

# Which firmware these tests drive. Two wallet zips are built and the page picks
# one by name with ?firmware=; "smartcard" is the 3rdIteration fork the
# simulator has always run, "stock" is SeedSigner as its own project publishes
# it. Set SIM_FIRMWARE to switch a single test file over; run.py sets it for the
# stock half of the suite.
FIRMWARE = os.environ.get("SIM_FIRMWARE", "smartcard")

# The build outputs, none of which is committed. build/build-wallet-zip.sh
# assembles a wallet zip per firmware from its pinned upstream SeedSigner commit
# and leaves it in build/out; build/fetch-assets.sh downloads the Pyodide
# runtime into src/web/pyodide. Both are looked for in a list rather than at one
# path, so a deploy that puts everything in one directory still works.
# SIM_ASSETS replaces the list, which is how the suite is pointed at an
# already-built tree.
ASSET_DIRS = [d for d in os.environ.get("SIM_ASSETS", "").split(os.pathsep) if d] or [
    os.path.join(REPO, "build", "out"),
    os.path.join(REPO, "src", "web"),
]

# What the server overlays, in priority order: the page and its scripts, then
# the shims the worker fetches by name at boot, then the build outputs.
WEB_ROOTS = [os.path.join(REPO, "src", "web"), os.path.join(REPO, "src", "shims")]
WEB_ROOTS += [d for d in ASSET_DIRS if d not in WEB_ROOTS]


def find_asset(name):
    """First ASSET_DIRS entry holding <name>, or None if the build has not run."""
    for directory in ASSET_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate
    return None



# The analytics endpoints are served by the site, not by this repository: a
# clone serves no /mt.js and no /mt.php, which is a state wallet-track.js is
# written to survive silently. The browser still logs the failed fetch as a
# console error, so the two are dropped here rather than left to fail every
# suite that asks whether the page had a clean run. Anything else is kept.
ANALYTICS = ("/mt.js", "/mt.php")


def page_error(message):
    """Is this console message a real problem, rather than the missing tracker?

    The URL is in the message's location rather than its text: a failed fetch
    logs "Failed to load resource: the server responded with a status of 404"
    and nothing else, so matching on the text alone drops nothing at all.
    """
    url = (message.location or {}).get("url", "")
    return not any(path in url for path in ANALYTICS)

def wallet_url(page="wallet.html", **params):
    """A URL for the simulator, with tracing on and the firmware named."""
    params.setdefault("debug", "1")
    params.setdefault("firmware", FIRMWARE)
    return f"{BASE_URL}/{page}?{urlencode(params)}"


def artifact(name):
    """Absolute path to a generated file, with the directory made on demand."""
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    return os.path.join(ARTIFACT_DIR, name)


def firmware_artifact(name):
    """artifact(), with the firmware in the name so two runs of the same test
    against two firmwares do not overwrite each other's evidence.

    The smartcard run keeps the bare names it has always written, because the
    baseline image and everything referring to these files by name predates the
    second firmware and there is no reason to churn it.
    """
    return artifact(name if FIRMWARE == "smartcard" else f"{FIRMWARE}-{name}")


def save_screen(page, path):
    """Write the device's screen, at the 320x240 the wallet drew it.

    Not a screenshot. A screenshot of the page also holds the title, the warning
    box, the card tray and the hint line, all of them rendered with whatever
    fonts the machine has and none of them anything the wallet can influence; a
    screenshot of the canvas element holds whatever CSS scaled it to, and those
    scaled edge pixels move by one when anything else on the page changes
    height. Reading the canvas's own pixels instead gets exactly the bytes
    SeedSigner's renderer put there and nothing else, which is the same reason
    test_cards_seedkeeper.py digests the canvas rather than an image of it.
    """
    data_url = page.evaluate(
        "() => document.getElementById('screen').toDataURL('image/png')")
    with open(path, "wb") as handle:
        handle.write(base64.b64decode(data_url.split(",", 1)[1]))


# --- checks ------------------------------------------------------------------
# Deliberately not an assert: a run that stops at the first failure tells you one
# thing per run, and these runs are slow.

_failures = []


def check(name, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + name + (f"  {detail}" if detail else ""),
          flush=True)
    if not condition:
        _failures.append(name)


def report():
    """Exit code for the whole file: 0 only if every check passed."""
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print("all checks passed")
    return 0


# --- reading the wallet's log ------------------------------------------------


class Log:
    """Everything the page has said, in order, with the waiting built in."""

    def __init__(self, page):
        self.lines = []
        self.page = page
        page.on("console", lambda m: self.lines.append(m.text))
        # A page error is invisible otherwise, and a test that fails because the
        # worker threw should say so rather than just time out.
        page.on("pageerror", lambda e: self.lines.append(f"PAGEERROR {e}"))

    def mark(self):
        """Index to search from, so a later phase cannot pass on a line an
        earlier phase produced."""
        return len(self.lines)

    def wait(self, pattern, timeout, what, since=0):
        deadline = time.time() + timeout
        matcher = re.compile(pattern)
        while time.time() < deadline:
            for line in self.lines[since:]:
                found = matcher.search(line)
                if found:
                    return found
            self.page.wait_for_timeout(250)
        raise AssertionError(f"timed out after {timeout}s waiting for {what}\n  "
                             + "\n  ".join(self.lines[-40:]))

    def seen(self, pattern, since=0):
        """For asserting a line is absent, which is how the refusal tests work."""
        matcher = re.compile(pattern)
        for line in self.lines[since:]:
            found = matcher.search(line)
            if found:
                return found
        return None

    def last_screen(self):
        """The screen most recently put up, which is the one being looked at."""
        for line in reversed(self.lines):
            found = re.search(r"display\(\) enter: (\w+)", line)
            if found:
                return found.group(1)
        return None

    def dump(self, needle):
        for line in self.lines:
            if needle in line:
                print("  " + line)
