"""
The technical details panel, and the one check the page makes about itself.

The panel exists so that a visitor can see what is running without being asked
to take anybody's word for it: the firmware, the pin it was built from, the
published hashes, and the sha256 of the wallet zip the worker actually received.
That last one is the only line with any weight in it, so most of this file is
about it.

Every value the panel shows is checked against something that is not the panel's
own source. The tag, the commit and both hashes are read out of UPSTREAM here,
not out of build-info.json, because build-info.json is what feeds the panel and
comparing the two would only prove the page can echo a file back. The dependency
list is compared against the licences manifest inside the built zip. And the
received hash is compared against sha256 of the zip on disk.

Then the part that makes the check a check: a deliberately altered zip is served
from a second server, and the panel has to say so. A self-check that cannot go
red is decoration.

Nothing here needs the wallet to finish booting, but the hash does not arrive
until the worker has loaded Pyodide and fetched the zip, so this is minutes
rather than seconds.
"""

import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import check, report

from playwright.sync_api import sync_playwright

# Both are visited by name rather than by SIM_FIRMWARE: the panel describes
# whichever firmware is running, so it is only proved by running both.
FIRMWARES = ("smartcard", "stock")

# The zip has to arrive and be hashed, which happens after Pyodide and its
# binary packages have loaded. That is the whole cost of this file.
HASH_TIMEOUT = 180_000


def upstream_field(firmware, key):
    """One published value, straight out of UPSTREAM.

    Section-aware for the same reason every other reader of that file is: it
    describes two firmwares, and a parser that ignored the headers would hand
    back the other one's hash.
    """
    section = None
    with open(os.path.join(harness.REPO, "UPSTREAM"), encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("["):
                section = line.strip().strip("[]")
            elif section == firmware and "=" in line and not line.startswith("#"):
                name, value = line.split("=", 1)
                if name.strip() == key:
                    return value.strip()
    raise AssertionError(f"no {key!r} in the [{firmware}] section of UPSTREAM")


def pinned_pyodide():
    """The version build/fetch-assets.sh pins, which is where it is written."""
    with open(os.path.join(harness.REPO, "build", "fetch-assets.sh"), encoding="utf-8") as handle:
        found = re.search(r'^PYODIDE_VERSION="([^"]+)"', handle.read(), re.M)
    assert found, "no PYODIDE_VERSION in build/fetch-assets.sh"
    return found.group(1)


def zip_dependencies(zip_path):
    """(name, version) for every third-party dependency the zip carries.

    Read out of licenses/MANIFEST.txt, which the build writes into the zip as it
    packs each one, so it is the zip's own account of what is in it rather than
    a second description of the same table.
    """
    with zipfile.ZipFile(zip_path) as archive:
        manifest = archive.read("licenses/MANIFEST.txt").decode()

    pairs = set()
    for line in manifest.splitlines():
        # Three columns, padded, so two or more spaces is the separator.
        columns = re.split(r"\s{2,}", line.strip())
        if len(columns) != 3:
            continue  # the prose at the top of the file
        _module, distribution, release = columns
        if distribution in ("DISTRIBUTION", "this repository"):
            continue  # the header, and this repository's own stand-ins
        if release.startswith("commit "):
            continue  # upstream itself, which the panel names separately
        pairs.add((distribution, release))
    return pairs


def panel_dependencies(page):
    """(name, version) for every entry the panel lists."""
    pairs = set()
    for item in page.locator("#build-deps li").all():
        name, _, version = item.inner_text().partition(" ")
        pairs.add((name.strip(), version.strip()))
    return pairs


def open_panel(page, url):
    page.goto(url)
    page.wait_for_selector("#build > summary")
    page.locator("#build > summary").click()
    # Filled in from build-info.json, so waiting for the first dependency is
    # waiting for that fetch rather than for a fixed number of milliseconds.
    page.wait_for_selector("#build-deps li")


def text(page, selector):
    return page.locator(selector).inner_text().strip()


def describes(page, firmware):
    """The panel's account of a build, checked against UPSTREAM and the zip."""
    published = upstream_field(firmware, "wallet_zip_sha256")

    check(f"[{firmware}] the panel says which firmware is running",
          firmware in text(page, "#build-firmware"), text(page, "#build-firmware"))
    check(f"[{firmware}] and the tag UPSTREAM pins",
          text(page, "#build-tag") == upstream_field(firmware, "tag"),
          text(page, "#build-tag"))
    check(f"[{firmware}] and the commit UPSTREAM pins",
          text(page, "#build-commit") == upstream_field(firmware, "commit"),
          text(page, "#build-commit"))
    commit_url = (re.sub(r"\.git$", "", upstream_field(firmware, "repo"))
                  + "/commit/" + upstream_field(firmware, "commit"))
    check(f"[{firmware}] and links the commit at the upstream repo",
          page.locator("#build-commit").get_attribute("href") == commit_url,
          page.locator("#build-commit").get_attribute("href"))
    check(f"[{firmware}] and the published zip sha256",
          text(page, "#build-published") == published, text(page, "#build-published"))
    check(f"[{firmware}] and the published contents sha256",
          text(page, "#build-contents")
          == upstream_field(firmware, "wallet_zip_contents_sha256"),
          text(page, "#build-contents"))
    check(f"[{firmware}] and the Pyodide the repo pins",
          text(page, "#build-pyodide") == pinned_pyodide(), text(page, "#build-pyodide"))

    zip_path = harness.find_asset(f"wallet-{firmware}.zip")
    if zip_path:
        check(f"[{firmware}] and every dependency the zip's own manifest lists",
              panel_dependencies(page) == zip_dependencies(zip_path),
              str(panel_dependencies(page) ^ zip_dependencies(zip_path)))

    # The limitation is the point of the panel as much as the hashes are, so it
    # is asserted rather than left to survive on good intentions.
    check(f"[{firmware}] and does not claim the self-check is proof",
          "not proof" in text(page, "#build .limit"), text(page, "#build .limit"))


def verdict(page, timeout=HASH_TIMEOUT):
    page.wait_for_selector("#build-verdict:not([data-state=pending])", timeout=timeout)
    return (page.locator("#build-verdict").get_attribute("data-state"),
            text(page, "#build-computed"))


def sha256_of(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def serve(root, port):
    """A second server, with ROOT overlaid in front of the usual ones."""
    server = subprocess.Popen(
        [sys.executable, os.path.join(harness.REPO, "test", "serve.py"),
         "--port", str(port), root] + [r for r in harness.WEB_ROOTS if os.path.isdir(r)])
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.5):
                return server
        except OSError:
            time.sleep(0.25)
    server.kill()
    raise AssertionError(f"the second server never came up on port {port}")


def altered(page):
    """Serve a zip that is not the published one, and require the panel to say so.

    The altered copy goes in a temporary directory served in front of the real
    one, so build/out is never touched: a test that corrupts a build output has
    to put it back, and one that fails halfway through does not.
    """
    firmware = "smartcard"
    original = harness.find_asset(f"wallet-{firmware}.zip")
    if not original:
        check("a built wallet zip to alter", False, "no wallet-smartcard.zip")
        return

    root = tempfile.mkdtemp(prefix="sim-altered-")
    fake = os.path.join(root, f"wallet-{firmware}.zip")
    shutil.copyfile(original, fake)
    with open(fake, "ab") as handle:
        handle.write(b"\n")  # one byte, which is all it should take

    server = serve(root, harness.PORT + 1)
    try:
        open_panel(page, f"http://127.0.0.1:{harness.PORT + 1}"
                         f"/wallet.html?debug=1&firmware={firmware}")
        state, computed = verdict(page)
        check("an altered zip is reported as altered", state == "differs", state)
        check("and the panel says so in words",
              "not the published build" in text(page, "#build-verdict"),
              text(page, "#build-verdict"))
        check("and what it shows is the hash of the bytes it was actually served",
              computed == sha256_of(fake) and computed != text(page, "#build-published"),
              computed)
        page.screenshot(path=harness.artifact("build-panel-altered.png"), full_page=True)
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 900, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        for firmware in FIRMWARES:
            open_panel(page, harness.wallet_url(firmware=firmware))
            describes(page, firmware)

            state, computed = verdict(page)
            check(f"[{firmware}] the zip the page received hashes to the published sha256",
                  state == "match", state)
            check(f"[{firmware}] and that hash is the hash of the built zip",
                  computed == sha256_of(harness.find_asset(f"wallet-{firmware}.zip")),
                  computed)
            page.screenshot(path=harness.artifact(f"build-panel-{firmware}.png"),
                            full_page=True)

        altered(page)

        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    return report()


if __name__ == "__main__":
    sys.exit(main())
