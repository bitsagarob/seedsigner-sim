"""
The device art as a control: what a thumb can press, and how big it gets.

Everything here is about the shell in front of the wallet rather than about the
wallet, so nothing waits for Python: the art is drawn before the worker has
finished fetching anything, and this file is seconds rather than minutes. The
keyboard is left to the rest of the suite, which drives every wallet through it
and would notice at once if it stopped working.

Two things are being pinned down.

**The screen is not a control.** It used to be the select key, on the grounds
that it is the biggest target on the shell, and on a phone that meant a tap
anywhere on the home menu opened the camera. A SeedSigner has no touchscreen,
so neither has this: only the drawn keys answer. The proof is a second device
rendered on the page with a counting onKey, driven by real touch events through
the DevTools protocol -- a tap on the screen has to count nothing, a tap on a
key has to count exactly one, and a finger held on a key has to keep counting
one, because a hardware button does not repeat either.

**The shell can have the screen.** A landscape device fitted to a portrait
phone's width draws keys about 23 pixels across, which is not a thumb target.
The page offers a mode where the device takes the whole viewport, fitted to its
height as well as its width, so that turning the phone sideways is what makes
the keys big. This checks both halves of that, in both orientations, and that
the wallet's own 320x240 screen keeps its shape throughout, since the tests
that compare it are comparing pixels.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import check, report

from playwright.sync_api import sync_playwright

PHONE = {"width": 360, "height": 780}
PHONE_SIDEWAYS = {"width": 780, "height": 360}
DESKTOP = {"width": 1200, "height": 900}

# A second device, rendered by the same call wallet.html makes, with an onKey
# that only counts. Nothing is sent to the wallet, so what a press does is not
# in the way of asking whether a press happened.
PROBE = """
() => {
  const box = document.createElement("div");
  box.id = "probe";
  box.style.cssText = "position:fixed;left:0;bottom:0;width:340px;z-index:99";
  document.body.appendChild(box);
  window.__presses = [];
  window.SeedSignerDevice.render(box, {
    screenWidth: 320, screenHeight: 240, interactive: true, card: false,
    onKey: (channel) => window.__presses.push(channel),
  });
}
"""


def centre(page, selector):
    box = page.locator(selector).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def presses(page):
    return page.evaluate("() => window.__presses")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=PHONE, has_touch=True,
                                      service_workers="block")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(harness.wallet_url())
        page.wait_for_selector("#device .ssd-svg")

        # --- the order of the page, top to bottom ----------------------------
        # The title, then the warning, then the device, and the control that
        # opens the technical details above the device with them. Read off the
        # rendered boxes rather than off the source order, because either one
        # can be moved without the other.
        title = page.locator("h1").bounding_box()
        warn = page.locator("p.warn").bounding_box()
        details = page.locator("#build > summary").bounding_box()
        device = page.locator("#device").bounding_box()
        check("the title is above the simulator warning",
              title["y"] + title["height"] <= warn["y"],
              f"title ends at {int(title['y'] + title['height'])}, "
              f"warning starts at {int(warn['y'])}")
        check("the simulator warning sits above the device",
              warn["y"] + warn["height"] <= device["y"],
              f"warning ends at {int(warn['y'] + warn['height'])}, "
              f"device starts at {int(device['y'])}")
        check("and so does the technical details control",
              details["y"] + details["height"] <= device["y"],
              f"details ends at {int(details['y'] + details['height'])}, "
              f"device starts at {int(device['y'])}")

        # --- the screen is not a button --------------------------------------
        page.evaluate(PROBE)
        slot = "#probe .ssd-screen-slot"
        check("the screen claims to be no control",
              page.locator(slot).get_attribute("role") is None
              and page.locator(slot).get_attribute("aria-label") is None,
              str(page.locator(slot).get_attribute("role")))

        x, y = centre(page, slot)
        page.touchscreen.tap(x, y)
        page.wait_for_timeout(200)
        check("tapping the screen does nothing at all", presses(page) == [],
              str(presses(page)))
        page.mouse.click(x, y)
        page.wait_for_timeout(200)
        check("and neither does clicking it", presses(page) == [], str(presses(page)))

        # --- and the keys are ------------------------------------------------
        select = "#probe [data-ssd-control=select]"
        x, y = centre(page, select)
        page.touchscreen.tap(x, y)
        page.wait_for_timeout(300)
        # A tap that reached both the pointer handler and the mouse event the
        # browser synthesises afterwards would count two.
        check("one tap on the select key is exactly one press",
              presses(page) == [5], str(presses(page)))

        page.evaluate("() => { window.__presses.length = 0; }")
        page.mouse.click(x, y)
        page.wait_for_timeout(200)
        check("and one click with a mouse is exactly one press",
              presses(page) == [5], str(presses(page)))

        # A finger that lands and stays. Playwright's tap is down and up in one
        # call, so the two halves are dispatched separately here.
        page.evaluate("() => { window.__presses.length = 0; }")
        cdp = context.new_cdp_session(page)
        cdp.send("Input.dispatchTouchEvent", {
            "type": "touchStart",
            "touchPoints": [{"x": x, "y": y, "radiusX": 12, "radiusY": 12}],
        })
        page.wait_for_timeout(2000)
        held = presses(page)
        down = page.locator(select).evaluate("node => node.classList.contains('ssd-down')")
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_timeout(400)
        check("a finger held on a key sends one press and never repeats",
              held == [5], f"{len(held)} presses in two seconds")
        check("and the key is visibly down the whole time it is held", down, str(down))
        check("lifting it sends nothing more", presses(page) == [5], str(presses(page)))
        check("and the key comes back up",
              not page.locator(select).evaluate(
                  "node => node.classList.contains('ssd-down')"))

        # Two fingers at once are one press, not two: the second is ignored
        # rather than answered, because a hardware key cannot be pressed twice.
        page.evaluate("() => { window.__presses.length = 0; }")
        ux, uy = centre(page, "#probe [data-ssd-control=up]")
        cdp.send("Input.dispatchTouchEvent", {
            "type": "touchStart",
            "touchPoints": [{"x": x, "y": y}, {"x": ux, "y": uy}],
        })
        page.wait_for_timeout(300)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_timeout(200)
        check("two fingers landing together are one press",
              presses(page) == [5], str(presses(page)))
        page.evaluate("() => document.getElementById('probe').remove()")

        # --- the shell filling the screen -------------------------------------
        def key_size():
            box = page.locator("#device [data-ssd-control=select]").bounding_box()
            return min(box["width"], box["height"])

        # Long side over short side, so this says the same thing whichever way
        # up the shell is: 320x240 is 4:3 laid across a phone as well as along
        # it, and anything that stretched it would not be.
        def screen_shape():
            box = page.locator("#device .ssd-screen-slot").bounding_box()
            return (max(box["width"], box["height"])
                    / min(box["width"], box["height"]))

        in_page = key_size()
        check("a key in the page is nowhere near a thumb target", in_page < 30,
              f"{in_page:.0f}px at {PHONE['width']}px wide")
        check("the fullscreen control is offered on a phone",
              page.locator("#fullscreen").is_visible())
        page.locator("#fullscreen").click()
        page.wait_for_timeout(400)
        device = page.locator("#device").bounding_box()
        # Laid across the phone, so what it fills upright is the height.
        check("it lays the device along the phone's long side",
              device["height"] >= PHONE["height"] * 0.9
              and device["width"] <= PHONE["width"] + 1,
              f"{int(device['width'])}x{int(device['height'])} in "
              f"{PHONE['width']}x{PHONE['height']}")
        # 44 pixels is the smallest target every accessibility guideline agrees
        # a finger can be asked to hit.
        check("which is what makes the keys thumb sized", key_size() >= 44,
              f"{key_size():.0f}px, was {in_page:.0f}px in the page")
        check("the wallet's screen keeps its 4:3 shape, unstretched",
              abs(screen_shape() - 4 / 3) < 0.02, f"{screen_shape():.3f}")
        page.screenshot(path=harness.artifact("device-360-fullscreen.png"))

        page.set_viewport_size(PHONE_SIDEWAYS)
        page.wait_for_timeout(400)
        device = page.locator("#device").bounding_box()
        check("turned sideways it is upright and fills the height",
              device["height"] >= PHONE_SIDEWAYS["height"] - 2
              and device["width"] <= PHONE_SIDEWAYS["width"] + 1,
              f"{int(device['width'])}x{int(device['height'])} in "
              f"{PHONE_SIDEWAYS['width']}x{PHONE_SIDEWAYS['height']}")
        check("and the keys are the same thumb sized keys", key_size() >= 44,
              f"{key_size():.0f}px")
        check("and the screen is still 4:3", abs(screen_shape() - 4 / 3) < 0.02,
              f"{screen_shape():.3f}")
        page.screenshot(path=harness.artifact("device-sideways-fullscreen.png"))

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape leaves it", not page.evaluate(
            "() => document.body.classList.contains('solo')"))
        page.set_viewport_size(PHONE)
        page.wait_for_timeout(300)
        page.locator("#fullscreen").click()
        page.wait_for_timeout(200)
        page.locator("#fullscreen").click()
        page.wait_for_timeout(300)
        check("and so does the control that opened it",
              not page.evaluate("() => document.body.classList.contains('solo')")
              and page.locator("#fullscreen").get_attribute("aria-pressed") == "false")
        check("focus went back to the page so the wallet keeps the keyboard",
              page.evaluate("document.activeElement === document.body"),
              page.evaluate("document.activeElement.tagName"))

        page.set_viewport_size(DESKTOP)
        page.wait_for_timeout(300)
        check("nothing about it is offered on a desktop, which has the room",
              not page.locator("#fullscreen").is_visible())
        check("no page errors", not errors, "; ".join(errors[:3]))

        browser.close()

    return report()


if __name__ == "__main__":
    sys.exit(main())
