"""
The Simulator wallet, end to end, against the live Bitsaga Signet.

Nobody had ever watched a signature come back from this path. The multisig
tutorial proves the 2 of 3 road; the wallet panel is the other one, and until
this file existed every part of it downstream of "the device reads the QR" was
an argument rather than an observation. So the only thing this test accepts as
success is a transaction the chain says is in a block. A green run that ends
anywhere earlier is a failed run, and the checks are written so that it says so.

Not part of `test/run.py`, and for the same reasons `test_tutorial_live.py` is
not: it needs the network, it needs Bitsaga Signet up, it spends real time
waiting for real blocks, and it takes coins out of a faucet that only has so
many to give.

    python3 test/test_wallet_live.py                 the whole thing
    python3 test/test_wallet_live.py --no-multi      the happy path only
    python3 test/test_wallet_live.py --headed        watch it

What is driven, in order, and what is believed at each point:

  1. boot, then a SeedQR held up to Chromium's fake camera, exactly as
     test_scan.py does it, carrying the published BIP39 vector
     "army van defense ...". Believed when the device says SeedFinalizeScreen.
  2. Seeds -> the seed -> Export Xpub -> Single sig -> Native Segwit -> Static,
     driven by the device's own keys. The panel is watching the device's screen
     and connects with nothing pressed on it. Believed when the panel shows a
     balance and an address.
  3. the faucet, believed when the panel shows the payment unconfirmed and then
     confirmed, and when the balance rises by exactly the payout.
  4. a spend: PSBT built in the panel, photographed off the panel's canvas by
     the device's own camera path, reviewed on the device's own screens, signed,
     read back off the device's screen, finalised and broadcast.
  5. `/api/tx-proof`, polled until it answers 200. That is the whole point.
  6. then the case the crypto author flagged as least proven: two inputs and a
     change output, forced by spending more than any single coin holds.

The oracle throughout is the device's own narration ("display() enter: X") and
the chain, never a screenshot and never a sleep. Screenshots are written for
looking at afterwards, and they are evidence of nothing on their own.

One check in here fails on purpose, and it is the finding this file was written
to catch: "the device recognises the change output as its own". It does not.
buildPsbtSingle writes the change output's derivation under key type 0x06, which
is what an *input* map calls a BIP32 derivation; an output map calls it 0x02 and
calls 0x06 a taproot tree. So no wallet reads it, the device decides no change
is coming back, warns "Full Spend!", never shows PSBTChangeDetailsScreen, and
asks the visitor to approve giving away every satoshi that went in. What is
signed is correct and the change really does come back, which is why everything
downstream of it passes; what the visitor was shown was not. One byte in
src/web/signet-coordinator.js, deliberately not fixed here.

Three things about the scaffolding, none of which touches the code under test:

  * the page is served at https://bitsaga.be, because that is the one browser
    origin Bitsaga Signet's API allows. signet_bridge.py does it and explains it.
  * /api/broadcast and /api/scan both exist on the live server now, so nothing
    here stands in for them: every call the panel makes goes to the real host.
    docs/SIGNET-API.md still says broadcast does not exist; it is out of date.
  * keys are typed at the page, and the page deliberately ignores keys aimed at
    the wallet panel, so anything focused inside it is blurred before a press.
    See press() -- that gate is real behaviour, not a test workaround.
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import Log, check, report
from signet_bridge import serve_site_at_real_origin

from playwright.sync_api import sync_playwright

# The origin the API allows, and the path the deployed site serves the page at.
# No ?tutorial=, because the wallet panel is mounted on every page the tutorial
# is not running on and would not exist if it were.
URL = "https://bitsaga.be/seedsigner-simulator/wallet.html?debug=1&firmware=smartcard"

# The SeedQR the fake camera plays: the published BIP39 test vector
# "army van defense carry jealous true garbage claim echo media make crunch",
# the same one the rest of the suite scans. make_qr_y4m.py writes it.
Y4M = harness.artifact("qr.y4m")

# What the panel charges, from wallet-coordinator.js. Repeated rather than
# imported because the point is to check the panel against a number decided
# here: if FEE_RATE there changes, this file should notice and disagree.
FEE_RATE = 2

# The faucet's payout, asserted against /status rather than assumed, because a
# balance check written around a number nobody confirmed proves nothing.
API = "https://signet.bitsaga.be/api"

# Small enough to leave change worth having, large enough to be visibly a spend.
SPEND_SATS = 20000

# The sentence wallet-coordinator.js puts up between "the network has it" and
# "the network has mined it". It is the panel's own statement that something is
# not confirmed yet, and it is the one such statement that does not depend on a
# round trip finishing before the next block does.
WAITING = "Waiting for Bitsaga Signet to put it in a block"


# --- the chain, read from outside the browser --------------------------------
#
# Everything below asks the public API directly rather than through the page.
# The page's own answer to "did that confirm" is the thing under test, so it
# cannot also be the thing that judges it.


def api(path, timeout=30):
    with urllib.request.urlopen(f"{API}{path}", timeout=timeout) as fh:
        return json.load(fh)


def proof(txid):
    """The tx-proof payload, or None while the chain still answers 404."""
    try:
        return api(f"/tx-proof?txid={txid}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _varint(raw, at):
    value = raw[at]
    at += 1
    if value < 0xfd:
        return value, at
    width = {0xfd: 2, 0xfe: 4}.get(value, 8)
    return int.from_bytes(raw[at:at + width], "little"), at + width


def parse_tx(hexstr):
    """A raw transaction as inputs, outputs and a virtual size.

    Written out rather than pulled in because the fee is the one number both
    sides of this test have an opinion about -- the panel estimated it before
    the transaction existed, the chain charged it after -- and comparing them
    means reading the bytes the chain actually holds.
    """
    raw = bytes.fromhex(hexstr)
    at = 4
    segwit = raw[4] == 0x00 and raw[5] == 0x01
    if segwit:
        at = 6
    count, at = _varint(raw, at)
    vin = []
    for _ in range(count):
        txid = raw[at:at + 32][::-1].hex()
        at += 32
        vout = int.from_bytes(raw[at:at + 4], "little")
        at += 4
        length, at = _varint(raw, at)
        at += length + 4                       # scriptSig, then sequence
        vin.append((txid, vout))
    count, at = _varint(raw, at)
    outputs = []
    for _ in range(count):
        value = int.from_bytes(raw[at:at + 8], "little")
        at += 8
        length, at = _varint(raw, at)
        outputs.append((value, raw[at:at + length].hex()))
        at += length
    witness_at = at
    if segwit:
        for _ in range(len(vin)):
            items, at = _varint(raw, at)
            for _ in range(items):
                length, at = _varint(raw, at)
                at += length
    witness = (at - witness_at + 2) if segwit else 0
    total = len(raw)
    base = total - witness
    return {"vin": vin, "vout": outputs, "size": total,
            "vsize": -(-(base * 3 + total) // 4)}


def estimate_vsize(inputs, scripts):
    """wallet-coordinator.js's own estimator, rewritten here.

    Rewritten rather than trusted: this is the number that decides the fee, so
    the test has to be able to disagree with it. Same shape, same 72 byte
    signature ceiling, same weight arithmetic.
    """
    paid = sum(8 + 1 + length for length in scripts)     # every script here is 22 bytes
    base = 4 + 1 + inputs * 41 + 1 + paid + 4
    witness = 2 + inputs * (1 + 1 + 72 + 1 + 33)
    return math.ceil((base * 4 + witness) / 4)


def fee_of(tx):
    """What the chain charged for a transaction, from the outputs it spent."""
    spent = 0
    for txid, vout in tx["vin"]:
        parent = proof(txid)
        if not parent:
            raise AssertionError(f"input {txid}:{vout} is not on this chain")
        spent += parse_tx(parent["tx"])["vout"][vout][0]
    return spent - sum(value for value, _ in tx["vout"])


# --- driving the device ------------------------------------------------------


def press(page, *keys, gap=280):
    """Press the device's buttons, the way a visitor's keyboard does.

    Blurred first, and that is not a workaround. The page swallows every key
    aimed at the wallet panel, because Enter belongs to the button under the
    cursor and the arrows belong to the amount field; and the panel takes focus
    when the drawer opens. So a visitor with the drawer open reaches the device
    by clicking it, and a test reaching it by keyboard has to leave the panel
    first. Doing it here keeps every call site honest about which of the two
    halves it is talking to.
    """
    page.evaluate("() => { const a = document.activeElement;"
                  " if (a && a.closest && a.closest('#wallet, #wallet-strip')) a.blur(); }")
    for key in keys:
        page.keyboard.press(key)
        page.wait_for_timeout(gap)


def screen_is(log, name, timeout, since=0):
    return log.wait(r"display\(\) enter: " + name + r"\b", timeout, name, since=since)


def go_home(page, log, tries=8):
    """Climb back to the home screen, however deep the device happens to be.

    Left goes to the back arrow at the top of a list screen and select takes it,
    so how many screens down this is does not have to be known. Up would be
    shorter and is wrong: on the home screen itself it lands on the power button
    and selecting that reboots the device. wallet-tutorial.js's homeAgain(), in
    Python, and needed for the same reason it is: leaving a QR does not
    necessarily land on the home screen, it lands on whatever the flow put after
    it, which for an xpub export is a status screen.
    """
    for _ in range(tries):
        if log.last_screen() == "MainMenuScreen":
            return True
        press(page, "ArrowLeft")
        if log.last_screen() == "MainMenuScreen":
            return True
        press(page, "Enter")
        page.wait_for_timeout(400)
    return log.last_screen() == "MainMenuScreen"


def advance(page, log, target, tries=10, capture=None):
    """Press select until the device arrives at a screen, and no further.

    What sits between the scan and the signature depends on the transaction and
    on settings -- an overview, the arithmetic, one screen per recipient, the
    change -- so this is driven by where the device has got to rather than by a
    count of presses. Only ever pressing on a screen that has been up for two
    looks in a row, because a key sent mid transition is buffered and taken by
    whatever arrives next, and one stray press past the signing screen dismisses
    the signed QR before anything can read it. This is wallet-tutorial.js's own
    advance(), in Python, with one addition: capture names screens worth
    photographing on the way past. What the device puts up between the scan and
    the signature is the only place its opinion of the transaction is visible,
    and pressing straight through a warning without keeping a picture of it is
    how a device's objection goes unnoticed.
    """
    previous = None
    kept = set()
    for _ in range(tries * 2):
        if log.last_screen() == target:
            return True
        page.wait_for_timeout(900)
        now = log.last_screen()
        if capture and now in capture and now not in kept:
            kept.add(now)
            device_shot(page, capture[now])
        if now == target:
            return True
        if now != previous:
            previous = now                     # still settling; look again
            continue
        previous = None
        press(page, "Enter")
    return log.last_screen() == target


# --- reading the panel -------------------------------------------------------


def panel(page, selector):
    node = page.locator("#wallet " + selector)
    return node.first.inner_text().strip() if node.count() else ""


def complaint(page):
    """Whatever the panel is saying went wrong, which is always fatal here."""
    return panel(page, ".wal-bad")


def says(page):
    """Every progress line the panel has up, joined.

    All of them rather than the first: the panel puts a fixed sentence and a
    changing one in the same class, and which comes first depends on which view
    is up, so asking for one of them asks for whichever happened to be built
    first.
    """
    return "\n".join(page.locator("#wallet .wal-say").all_inner_texts())


def step_now(page):
    """The step of a send the panel says it is on."""
    return panel(page, ".wal-steps li[data-state=now]")


def balance(page):
    """The one big number, as an integer."""
    text = panel(page, ".wal-balance")
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else None


def until(page, condition, timeout, what, allow_complaint=False):
    """Poll the page until something is true, and give up loudly.

    Never a sleep of a guessed length: everything waited for here is either a
    line the device wrote or a number the panel put on screen, and both can be
    looked at. A complaint in the panel ends the wait immediately, because a
    panel that has said what went wrong is not going to change its mind.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not allow_complaint:
            said = complaint(page)
            if said:
                raise AssertionError(f"the panel refused while waiting for {what}: {said}")
        value = condition()
        if value:
            return value
        page.wait_for_timeout(250)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def real_error(message):
    """Is this console message a problem, rather than the two 404s by design?

    harness.page_error already drops the missing tracker, which a clone does not
    serve. The other one is /api/tx-proof, which answers 404 until the
    transaction is in a block and is therefore the confirmation check itself:
    the panel polls it precisely because it 404s, so a run without those in the
    console would be a run in which nothing waited for a block.
    """
    if not harness.page_error(message):
        return False
    return "/api/tx-proof" not in (message.location or {}).get("url", "")


def shot(page, name):
    page.screenshot(path=harness.artifact("wallet-live-" + name + ".png"), full_page=True)


def device_shot(page, name):
    harness.save_screen(page, harness.artifact("wallet-live-" + name + ".png"))


def screens_since(log, since):
    """The screens the device put up, in order, without the repeats.

    Printed on the way past rather than only after a failure: which review
    screens a device chose to show for a given transaction is the evidence that
    it understood it, and a run that passes silently leaves nobody able to say
    whether the change screen ever happened.
    """
    seen = []
    for line in log.lines[since:]:
        found = re.search(r"display\(\) enter: (\w+)", line)
        if found and (not seen or seen[-1] != found.group(1)):
            seen.append(found.group(1))
    return seen


def narrate(log, why):
    print(f"\n  {why}; the last thirty screens the device reached:")
    for line in [l for l in log.lines if "display() enter" in l or "PAGEERROR" in l][-30:]:
        print("    " + line)


# --- the flow ----------------------------------------------------------------


def export_the_account(page, log):
    """Seeds -> the seed -> Export Xpub -> Single sig -> Native Segwit -> Static.

    The path the panel prints in its own landing state, driven key for key. The
    tail is left to advance(): a privacy warning and a details page may sit
    between Static and the QR depending on settings, so where it has arrived is
    the only reliable thing to steer by.
    """
    mark = log.mark()
    press(page, "Enter")                       # leave SeedFinalizeScreen: Done
    screen_is(log, "SeedOptionsScreen", 60, since=mark)

    mark = log.mark()
    press(page, "ArrowDown", "Enter")          # Export Xpub, one below the top
    screen_is(log, "ButtonListScreen", 60, since=mark)

    mark = log.mark()
    press(page, "Enter")                       # Single Sig, first in the list
    screen_is(log, "ButtonListScreen", 60, since=mark)

    mark = log.mark()
    press(page, "Enter")                       # Native Segwit, first in the list
    screen_is(log, "ButtonListScreen", 60, since=mark)

    # Static rather than the animated default, because the animated form is a
    # ur:crypto-account and the page's UR decoder reads ur:crypto-psbt only.
    press(page, "ArrowDown", "Enter")
    return advance(page, log, "QRDisplayScreen", 6)


def claim_and_confirm(page, stage, payout):
    """Press Get test bitcoin, and hold the panel to its own two claims: that
    the payment exists and is not in a block, and then that it is.

    "Not in a block yet" is read from the sentence the panel says the moment the
    faucet answers, not from the transaction appearing as pending in the list.
    Both are true statements of the same fact, but only the first is one this
    test can rely on seeing: the panel has to ask the chain what forty addresses
    hold before a pending row can exist, and on a chain with thirty second
    blocks that round trip is sometimes longer than the wait it is reporting on.
    The pending row is watched for anyway and said out loud when it appears,
    because it appearing is worth knowing and it never appearing is not a
    failure of anything.
    """
    before = balance(page)
    print(f"  [{stage}] balance before the faucet: {before}", flush=True)
    page.locator("#wallet button", has_text="Get test bitcoin").first.click()

    pending_row = [False]

    def waiting():
        if (page.locator("#wallet .wal-pending").count()
                or page.locator("#wallet .wal-list li[data-state=pending]").count()):
            pending_row[0] = True
        return WAITING in says(page)

    until(page, waiting, 180, "the panel to say the faucet has paid")
    check(f"[{stage}] the panel says the faucet payment is not in a block yet", True)
    shot(page, stage + "-funded-pending")

    def mined():
        if (page.locator("#wallet .wal-pending").count()
                or page.locator("#wallet .wal-list li[data-state=pending]").count()):
            pending_row[0] = True
        return WAITING not in says(page) and balance(page) == before + payout

    until(page, mined, 420, "the faucet payment to be mined")
    after = balance(page)
    check(f"[{stage}] the balance rises by exactly the faucet's payout",
          after == before + payout, f"{before} -> {after}, payout {payout}")
    print(f"  [{stage}] the payment was also seen as a pending row: {pending_row[0]}",
          flush=True)
    shot(page, stage + "-funded-confirmed")
    return after


def spend(page, log, stage, amount, expect_inputs, destination=None):
    """One whole round trip, and the transaction id the chain agrees with.

    Every wait in here is on the device's own narration or on the panel's own
    step list. Nothing is timed, because a signature that arrives late is still
    a signature and a signature that never arrives is the finding.

    destination left out means the address the panel fills the field with, which
    is what a visitor sends to. That address is the panel's next change address,
    so the payment and the change come out as two outputs paying the same
    script; passing one in is how the other case gets covered.
    """
    before = balance(page)
    page.locator("#wallet button", has_text="Send").first.click()
    page.locator("#wal-amount").fill(str(amount))
    if destination:
        page.locator("#wal-to").fill(destination)
    destination = page.locator("#wal-to").input_value()
    print(f"  [{stage}] sending {amount} sats to {destination}", flush=True)
    shot(page, stage + "-send-form")

    page.locator("#wallet button", has_text="Build it").first.click()

    # The panel builds the PSBT and puts the codes up before it tells anybody to
    # scan, which is the order that matters: the page hands the camera over at
    # the moment it is opened, so a device already scanning is a device already
    # pointed at a webcam.
    until(page, lambda: "Show it to your signer" in step_now(page),
          120, "the panel to build the PSBT and hold it up")
    check(f"[{stage}] the panel builds a PSBT and shows it as a QR", True,
          panel(page, ".wal-note").split(".")[0])
    shot(page, stage + "-psbt-qr")

    # Now the device is told to look. Home first: whatever screen it was left on
    # is not one Scan can be reached from.
    check(f"[{stage}] the device is back on its home screen",
          go_home(page, log), log.last_screen() or "nothing")

    signing = log.mark()
    press(page, "Enter")                       # Scan is the first thing on the home screen
    screen_is(log, "ScanScreen", 90, since=signing)
    check(f"[{stage}] the device opens its scanner on the panel's canvas", True)

    # The device leaving ScanScreen is the device having decoded the whole
    # transaction off the canvas. Generous, because a Specter split of this
    # size cycles for a while and every missed frame costs a whole turn.
    until(page, lambda: log.last_screen() not in (None, "ScanScreen"),
          420, "the device to read the transaction off the canvas")
    check(f"[{stage}] the device reads the PSBT the panel built", True, log.last_screen())

    reached = advance(page, log, "PSBTFinalizeScreen", 12,
                      capture={"WarningScreen": stage + "-device-warning",
                               "PSBTMathScreen": stage + "-device-math",
                               "PSBTAddressDetailsScreen": stage + "-device-recipient"})
    check(f"[{stage}] the device works through its review screens and offers to sign",
          reached, log.last_screen() or "nothing")
    if not reached:
        narrate(log, f"[{stage}] the device never offered to sign")
        raise AssertionError("the device did not reach PSBTFinalizeScreen")
    device_shot(page, stage + "-device-finalize")

    mark = log.mark()
    press(page, "Enter")                       # approve it
    screen_is(log, "QRDisplayScreen", 240, since=mark)
    check(f"[{stage}] the device signs it and puts the signature on its screen", True)
    device_shot(page, stage + "-device-signature")
    shot(page, stage + "-signature")
    visited = screens_since(log, signing)
    print(f"  [{stage}] the device's own account of it: " + " -> ".join(visited), flush=True)

    # What the device made of the change output, read out of which screens it
    # chose to show. There are two WarningScreens on this road and they are told
    # apart by where they lead: PSBTUnsupportedScriptTypeWarningView skips the
    # arithmetic and goes straight to the recipient, PSBTNoChangeWarningView --
    # "Full Spend! This transaction spends its entire input value. No change is
    # coming back to your wallet." -- goes to it. And a device that has found
    # its own change shows PSBTChangeDetailsScreen before offering to sign.
    #
    # So a run in which the warning is followed by the arithmetic and no change
    # screen ever appears is a run in which the device did not believe the
    # change output was its own. The transaction is still signed and still
    # confirms, because none of that changes what is being signed; what it
    # changes is what the visitor was asked to approve, which was a spend of
    # every satoshi that went in.
    full_spend = any(visited[i] == "WarningScreen" and visited[i + 1] == "PSBTMathScreen"
                     for i in range(len(visited) - 1))
    check(f"[{stage}] the device recognises the change output as its own",
          "PSBTChangeDetailsScreen" in visited and not full_spend,
          "it called this a full spend and showed no change screen" if full_spend
          else "no change screen")

    # From here the panel is on its own: read the codes off the device's screen,
    # finalise, broadcast. The step list is where it says how far it got, and it
    # is also where "sent but not mined" is a state rather than a race: the
    # panel puts the transaction id up on the step before the last one and only
    # moves to the last one when the chain has taken it.
    txid = until(page, lambda: re.search(r"\b[0-9a-f]{64}\b", panel(page, ".wal-mono") or ""),
                 600, "the panel to read the signature back and name what it sent").group(0)
    check(f"[{stage}] the panel reads the signature back and broadcasts it", True, txid)
    check(f"[{stage}] and says so before the chain has mined it",
          "Finish it here" in step_now(page) or WAITING in says(page), step_now(page))
    shot(page, stage + "-broadcast")

    # The device is holding a QR up and nothing needs it any more. Cleared now
    # rather than later, so the next spend starts from a device at home.
    press(page, "Enter")

    # The chain, asked directly. This is the only sentence in this file that is
    # allowed to decide whether any of it worked.
    found = None
    deadline = time.time() + 420
    while time.time() < deadline and not found:
        found = proof(txid)
        if not found:
            page.wait_for_timeout(3000)
    check(f"[{stage}] Bitsaga Signet puts the spend in a block",
          bool(found) and found.get("height", 0) > 0,
          f"{txid} in block {found.get('height')}" if found else f"{txid} never confirmed")
    if not found:
        narrate(log, f"[{stage}] the broadcast never confirmed")
        return {"txid": txid, "confirmed": False}

    tx = parse_tx(found["tx"])
    fee = fee_of(tx)
    check(f"[{stage}] it really spends {expect_inputs} input(s)",
          len(tx["vin"]) == expect_inputs, f"{len(tx['vin'])} inputs, {len(tx['vout'])} outputs")
    check(f"[{stage}] it carries a change output as well as the payment",
          len(tx["vout"]) == 2, f"{[value for value, _ in tx['vout']]}")
    # Worth stating either way. Two outputs paying one script is what the
    # panel's own default produces, and it makes the change output the weaker
    # half of a pair the device cannot tell apart by script; two scripts is the
    # case where the derivation record in the PSBT is the only thing saying
    # which of them is coming back.
    scripts = {script for _, script in tx["vout"]}
    print(f"  [{stage}] the payment and the change pay "
          + ("one script" if len(scripts) == 1 else "two different scripts"), flush=True)

    # The fee the panel worked out before the transaction existed against the
    # fee the chain charged for the one that does. They have to be the same
    # number: the panel gives change the whole remainder, so its estimate is the
    # fee, and an estimate that is wrong low is a transaction nobody relays.
    wanted = FEE_RATE * estimate_vsize(len(tx["vin"]), [22] * len(tx["vout"]))
    check(f"[{stage}] the fee is the panel's own estimate at {FEE_RATE} sat/vB",
          fee == wanted, f"paid {fee}, estimated {wanted}, real vsize {tx['vsize']}")
    check(f"[{stage}] and that is at or a shade above {FEE_RATE} sat/vB of what was mined",
          FEE_RATE <= fee / tx["vsize"] < FEE_RATE + 0.5,
          f"{fee / tx['vsize']:.2f} sat/vB over {tx['vsize']} vB")

    until(page, lambda: "In a block" in step_now(page),
          180, "the panel to agree it is in a block")
    check(f"[{stage}] the panel reaches its own last step", True)
    shot(page, stage + "-confirmed")

    # Everything this wallet sends goes to its own addresses, because there is
    # nobody on this chain to pay: the payment and the change both come back.
    # So the balance can only have fallen by the fee, and by exactly the fee.
    page.locator("#wallet button", has_text="Back to the wallet").first.click()
    until(page, lambda: balance(page) is not None and balance(page) != before,
          180, "the balance to catch up with the spend")
    after = balance(page)
    check(f"[{stage}] the balance falls by the fee and nothing else",
          after == before - fee, f"{before} -> {after}, fee {fee}")
    return {"txid": txid, "confirmed": True, "height": found["height"],
            "inputs": len(tx["vin"]), "outputs": len(tx["vout"]), "fee": fee,
            "scripts": len(scripts)}


def main(argv) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--port", type=int, default=harness.PORT)
    parser.add_argument("--no-multi", action="store_true",
                        help="stop after the single input spend")
    # The faucet allows eight claims per IP per day and a full run takes two, so
    # four runs is the ceiling. The seed is fixed, so its coins survive from
    # one run to the next: with this the spend half can be re-run all day
    # against what earlier runs already claimed, which is what iterating on the
    # signing path actually needs.
    parser.add_argument("--no-claim", action="store_true",
                        help="skip the faucet and spend the balance already there")
    args = parser.parse_args(argv[1:])

    if not os.path.exists(Y4M):
        print(f"no {Y4M}: run test/make_qr_y4m.py first", file=sys.stderr)
        return 2

    status = api("/status")
    payout = status["payout_sat"]
    print(f"  Bitsaga Signet at height {status['height']}, faucet pays {payout} sats, "
          f"blocks every {status['block_seconds']}s", flush=True)
    if not status.get("faucet_ready"):
        print("the faucet says it is not ready; nothing below can pass", file=sys.stderr)
        return 2

    started = time.time()
    sent = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, args=[
            # The seed arrives the way test_scan.py delivers it: a file played
            # into a fake camera, so the run is deterministic and needs no
            # hardware. Once the panel is holding a code up the page hands the
            # device that canvas instead, and this file is never looked at again.
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-video-capture={Y4M}",
        ])
        context = browser.new_context(
            permissions=["camera"],
            viewport={"width": 1280, "height": 1400},
            # The service worker would answer from its own cache, and what is
            # under test is the working tree.
            service_workers="block",
        )
        serve_site_at_real_origin(context, args.port)
        page = context.new_page()
        log = Log(page)
        errors = []
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and real_error(m) else None)
        page.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))

        try:
            page.goto(URL, wait_until="domcontentloaded")

            # ---------------------------------------------------- 1. the seed
            screen_is(log, "MainMenuScreen", 300)
            check("the simulator boots with the wallet drawer on the page",
                  page.locator("#wallet-strip").count() == 1)
            shot(page, "01-booted")

            mark = log.mark()
            press(page, "Enter")               # Scan, the first thing on the home screen
            screen_is(log, "ScanScreen", 90, since=mark)
            check("nothing is decoded from the video's blank lead-in",
                  log.seen(r"display\(\) enter: SeedFinalizeScreen") is None)
            screen_is(log, "SeedFinalizeScreen", 240, since=mark)
            check("the SeedQR decodes and the device loads the seed", True)
            device_shot(page, "02-seed")

            # -------------------------------------------- 2. the account key
            # The drawer is opened before the export, so the panel is already
            # watching the screen when the QR lands on it. Nothing is pressed in
            # the panel to make it connect, which is the claim being tested.
            page.locator("#wallet-strip").click()
            check("opening the drawer puts the panel in its unconnected state",
                  "Export Xpub" in panel(page, ".wal-path"), panel(page, ".wal-path"))

            reached = export_the_account(page, log)
            check("the device exports the account key as a static QR", reached,
                  log.last_screen() or "nothing")
            if not reached:
                narrate(log, "the export never reached a QR")
                raise AssertionError("no QRDisplayScreen after Export Xpub")
            device_shot(page, "03-xpub")

            until(page, lambda: balance(page) is not None, 240,
                  "the panel to read the account key and connect")
            check("the panel connects off the device's screen with nothing pressed on it",
                  True, panel(page, ".wal-balance"))
            shot(page, "04-connected")

            # Back to the home screen, and off the QR, before anything else.
            check("the device comes back to its home screen after the export",
                  go_home(page, log), log.last_screen() or "nothing")

            # ------------------------------------------------- 3. the faucet
            if args.no_claim:
                print("  skipping the faucet, spending what is already there",
                      flush=True)
                until(page, lambda: balance(page) > 0, 60000,
                      "a balance from an earlier run to spend")
            else:
                claim_and_confirm(page, "06", payout)

            # Only now, because a panel with nothing in it and nothing behind it
            # offers one button and it is the faucet: Receive is not on the
            # screen until the wallet has a transaction to its name. The address
            # is still derived at connect time, this is just where it is shown.
            page.locator("#wallet button", has_text="Receive").first.click()
            address = until(page, lambda: re.search(r"\btb1[0-9a-z]{20,}\b",
                                                    panel(page, ".wal-mono") or ""),
                            60, "the panel to show an address").group(0)
            check("the panel derives and shows an address of that account", True, address)
            shot(page, "05-address")
            page.locator("#wallet button", has_text="Back").first.click()

            # -------------------------------------- 4-8. the whole round trip
            first = spend(page, log, "07", SPEND_SATS, expect_inputs=1)
            sent.append(first)

            # ------------------------------- and then the part nobody trusts
            if not args.no_multi and first["confirmed"]:
                claim_and_confirm(page, "08", payout)
                # More than any single coin holds, so the panel has no choice
                # but to reach for a second one. Asked of the panel's own coin
                # list rather than assumed, because this seed is reused across
                # runs and whatever it is carrying from last time is real.
                coins = page.evaluate(
                    "() => WalletCoordinator.current.coins().map(c => c.value)")
                amount = max(coins) + 50000
                print(f"  [09] coins on hand: {sorted(coins, reverse=True)}", flush=True)
                check("there is more in the wallet than its largest coin holds",
                      sum(coins) > amount + 1000, f"{sum(coins)} across {len(coins)} coins")

                # Somewhere other than where the change is going, which the
                # panel's own default is not. With one script the device can
                # tell the two outputs apart by looking at them; with two, the
                # derivation record the panel put in the PSBT is the only thing
                # that says which one is coming home, and that record is the
                # part nobody had watched a device act on.
                page.locator("#wallet button", has_text="Receive").first.click()
                elsewhere = until(page, lambda: re.search(r"\btb1[0-9a-z]{20,}\b",
                                                          panel(page, ".wal-mono") or ""),
                                  60, "an address to pay that is not the change address")
                page.locator("#wallet button", has_text="Back").first.click()

                second = spend(page, log, "09", amount, expect_inputs=2,
                               destination=elsewhere.group(0))
                sent.append(second)
                check("the multi input spend really pays somewhere other than its change",
                      second.get("scripts") == 2, f"{second.get('scripts')} distinct scripts")

        except AssertionError as exc:
            check("the run reaches the end", False, str(exc))
            narrate(log, "the run stopped early")
        finally:
            shot(page, "99-final")
            said = complaint(page)
            check("the panel is not complaining about anything at the end",
                  not said, said)
            check("no page errors and no content security policy violations",
                  not errors, "; ".join(errors[:3]))
            browser.close()

    elapsed = time.time() - started
    print()
    for spent in sent:
        if spent.get("confirmed"):
            print(f"  confirmed {spent['txid']} in block {spent['height']}: "
                  f"{spent['inputs']} in, {spent['outputs']} out, {spent['fee']} sats fee")
            print(f"    {API}/tx-proof?txid={spent['txid']}")
        else:
            print(f"  NOT CONFIRMED {spent['txid']}")
    if not sent:
        print("  no transaction was ever broadcast")
    print(f"\nwhole run: {elapsed:.0f}s")
    return report()


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
