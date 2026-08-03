"""
The multisig tutorial, end to end, against the live Bitsaga Signet.

Not part of `test/run.py`: it needs the network, it needs Bitsaga Signet to be
up, and it spends real time waiting for real blocks. `test_tutorial.py` is the
one that runs in the suite, offline. This is the one that proves the whole thing
actually works, and it is the one to run before believing anything.

    python3 test/test_tutorial_live.py               the whole flow, self driving
    python3 test/test_tutorial_live.py --steps 2     stop after two steps
    python3 test/test_tutorial_live.py --mode hands  drive it by hand instead

Two pieces of scaffolding, both in signet_bridge.py and both explained there:
the page is served at https://bitsaga.be, which is the origin the API allows,
and POST /api/broadcast is answered by the test because that endpoint does not
exist on the server yet.

What it asserts is what the panel says, because that is what a visitor sees: it
follows the step titles, refuses any red verdict, and requires the run to reach
the last step with a transaction id that Bitsaga Signet's own proof endpoint
agrees is in a block.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import check, report
from signet_bridge import answer_broadcast, serve_site_at_real_origin

from playwright.sync_api import sync_playwright

URL = ("https://bitsaga.be/seedsigner-simulator/wallet.html"
       "?tutorial=1&debug=1")


def panel(page, selector):
    node = page.locator("#tutorial " + selector)
    return node.inner_text().strip() if node.count() else ""


def shot(page, name):
    page.screenshot(path=harness.artifact(name), full_page=True)


def main(argv) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=0, help="stop after this many")
    parser.add_argument("--mode", default="self", choices=["self", "hands"])
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--port", type=int, default=harness.PORT)
    args = parser.parse_args(argv[1:])

    broadcast = []
    started = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={"width": 1000, "height": 1300},
                                      service_workers="block")
        serve_site_at_real_origin(context, args.port)
        answer_broadcast(context, broadcast)
        page = context.new_page()
        lines = []
        page.on("console", lambda m: lines.append(m.text))
        page.on("pageerror", lambda e: lines.append("PAGEERROR " + str(e)))

        page.goto(URL, wait_until="domcontentloaded")
        deadline = time.time() + 300
        while time.time() < deadline and not any("MainMenuScreen" in l for l in lines):
            page.wait_for_timeout(500)
        check("the simulator boots with the tutorial on the page",
              any("MainMenuScreen" in l for l in lines) and page.locator("#tutorial").count() == 1)
        shot(page, "tutorial-00-ready.png")

        started = time.time()
        page.locator("#tutorial button", has_text="Play").first.click()

        seen = []
        last = None
        idle = time.time()
        while time.time() - idle < 240:
            title = panel(page, ".tut-step")
            if title and title != last:
                last = title
                seen.append(title)
                idle = time.time()
                print(f"  [{time.time() - started:6.0f}s] {title}", flush=True)
                shot(page, "tutorial-%02d-%s.png" % (
                    len(seen), title.lower().replace(" ", "-").replace("'", "")))
            if panel(page, ".tut-verdict") and \
                    page.locator("#tutorial .tut-verdict[data-state=bad]").count():
                check("no step failed", False, panel(page, ".tut-verdict"))
                break
            if title == "Done" and panel(page, ".tut-verdict"):
                break
            if args.steps and len(seen) > args.steps:
                break
            page.wait_for_timeout(1000)

        elapsed = time.time() - started
        print(f"\n{len(seen)} steps in {elapsed:.0f}s", flush=True)

        # Opened first: everything technical is behind the fold, and a closed
        # <details> has no rendered text to read.
        page.locator("#tutorial details").first.evaluate("node => node.open = true")
        details = dict(zip(
            [d.inner_text() for d in page.locator("#tutorial .tut-fold dt").all()],
            [d.inner_text() for d in page.locator("#tutorial .tut-fold dd").all()]))
        for name, value in details.items():
            print(f"  {name}: {value}", flush=True)

        if not args.steps:
            check("the run reaches the last step", last == "Done", last or "nothing")
            check("and it broadcast one transaction", len(broadcast) == 1, str(broadcast))
            txid = details.get("transaction id")
            check("the panel names the transaction it sent",
                  txid and txid == (broadcast[0] if broadcast else None), str(txid))
            if txid:
                with urllib.request.urlopen(
                        f"https://signet.bitsaga.be/api/tx-proof?txid={txid}", timeout=30) as fh:
                    proof = json.load(fh)
                check("Bitsaga Signet proves the spend is in a block",
                      proof.get("txid") == txid and proof.get("height", 0) > 0,
                      f"block {proof.get('height')}")
                print(f"\n  confirmed spend {txid} in block {proof.get('height')}")
        page.locator("#tutorial details").first.evaluate("node => node.open = true")
        shot(page, "tutorial-99-details.png")

        if last != "Done":
            print("\nthe last thirty screens the device reached:")
            screens = [l for l in lines if "display() enter" in l or "[card]" in l]
            for line in screens[-30:]:
                print("  " + line)

        errors = [l for l in lines if l.startswith("PAGEERROR")]
        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\nself driving run: {elapsed:.0f}s")
    return report()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
