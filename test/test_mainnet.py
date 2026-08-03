"""Mainnet, on purpose: the keys the wallet exports and a signature it makes.

The simulator starts on Testnet and the page shouts when anybody moves it to
Mainnet, because a seed typed into a browser tab has no secure element and any
mainnet key derived from it should be treated as public. That warning is only
honest if mainnet actually works here, and until this file existed nobody had
checked. This is that check, and it costs nothing: no coins, no network, no
broadcast, and a seed that has been published in SeedSigner's own documentation
for years.

Two claims, both about mainnet, both anchored outside the wallet.

**Derivation.** With the device set to Mainnet through its own Settings and
Advanced menus, the test scans the published test seed and exports an account key
through the wallet's own Export Xpub screens, at both standard mainnet paths: the
multisig one, m/48'/0'/0'/2', and the single sig one, m/84'/0'/0'. What the wallet
puts on the screen is read back out of the QR it drew, and compared with a key
derived in mainnet_reference.py, which is BIP39, BIP32 and SLIP-132 written out
from the specifications with nothing but hashlib underneath. That file never
imports embit and never opens wallet.zip; the wallet does all of its work with
embit. Two implementations that share no code have to agree on the fingerprint
and on all 111 characters of the extended key, or this fails.

**Signing.** The test then fabricates a mainnet transaction: an invented UTXO on a
transaction that does not exist, spent to an invented recipient. It goes in by
camera as a base64 PSBT, exactly as one would from a coordinator, and the wallet
is driven through its own review and approve screens. The signed PSBT comes back
out as the animated QR the wallet displays, and the signature in it is verified
here against a BIP143 sighash computed from the transaction the test built, with
an ECDSA verifier written out in the same reference file. Nothing is broadcast,
and nothing could be: the input it spends has never existed.

What this does not establish: that the simulator is safe to put a real seed into.
It is not, and proving that mainnet signing works correctly is precisely what
makes that worth saying loudly. A signature made in a browser tab from a seed
typed into that tab is a signature anybody who has seen the tab could have made.

The camera is fed the way every other scan test here feeds it, from a generated
y4m file, with one addition: the reel is changed between scans. Chromium reopens
the file each time a stream starts (measured, not assumed), so replacing it
between two scans is what holding up a different QR looks like from the wallet's
side. The wallet cannot tell the difference and is never told which reel is up.

Smartcard firmware only. Both firmwares carry the same embit and derive the same
keys from the same seed, and neither the export screens nor the signing path is
smartcard code, so running this twice would double the runtime and prove the same
thing twice.
"""

import base64
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import mainnet_reference as reference
from harness import Log, check, report
# Same wallet, same gestures, and back_to_home knows the one climb that works on
# every screen with a back arrow.
from test_cards_browser import press, back_to_home
# The route into Settings, Advanced and the Bitcoin network setting, written
# down once next door and reused rather than rediscovered.
from test_settings import TO_SETTINGS, TO_NETWORK, TO_MAINNET, indicator

import make_qr_y4m

from playwright.sync_api import sync_playwright

# The seed every test here uses: SeedSigner's own published example, and the one
# the scan tests hold up. Nothing about it is secret and nothing should ever hold
# value, which is the only kind of seed that belongs in a browser.
MNEMONIC = make_qr_y4m.MNEMONIC
FINGERPRINT = "b2269592"

# The two standard mainnet paths, as BIP48 and BIP84 define them. The wallet
# builds these itself from the network setting: it is on Mainnet, so the coin
# type it derives under has to be 0' and not 1'.
MULTISIG_PATH = "m/48'/0'/0'/2'"
SINGLE_SIG_PATH = "m/84'/0'/0'"
# The address the fabricated transaction spends from, one below the single sig
# account.
SIGNING_PATH = SINGLE_SIG_PATH + "/0/0"

# The fabricated UTXO. The txid is a hash of a sentence, so it names no
# transaction that has ever existed, and the recipient is a hash of another one,
# so it is an address nobody holds the key to. A hundred thousand satoshis that
# are not there, ten thousand of them spent on a fee that will never be paid.
UTXO_TXID = reference.sha256(b"seedsigner-sim fabricated utxo, no such transaction exists")
RECIPIENT = reference.hash160(reference.sha256(b"seedsigner-sim fabricated recipient"))
INPUT_SATS = 100_000
OUTPUT_SATS = 90_000

# Where the camera looks. One path, whose contents change between scans.
REEL = harness.artifact("mainnet-camera.y4m")
SEED_REEL = harness.artifact("qr.y4m")

# The wallet's own canvas, decoded by the copy of jsQR the page already serves.
# This is the same library the simulator's camera path uses to read a QR held up
# to it; here it is pointed at the QR the wallet is holding up instead.
READ_SCREEN_QR = """
() => {
  const canvas = document.getElementById('screen');
  const context = canvas.getContext('2d');
  const image = context.getImageData(0, 0, canvas.width, canvas.height);
  const found = jsQR(image.data, image.width, image.height);
  return found ? found.data : null;
}
"""


def load_reel(path):
    """Put a different QR in front of the camera.

    Chromium's fake capture device opens the file when a stream starts, so a
    reel swapped in between two scans is what the next scan sees. Replacing it
    in one rename keeps a half-written file from ever being what the camera
    reads.
    """
    shutil.copyfile(path, REEL + ".next")
    os.replace(REEL + ".next", REEL)


def write_psbt_reel(psbt_bytes, wallet_zip):
    """The fabricated PSBT as a QR, drawn by the wallet's own qrcode library.

    Base64 in a single frame: a small enough transaction to fit one QR, which is
    what a coordinator would show for one input and one output, and it means the
    test is not also testing an animated scan that test_scan.py already covers.
    """
    unpacked = make_qr_y4m.vendored_libraries(wallet_zip)
    try:
        payload = base64.b64encode(psbt_bytes).decode()
        make_qr_y4m.write_y4m(REEL + ".next", payload)
    finally:
        shutil.rmtree(unpacked, ignore_errors=True)
    os.replace(REEL + ".next", REEL)


def read_qr(page, timeout=20):
    """Whatever QR the device is showing, or None if it never shows one."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = page.evaluate(READ_SCREEN_QR)
        if payload:
            return payload
        page.wait_for_timeout(200)
    return None


def collect_animated_qr(page, timeout=120):
    """Read the animated QR the wallet is displaying until it is complete.

    The wallet is showing a UR fountain encoder's frames, so this is what a
    coordinator does: watch the screen and hand each frame to a UR decoder until
    it says it has the whole message. The decoder is the wallet's own copy,
    taken out of wallet.zip, which is transport and nothing more -- a PSBT
    reassembled wrongly cannot produce a signature that verifies, and verifying
    it is the next thing that happens.
    """
    sys.path.append(harness.find_asset("wallet-smartcard.zip"))
    from seedsigner.helpers.ur2.ur_decoder import URDecoder
    from urtypes.crypto import PSBT as UR_PSBT

    decoder = URDecoder()
    frames = set()
    deadline = time.time() + timeout
    while time.time() < deadline and not decoder.is_complete():
        payload = page.evaluate(READ_SCREEN_QR)
        if payload and payload not in frames:
            frames.add(payload)
            decoder.receive_part(payload)
        page.wait_for_timeout(120)

    if not decoder.is_complete():
        return None, frames
    message = decoder.result_message()
    if message.type != "crypto-psbt":
        return None, frames
    return UR_PSBT.from_cbor(message.cbor).data, frames


def wait_screen(log, name, since, what, timeout=90):
    """Wait for a screen, and hand back a mark taken while it is still up.

    Marking here rather than after the keypress that leaves it is the difference
    between a reliable test and a flaky one, and it is the only way to tell four
    consecutive ButtonListScreens apart: they are the same class, so what
    distinguishes them is that each is a *new* line since the last one.
    """
    log.wait(r"display\(\) enter: " + name, timeout, what, since)
    return log.mark()


def export_xpub(page, log, sig_type_presses, path, screenshot):
    """Drive Export Xpub to the QR it ends on, and hand back what it drew.

    The route is the device's own: seed options, sig type, script type, QR
    format, the privacy warning, the details screen, the QR. Static rather than
    the animated default, because a static xpub QR carries the origin and the
    key as text, which is exactly the claim being checked.
    """
    window = log.mark()
    press(page, "ArrowDown")            # Scan transaction -> [Export xpub]
    press(page, "Enter")
    window = wait_screen(log, "ButtonListScreen", window, "the sig type list")

    for key in sig_type_presses:        # nothing for Single Sig, down one for Multisig
        press(page, key)
    press(page, "Enter")
    window = wait_screen(log, "ButtonListScreen", window, "the script type list")

    press(page, "Enter")                # Native Segwit, first of the script types
    window = wait_screen(log, "ButtonListScreen", window, "the QR format list")

    press(page, "ArrowDown")            # Animated (default) -> [Static]
    press(page, "Enter")
    window = wait_screen(log, "WarningScreen", window, "the privacy warning")

    press(page, "Enter")                # I understand
    window = wait_screen(log, "SeedExportXpubDetailsScreen", window,
                         f"the {path} key to be derived", timeout=120)
    harness.save_screen(page, harness.artifact(screenshot))

    press(page, "Enter")                # Export as QR
    wait_screen(log, "QRDisplayScreen", window, "the xpub QR")
    return read_qr(page)


def expected_xpub_string(root, path, version):
    """What the wallet has to have drawn, derived here instead of there.

    The origin in front of the key is part of the claim: the fingerprint is the
    seed's, and the path is the standard mainnet one for that wallet type, which
    is where a wallet on the wrong network gives itself away by deriving under
    coin type 1' instead of 0'.
    """
    key = root.derive(path)
    return "[{}{}]{}".format(root.fingerprint.hex(), path[1:],
                             key.extended_public_key(version))


def compared(drawn, expected):
    """Detail for the xpub checks: the value, and what it should have been."""
    return drawn if drawn == expected else f"{drawn}\n         wanted {expected}"


def main() -> int:
    if not os.path.exists(SEED_REEL):
        print(f"no {SEED_REEL}: run make_qr_y4m.py first", file=sys.stderr)
        return 2
    wallet_zip = harness.find_asset("wallet-smartcard.zip")
    if not wallet_zip:
        print("no wallet-smartcard.zip: run build/build-wallet-zip.sh smartcard first",
              file=sys.stderr)
        return 2

    # --- the reference, before anything trusts it ----------------------------
    # Every expected value below comes out of mainnet_reference.py, so the first
    # thing to establish is that it agrees with values published by people who
    # were not us. If any of these fail, nothing after them means anything.
    print("checking the reference implementation against published vectors")
    for name, passed, detail in reference.check_published_vectors():
        check(name, passed, detail)

    root = reference.root_from_mnemonic(MNEMONIC)
    check("the published seed has the fingerprint the rest of the suite sees",
          root.fingerprint.hex() == FINGERPRINT, root.fingerprint.hex())

    # --- the transaction the wallet will be asked to sign --------------------
    signing_key = root.derive(SIGNING_PATH)
    input_script = reference.p2wpkh_script(reference.hash160(signing_key.public))
    outputs = [(OUTPUT_SATS, reference.p2wpkh_script(RECIPIENT))]
    psbt = reference.build_psbt(UTXO_TXID, 0, INPUT_SATS, input_script, outputs,
                                signing_key.public, root.fingerprint, SIGNING_PATH)
    unsigned_tx = reference.unsigned_transaction(UTXO_TXID, 0, outputs)
    # BIP143's script code for a P2WPKH input is the P2PKH script for the same
    # key hash, which is the one place segwit v0 signing surprises people.
    script_code = b"\x76\xa9\x14" + reference.hash160(signing_key.public) + b"\x88\xac"
    sighash = reference.bip143_sighash(UTXO_TXID, 0, script_code, INPUT_SATS, outputs)
    print(f"  fabricated a {len(psbt)}-byte PSBT spending {INPUT_SATS} sats that do not exist")

    load_reel(SEED_REEL)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            f"--use-file-for-fake-video-capture={REEL}",
        ])
        context = browser.new_context(
            permissions=["camera"],
            viewport={"width": 900, "height": 1000},
        )
        page = context.new_page()
        log = Log(page)
        page.goto(harness.wallet_url())
        log.wait(r"display\(\) enter: MainMenuScreen", 300, "the wallet to boot")
        # The page's own copy, served from this origin. Nothing about the wallet
        # changes; this is the test being able to read what the device drew.
        page.add_script_tag(url="jsQR.js")

        # --- to mainnet, through the device's own menus ----------------------
        print("\ngoing to mainnet")
        check("the wallet starts on Testnet, as this simulator ships",
              indicator(page) == ("Bitcoin network: Testnet", False), str(indicator(page)))
        for key in TO_SETTINGS + TO_NETWORK + TO_MAINNET:
            page.keyboard.press(key)
            page.wait_for_timeout(500)
        check("Settings > Advanced > Bitcoin network reaches Mainnet",
              indicator(page) == ("Bitcoin network: Mainnet", True), str(indicator(page)))
        page.screenshot(path=harness.artifact("mainnet-warned.png"), full_page=True)
        back_to_home(page, log)

        # --- the seed, by camera ---------------------------------------------
        print("\nscanning the published test seed")
        window = log.mark()
        press(page, "Enter")            # Scan, the first tile on the home screen
        window = wait_screen(log, "SeedFinalizeScreen", window, "the scanned seed",
                             timeout=240)
        check("the camera loads the published test seed", True)
        press(page, "Enter")            # Done
        window = wait_screen(log, "SeedOptionsScreen", window, "the seed's own menu")

        # --- the multisig account key ----------------------------------------
        # First, because SeedSigner's multisig export comes home afterwards and
        # the single sig one does not; see the single sig block below.
        print(f"\nexporting {MULTISIG_PATH}")
        drawn = export_xpub(page, log, ("ArrowDown",), MULTISIG_PATH,
                            "mainnet-xpub-multisig.png")
        expected = expected_xpub_string(root, MULTISIG_PATH,
                                        reference.VERSION_ZPUB_MULTISIG)
        check("the multisig account key is the one derived independently",
              drawn == expected, compared(drawn, expected))
        window = log.mark()
        press(page, "Enter")            # leave the QR screen; multisig comes home

        # --- the fabricated transaction --------------------------------------
        print("\nchanging the reel and scanning the fabricated PSBT")
        window = wait_screen(log, "MainMenuScreen", window, "the wallet to come home")
        write_psbt_reel(psbt, wallet_zip)

        press(page, "Enter")            # Scan
        window = wait_screen(log, "ButtonListScreen", window, "the signer list",
                             timeout=240)
        check("the wallet reads the PSBT and asks which seed should sign it", True)
        # The seed is offered without a "(?)" only when its fingerprint matches
        # an input's, so choosing the first entry is choosing a seed the wallet
        # has already matched to this transaction.
        press(page, "Enter")
        window = wait_screen(log, "PSBTOverviewScreen", window, "the transaction overview",
                             timeout=180)
        page.screenshot(path=harness.artifact("mainnet-psbt-overview.png"), full_page=True)

        press(page, "Enter")            # through the overview
        window = wait_screen(log, "WarningScreen", window, "the full-spend warning")
        check("it notices the transaction leaves no change", True)
        press(page, "Enter")
        window = wait_screen(log, "PSBTMathScreen", window, "the amounts")
        press(page, "Enter")
        window = wait_screen(log, "PSBTAddressDetailsScreen", window, "the recipient")
        harness.save_screen(page, harness.artifact("mainnet-psbt-recipient.png"))
        press(page, "Enter")
        window = wait_screen(log, "PSBTFinalizeScreen", window, "the approve screen")

        print("approving")
        signing = log.mark()
        press(page, "Enter")            # Approve transaction
        window = wait_screen(log, "QRDisplayScreen", signing, "the signed PSBT",
                             timeout=240)
        # A QR at all is the wallet's own account of having signed something:
        # approving routes to the signed-PSBT QR when the sign added signatures
        # the PSBT did not already carry, and to a "Signing Failed" warning when
        # it did not. So no warning between the two is the assertion, and the
        # QR that was waited for above is the other half of it.
        check("approving reaches the signed-PSBT QR, not the signing-failed warning",
              log.seen(r"display\(\) enter: WarningScreen", since=signing) is None)
        check("and nothing raised on the way",
              log.seen(r"SystemError|Traceback|RAISED", since=signing) is None)
        page.screenshot(path=harness.artifact("mainnet-psbt-signed.png"), full_page=True)

        signed, frames = collect_animated_qr(page)
        check("the signed PSBT comes back off the screen",
              signed is not None, f"{len(frames)} distinct frames read")
        window = log.mark()
        press(page, "Enter")            # leave the QR screen; signing comes home

        # --- the single sig account key --------------------------------------
        # Last, deliberately. SeedSigner's single sig export ends on Verify
        # Address, which offers to scan a receive address and has no back
        # button, so anything after it would need the page reloaded.
        print(f"\nexporting {SINGLE_SIG_PATH}")
        window = wait_screen(log, "MainMenuScreen", window, "the wallet to come home",
                             timeout=120)
        press(page, "ArrowRight")       # Scan -> [Seeds]
        press(page, "Enter")
        window = wait_screen(log, "ButtonListScreen", window, "the list of loaded seeds")
        press(page, "Enter")            # the one loaded seed
        wait_screen(log, "SeedOptionsScreen", window, "the seed's own menu")
        drawn = export_xpub(page, log, (), SINGLE_SIG_PATH, "mainnet-xpub-singlesig.png")
        expected = expected_xpub_string(root, SINGLE_SIG_PATH, reference.VERSION_ZPUB)
        check("the single sig account key is the one derived independently",
              drawn == expected, compared(drawn, expected))

        browser.close()

    # --- the signature, checked offline --------------------------------------
    # Everything from here on is arithmetic on the bytes the wallet handed back,
    # with no wallet involved.
    print("\nchecking the signature against the sighash")
    if signed is None:
        check("a signed PSBT to check", False, "nothing came back off the screen")
        return report()

    returned_tx, signatures = reference.read_psbt(signed)
    check("the wallet signed the transaction it was given, byte for byte",
          returned_tx == unsigned_tx, returned_tx.hex())
    check("and added exactly one signature", len(signatures) == 1, str(len(signatures)))

    signature = signatures.get(signing_key.public)
    check(f"under the key at {SIGNING_PATH} and no other",
          signature is not None, str([key.hex() for key in signatures]))
    if signature is None:
        return report()

    check("committed to SIGHASH_ALL", signature[-1] == 0x01, hex(signature[-1]))
    r, s = reference.decode_der(signature[:-1])
    check("and the signature verifies against the BIP143 sighash computed here",
          reference.verify_signature(signing_key.public, sighash, r, s),
          f"r={r:#x} s={s:#x}")
    # A verifier that says yes to everything would have passed the line above.
    check("while the same check refuses that signature with one bit changed",
          not reference.verify_signature(signing_key.public, sighash, r, s ^ 1))
    # And a signature that did not commit to where the money goes would also
    # have passed it: this is the same signature against the sighash of a
    # transaction paying one satoshi more.
    elsewhere = reference.bip143_sighash(
        UTXO_TXID, 0, script_code, INPUT_SATS,
        [(OUTPUT_SATS + 1, reference.p2wpkh_script(RECIPIENT))])
    check("and refuses it against a transaction paying a different amount",
          not reference.verify_signature(signing_key.public, elsewhere, r, s))
    check("the signature is low-S, as any node would require", s <= reference.N // 2,
          f"s={s:#x}")

    print(f"\n  signed PSBT: {len(signed)} bytes, read from {len(frames)} QR frames")
    print(f"  signature:   {signature.hex()}")
    return report()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
