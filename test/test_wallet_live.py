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
    python3 test/test_wallet_live.py --new-seed      make the seed on the device
    python3 test/test_wallet_live.py --headed        watch it

What is driven, in order, and what is believed at each point:

  1. boot, then a SeedQR held up to Chromium's fake camera, exactly as
     test_scan.py does it, carrying the published BIP39 vector
     "army van defense ...". Believed when the device says SeedFinalizeScreen.
     With --new-seed this one stage is replaced: the device makes the seed
     itself, out of its own dice screens, and no camera is opened at all. See
     create_a_seed(). Everything from stage 2 down is the same code either way.
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

A second finding, since fixed, and it is why --new-seed steers by button numbers
rather than by names. src/web/wallet-worker.js narrates "View.run enter:
<ViewName>" so that a view which stalls before it ever builds a screen still
names itself. It used to patch View.run, which never fired: every View in
SeedSigner overrides run(), so the base class's run() is an attribute nothing
ever looks up, and a whole boot produced exactly zero of those lines. It now
wraps Destination.run, the one funnel the controller drives every transition
through, and the lines are real. This file still steers by button numbers,
because that is what it was written against and the numbers are no less true.

--new-seed and the faucet. A seed nobody has ever used holds nothing, so this
mode cannot be run against coins an earlier run left behind and cannot be
combined with --no-claim; it has to ask the faucet. The faucet allows a fixed
number of claims per IP per day, and once that is spent there is no way to fund
the wallet and no way to reach the spend. That is not this file failing, and it
is not this file passing either, so it is neither: a refusal at the faucet is
caught as FaucetRefused, said out loud, and the run exits 3. 0 still means every
check passed, 1 still means one did not.

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
# wallet=1 goes past the game: the page boots into DOOM and fetches the wallet
# only on KEY1, KEY2, KEY3, and this file is about the wallet.
URL = ("https://bitsaga.be/seedsigner-simulator/wallet.html"
       "?debug=1&wallet=1&firmware=smartcard")

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

# --- what --new-seed rolls, and what those rolls have to come out as ----------
#
# The device wants 50 rolls for a 12 word seed and refuses a run of them that is
# not random enough (mnemonic_generation.dice_entropy_is_sufficient, Shannon
# entropy of the roll string, threshold 2.0 bits per symbol). So the rolls
# cannot be 50 of the same face; they walk the six faces in a fixed order
# instead, which comes to 2.58 bits and is still the same every run.
#
# Fixed rather than random on purpose. A random run would make an unreachable
# wallet every time and strand its change there forever, on a chain whose faucet
# is rationed; these rolls always make the same wallet, so what one run leaves
# behind is still there for the next one. It is a new seed the first time and a
# known one afterwards, and either way the device is the thing that built it.
#
# The keyboard is a 3x3 grid holding six keys, 1 2 3 above 4 5 6, and it opens
# on 1. One move per roll walks 1 2 3 6 5 4 and back to 1, so no press ever
# reaches an edge and nothing depends on how the keyboard wraps.
DICE_ROLLS = 50
DICE_MOVES = ["ArrowRight", "ArrowRight", "ArrowDown", "ArrowLeft", "ArrowLeft", "ArrowUp"]

# The master fingerprint those rolls have to produce, worked out here rather
# than recorded from a run: SeedSigner hashes the roll string with SHA-256, take
# the first 16 bytes as BIP39 entropy, and that mnemonic is
#
#   travel assume abandon brush behind sauce fly badge census dose that drastic
#
# whose BIP32 master key hashes to this. The panel reads the fingerprint out of
# the key origin the device exported, so checking it says the device's dice
# screens, its entropy and its export all agree with the arithmetic. It is also
# how this mode proves it is not quietly running on the scanned vector, which is
# b2269592.
NEW_SEED_FINGERPRINT = "c55970ff"


class FaucetRefused(Exception):
    """The faucet said no, which is a state of the world and not a bug.

    Its own words, so the run can repeat them rather than paraphrase. Raised
    instead of failing a check because there is nothing here to fix: the daily
    allowance is spent and the only cure is tomorrow.
    """


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
                  " if (a && a.closest && a.closest('#wallet, #wallet-button')) a.blur(); }")
    for key in keys:
        page.keyboard.press(key)
        page.wait_for_timeout(gap)


def screen_is(log, name, timeout, since=0):
    return log.wait(r"display\(\) enter: " + name + r"\b", timeout, name, since=since)


def listening(log, timeout, since=0):
    """Wait until the device is parked on a keypress again.

    The page hands the device its buttons through one slot, not a queue: a key
    sits in it until the device takes it, and a second key posted before then
    overwrites the first rather than lining up behind it. So two presses either
    side of a screen change are not safe at any fixed spacing. The first is
    posted while the new screen is still being built, the second replaces it,
    and the device sees one key where two were sent.

    "wait_for keys=" is the device saying it has taken everything it was given
    and is blocked waiting for more, which is the only moment a press is certain
    to be seen. It cost this file two runs to find that out.
    """
    return log.wait(r"wait_for keys=", timeout, "the device to want a key", since=since)


def tap(page, log, key):
    """One button, and no return until the device has actually taken it."""
    mark = log.mark()
    press(page, key)
    listening(log, 60, since=mark)


def chose(log, screen, index, timeout, since=0):
    """Wait for a list screen to close, and hold it to which button closed it.

    The device narrates the way out of a screen as well as the way in:
    "display() exit: ButtonListScreen -> 4" is it saying the fifth button was
    the one taken. That matters on the road to the dice, because every menu on
    it is a ButtonListScreen and the name alone cannot tell them apart, so a
    press that lands one row off would otherwise go unnoticed until something
    much later made no sense.

    It is the exit line rather than the entry line for the next screen because
    it is the only narration that carries a number. The device also traces
    "View.run enter: <ViewName>", which names the destination outright and is
    the better oracle for anything written from here on; this file predates it
    working at all. See this file's header.
    """
    return log.wait(rf"display\(\) exit: {screen} -> {index}\b", timeout,
                    f"{screen} -> {index}", since=since)


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
        # The address verification screens are the one place left-then-select
        # does not climb: the device is sitting on the result of checking an
        # address the panel held up for it, and that is dismissed rather than
        # navigated out of. It only appears now because the panel answers Verify
        # Address by itself, which is the whole point of it.
        if "AddressVerification" in (log.last_screen() or ""):
            press(page, "Enter")
            page.wait_for_timeout(500)
            continue
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
#
# Stage 1 is the only stage with two versions of it, and they are the two ways a
# visitor can arrive at a loaded seed. Both end on SeedFinalizeScreen, which is
# where export_the_account() starts, so nothing below stage 1 knows or cares
# which of them ran.


def scan_the_seed(page, log):
    """A SeedQR held up to the fake camera, which is where this file began.

    The published BIP39 vector, the same one the rest of the suite scans, so the
    wallet it lands on is the same wallet every run and whatever an earlier run
    left in it is still there.
    """
    mark = log.mark()
    press(page, "Enter")                       # Scan, the first thing on the home screen
    screen_is(log, "ScanScreen", 90, since=mark)
    check("nothing is decoded from the video's blank lead-in",
          log.seen(r"display\(\) enter: SeedFinalizeScreen") is None)
    screen_is(log, "SeedFinalizeScreen", 240, since=mark)
    check("the SeedQR decodes and the device loads the seed", True)


def create_a_seed(page, log):
    """Home -> Seeds -> Create a seed -> New seed (dice) -> 12 words -> 50 rolls.

    The path a first time visitor takes when they have nothing to scan, and the
    reason this mode exists: the device's own entropy, its own word review and
    its own finalisation had never been driven by anything, because every other
    test in this suite starts from a seed that already existed.

    Dice rather than the camera, which is the other offer on that menu. The
    camera's entropy is os.urandom and the wall clock mixed into a photograph,
    so it cannot be checked against anything; a fixed run of dice is a number
    this file can work out for itself, which is what NEW_SEED_FINGERPRINT is.

    Where a press is counted, it is because a ButtonListScreen does not say
    which of its buttons is selected, so there is nothing else to steer by; and
    every count is then held to the button number the device says it took, so a
    press that lands one row off is caught where it happens. Every press waits
    for the device to want it (see tap), because the button slot holds one key
    and the second of two fast presses replaces the first.

    The stretch between the rolls and the backup prompt is not counted at all
    and is left to advance(): how many pages of words the device decides to show
    is the device's business, and pressing past a screen because a count said so
    is how a device's own opinion goes unread.
    """
    # Seeds with nothing in memory is Load a Seed: SeedsMenuView has no list of
    # its own to show, so it forwards, and the screen after the home grid is
    # already that list.
    tap(page, log, "ArrowRight")               # Scan -> Seeds on the home grid
    mark = log.mark()
    tap(page, log, "Enter")
    chose(log, "MainMenuScreen", 1, 30, since=mark)
    check("with no seeds in memory, Seeds goes straight to Load a Seed",
          log.last_screen() == "ButtonListScreen", log.last_screen() or "nothing")

    # Scan a SeedQR, Enter 12-word, Enter 24-word, From SeedKeeper, [Create a
    # seed]. The list is built from settings and this is what the simulator's
    # settings make it: word lengths 12 and 24, smartcard support on, Electrum,
    # SLIP39, Aezeed and the three backup formats all off.
    for _ in range(4):
        tap(page, log, "ArrowDown")
    mark = log.mark()
    tap(page, log, "Enter")
    chose(log, "ButtonListScreen", 4, 30, since=mark)
    check("Create a seed is the fifth thing offered, and it is what was taken", True)

    # Two ways to make a seed and they are both called "New seed": the camera
    # first, the dice second.
    tap(page, log, "ArrowDown")
    mark = log.mark()
    tap(page, log, "Enter")
    chose(log, "ButtonListScreen", 1, 30, since=mark)

    mark = log.mark()
    tap(page, log, "Enter")                    # 12 words, first of 12 and 24
    chose(log, "ButtonListScreen", 0, 30, since=mark)
    check("the device asks for its 50 dice rolls",
          log.last_screen() == "ToolsDiceEntropyEntryScreen",
          log.last_screen() or "nothing")

    rolling = log.mark()
    for roll in range(DICE_ROLLS):
        if roll:
            tap(page, log, DICE_MOVES[(roll - 1) % len(DICE_MOVES)])
        tap(page, log, "Enter")
        if roll == DICE_ROLLS // 2:
            device_shot(page, "02-dice")

    # The screen returns itself once the last roll lands, so arriving anywhere
    # else is the whole roll sequence having been counted. An ErrorScreen here
    # is the device saying the rolls were not random enough, which would mean
    # DICE_MOVES no longer walks all six faces.
    check("the rolls are random enough for the device to accept them",
          log.seen(r"display\(\) enter: ErrorScreen", since=rolling) is None)

    reached = advance(page, log, "SeedWordsBackupTestPromptScreen", 12,
                      capture={"DireWarningScreen": "02-dire-warning",
                               "SeedWordsScreen": "02-words"})
    check("the device warns about the words, shows them, and offers to test them",
          reached, log.last_screen() or "nothing")
    if not reached:
        narrate(log, "the new seed never reached its backup prompt")
        raise AssertionError("no SeedWordsBackupTestPromptScreen after the rolls")

    # Verify, Review, [Skip]. Skip is the one that finalises: verify is a game of
    # picking words back out and review is the pages again, and neither is what
    # this test is here to drive.
    tap(page, log, "ArrowDown")
    tap(page, log, "ArrowDown")
    mark = log.mark()
    tap(page, log, "Enter")
    chose(log, "SeedWordsBackupTestPromptScreen", 2, 30, since=mark)
    screen_is(log, "SeedFinalizeScreen", 120, since=mark)
    check("the device finalises the seed it built", True)
    check("and built it with no camera anywhere in it",
          log.seen(r"display\(\) enter: ScanScreen") is None)


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

    # A complaint in the panel while this wait is running came out of the claim
    # and nothing else, so it is the faucet's own answer: out of allowance, out
    # of coins, or this address asking again too soon. None of those is a defect
    # in anything under test, so it leaves by a different door.
    try:
        until(page, waiting, 180, "the panel to say the faucet has paid")
    except AssertionError:
        said = complaint(page)
        if said:
            raise FaucetRefused(said)
        raise
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
    elif not page.locator("#wal-to").input_value():
        # Whichever way the panel has it. It used to arrive with one of the
        # wallet's own addresses already in the box and now deliberately
        # arrives empty, on the grounds that an address nobody typed is an
        # address nobody reads; either way the visitor with nobody to pay
        # presses the panel's own button, so that is what this presses.
        page.locator("#wallet button", has_text="Use one of my addresses").first.click()
    destination = page.locator("#wal-to").input_value()
    print(f"  [{stage}] sending {amount} sats to {destination}", flush=True)
    shot(page, stage + "-send-form")

    page.locator("#wallet button", has_text="Create transaction").first.click()

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
    parser.add_argument("--new-seed", action="store_true",
                        help="make a seed on the device instead of scanning one")
    args = parser.parse_args(argv[1:])

    # The two cannot meet. --no-claim exists to spend what an earlier run left
    # behind, and the first run of --new-seed has nothing behind it: the wallet
    # it makes has never been paid, so there is no balance to skip the faucet
    # for. Refused here rather than left to time out, because --no-claim waits
    # for a balance for as long as it takes.
    # Allowed, with one caveat worth knowing. The dice rolls are fixed, so the
    # seed --new-seed makes is the same seed every time and keeps what earlier
    # runs paid it: after one funded run this pair costs the faucet nothing, and
    # the signing path can be driven all day. It is only the very first run on a
    # given chain that has nothing to spend, and that one ends in the wait below
    # saying so rather than in a refusal here.

    if not os.path.exists(Y4M):
        # Wanted even by --new-seed, which never decodes it: Chromium is told to
        # back its fake camera with this file at launch, and the device still
        # opens that camera later to read the PSBT off the panel's canvas.
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
    refusal = None

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
                  page.locator("#wallet-button").count() == 1)
            shot(page, "01-booted")

            if args.new_seed:
                create_a_seed(page, log)
            else:
                scan_the_seed(page, log)
            device_shot(page, "02-seed")

            # -------------------------------------------- 2. the account key
            # The drawer is opened before the export, so the panel is already
            # watching the screen when the QR lands on it. Nothing is pressed in
            # the panel to make it connect, which is the claim being tested.
            page.locator("#wallet-button").click()
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

            # Exporting an xpub leaves the device on Verify Address, asking to be
            # shown a receive address from the wallet that just imported the key.
            # That is the check that the key the panel took is the key the device
            # holds, so the panel answers it without being asked: the address is
            # on screen and its QR is already up at the device's camera.
            shown = panel(page, ".wal-verify") or ""
            first = page.locator("#wallet .wal-verify .wal-mono").inner_text().strip() \
                if page.locator("#wallet .wal-verify").count() else ""
            check("and offers its first address for the device to check",
                  "Your first address" in shown and first.startswith("tb1"),
                  first or shown[:60] or "no verify block")
            shot(page, "04-connected")

            # And then the check itself, which is the reason the address is
            # offered at all. The panel holds it at the camera, the device reads
            # it and walks its own derivation path looking for it. That search is
            # a BaseThread, and this simulator drops those because most of them
            # are animation loops that would never return; dropped, the screen
            # sat on an index that never moved and its Skip 10 fed a counter
            # nobody read. It runs inline now, bounded, so a match comes back at
            # once and a miss gives up instead of wedging the worker.
            page.locator("#wallet button", has_text="Show it to the device").first.click()
            page.wait_for_timeout(400)
            mark = log.mark()
            press(page, "Enter")                      # the prompt's own Scan
            found = advance(page, log, "SeedAddressVerificationSuccessScreen", 8)
            check("the device finds the address on its own derivation path",
                  found, log.last_screen() or "nothing")
            device_shot(page, "04b-address-verified")
            go_home(page, log)

            # The one place a generated seed can be checked against arithmetic
            # this file did itself. The fingerprint came out of the key origin
            # the device wrote into its export, so it is the device's answer to
            # what those 50 rolls mean, and NEW_SEED_FINGERPRINT is this file's.
            if args.new_seed:
                made = page.evaluate(
                    "() => WalletCoordinator.current.account.fingerprint")
                check("the seed the device rolled is the one those rolls define",
                      made == NEW_SEED_FINGERPRINT,
                      f"{made}, and this file worked out {NEW_SEED_FINGERPRINT}")
                # Nothing at all the first time these rolls are ever made, and
                # whatever the last run left the time after that.
                print(f"  the wallet those rolls make holds {balance(page)} sats",
                      flush=True)

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

        except FaucetRefused as exc:
            refusal = str(exc)
            print(f"\n  THE FAUCET REFUSED: {refusal}", flush=True)
            print("  Every check above this line ran and is real. Nothing below it "
                  "could be tried,\n  because there are no coins to spend and no way "
                  "to get any today.", flush=True)
            shot(page, "06-faucet-refused")
        except AssertionError as exc:
            check("the run reaches the end", False, str(exc))
            narrate(log, "the run stopped early")
        finally:
            shot(page, "99-final")
            said = complaint(page)
            check("the panel is not complaining about anything at the end",
                  not said or said == refusal, said)
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

    code = report()
    if refusal and code == 0:
        print("\nNOT A PASS AND NOT A FAILURE. The faucet refused: " + refusal
              + "\nEverything this run could check, it checked. The spend, the "
                "signature and the\nbroadcast were never attempted, so this run "
                "says nothing about them either way.")
        return 3
    return code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
