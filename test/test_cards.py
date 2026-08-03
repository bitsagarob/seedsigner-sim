"""
Drive the simulated card registry the way pysatochip does, without a browser.

This is the fast half of the smartcard story: no Pyodide, no page, just the
pyscard stand-in in src/smartcard being asked the same questions the wallet asks
it. If this fails, test_cards_browser.py will fail too, and this one says why in
two seconds rather than two minutes.
"""

import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# The package warns when imported outside the browser, because there it would
# be shadowing pyscard. Here that shadowing is the point.
os.environ.setdefault("SEEDSIGNER_SIM_ALLOW_FAKE_SMARTCARD", "1")
sys.path.insert(0, HERE)
# The stand-in ships with the wallet rather than being installed, so it is
# imported from the checkout it lives in.
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import harness
from harness import check, report

# The card's crypto is embit's, and the client that has to accept what it answers
# is pysatochip's own parser. Both ship inside wallet.zip rather than being
# installed anywhere, and zipimport reads a pure-Python package straight out of
# one -- so this is the same copy the browser runs, not a lookalike from PyPI.
# Last on the path, not first: the zip carries a copy of the stand-in too, and
# the one under test is the checkout's.
WALLET_ZIP = harness.find_asset("wallet.zip")
if WALLET_ZIP is None:
    sys.exit("no wallet.zip: run build/build-wallet-zip.sh first")
sys.path.append(WALLET_ZIP)

from embit import bip32
from embit.hashes import hash160
from pysatochip.CardDataParser import CardDataParser

from smartcard import simulated_card
from smartcard.CardMonitoring import CardMonitor, CardObserver
from smartcard.CardRequest import CardRequest
from smartcard.Exceptions import (
    CardConnectionException,
    CardRequestTimeoutException,
    NoCardException,
)


class FakeTray:
    """Stands in for the page's half of the card tray, see wallet-cards.js."""

    def __init__(self):
        self.slot = simulated_card.EMPTY
        self.published = {}

    def inserted(self):
        return self.slot

    def wait(self, timeout_ms):
        return self.slot

    def publish(self, index, state):
        self.published[index] = state


def uid_of(card):
    return hashlib.sha1(bytes(card.uid)).hexdigest()


def read_status(service):
    """What CardConnector.card_get_status() sends and unpacks."""
    connection = service.createConnection()
    connection.connect()
    response, sw1, sw2 = connection.transmit([0xB0, 0x3C, 0x00, 0x00])
    assert (sw1, sw2) == (0x90, 0x00), (sw1, sw2)
    return {"pin_tries": response[4], "is_seeded": bool(response[9]), "setup_done": bool(response[10])}


def read_uid(service):
    """CPLC + IIN + CIN, hashed, exactly as RemovalObserver does it."""
    connection = service.createConnection()
    connection.connect()
    blob = []
    for p1, p2 in ((0x9F, 0x7F), (0x00, 0x42), (0x00, 0x45)):
        response, sw1, sw2 = connection.transmit([0x80, 0xCA, p1, p2])
        assert (sw1, sw2) == (0x90, 0x00)
        blob += response
    return hashlib.sha1(bytes(blob)).hexdigest()


print("three distinct cards")
check("three cards", len(simulated_card.CARDS) == 3)
check("labelled A B C", [c.label for c in simulated_card.CARDS] == ["Card A", "Card B", "Card C"],
      str([c.label for c in simulated_card.CARDS]))
uids = [uid_of(c) for c in simulated_card.CARDS]
check("distinct UIDs", len(set(uids)) == 3, " ".join(u[:12] for u in uids))
check("independent state objects",
      len({id(c.remaining_tries) for c in simulated_card.CARDS}) == 3)

print("empty reader")
tray = FakeTray()
simulated_card.install(tray)
check("reader starts empty", simulated_card.current_card() is None)
check("card_service() is None", simulated_card.card_service() is None)
try:
    CardRequest(timeout=0).waitforcard()
    check("waitforcard raises on empty reader", False)
except CardRequestTimeoutException:
    check("waitforcard raises on empty reader", True)

start = time.monotonic()
try:
    CardRequest(timeout=0.4).waitforcard()
    check("a timed wait gives up", False)
except CardRequestTimeoutException:
    waited = time.monotonic() - start
    check("a timed wait gives up", 0.3 < waited < 2.0, f"{waited:.2f}s")

try:
    simulated_card.SimulatedReader().createConnection().connect()
    check("connecting to an empty reader raises", False)
except NoCardException:
    check("connecting to an empty reader raises", True)

print("insert / eject through the tray")
events = []


class Watcher(CardObserver):
    def update(self, observable, actions):
        # pyscard notifies a new observer even when the readers are empty, so
        # only the notifications that name a card are events.
        added, removed = actions
        for service in added:
            events.append("+" + service.card.label)
        for service in removed:
            events.append("-" + service.card.label)


monitor = CardMonitor()
watcher = Watcher()
monitor.addObserver(watcher)
check("empty reader announces nothing", events == [], str(events))

tray.slot = 0
service = CardRequest(timeout=0).waitforcard()
uid_a = read_uid(service)
check("Card A is what waitforcard hands over", service.card.label == "Card A")
check("Card A's UID matches its identity", uid_a == uids[0], uid_a[:12])
check("insertion reached the monitor", events == ["+Card A"], str(events))

print("state that survives a trip out of the reader")
simulated_card.CARDS[0].setup_done = True
simulated_card.CARDS[0].remaining_tries[0] = 3
before = read_status(service)

tray.slot = simulated_card.EMPTY
simulated_card.poll()
check("removal reached the monitor", events[-1] == "-Card A", str(events))
try:
    service.createConnection().transmit([0xB0, 0x3C, 0x00, 0x00])
    check("a removed card cannot answer", False)
except CardConnectionException:
    check("a removed card cannot answer", True)

tray.slot = 1
service_b = CardRequest(timeout=0).waitforcard()
uid_b = read_uid(service_b)
check("Card B has a different UID", uid_b != uid_a, f"{uid_a[:12]} vs {uid_b[:12]}")
check("Card B is untouched by Card A's state",
      read_status(service_b) == {"pin_tries": 5, "is_seeded": False, "setup_done": False},
      str(read_status(service_b)))

tray.slot = 0
service_a = CardRequest(timeout=0).waitforcard()
check("Card A came back as it was left", read_status(service_a) == before, str(before))
check("and it is still Card A", read_uid(service_a) == uid_a)

print("what the tray gets told")
check("state published for every card", sorted(tray.published) == [0, 1, 2], str(tray.published))
# bit0 setup_done, bit1 is_seeded, bits 8-15 PIN tries -- see describe() in wallet-cards.js
check("Card A published as initialised with 3 tries", tray.published[0] == 0x01 | (3 << 8),
      hex(tray.published[0]))
check("Card B published as blank with 5 tries", tray.published[1] == 0x00 | (5 << 8),
      hex(tray.published[1]))

# --------------------------------------------------------------- a seed on a card

# The BIP39 test vector the rest of this suite scans, derived here rather than
# pasted so that what goes on the card is visibly the published vector and not a
# hex string somebody typed. Nothing about it is secret and nothing should ever
# hold value.
MNEMONIC = ("army van defense carry jealous true "
            "garbage claim echo media make crunch")
SEED = hashlib.pbkdf2_hmac("sha512", MNEMONIC.encode(), b"mnemonic", 2048, 64)
FINGERPRINT = "b2269592"

PIN = list(b"123456")

# The layout card_setup() packs, see SimulatedCard._setup. Everything but PIN0
# and the four try counts is either RFU on a Satochip or never asked for again.
SETUP = ([0x00] + [0x05, 0x01, len(PIN)] + PIN + [16] + 16 * [0x00]
         + [0x01, 0x01, 16] + 16 * [0x00] + [16] + 16 * [0x00])


def send(ins, p1, p2, data=()):
    """One card-edge APDU, framed the way every CardConnector method frames one:
    a length byte even when there is nothing behind it."""
    response, sw1, sw2 = connection.transmit([0xB0, ins, p1, p2, len(data)] + list(data))
    return response, (sw1, sw2)


def path_bytes(*indices):
    """A BIP32 path as CardDataParser.bip32path2bytes() would encode it."""
    return b"".join(index.to_bytes(4, "big") for index in indices)


print("a seed on a card")
tray.slot = 2
service_c = CardRequest(timeout=0).waitforcard()
connection = service_c.createConnection()
connection.connect()

check("setup takes the PIN", send(0x2A, 0x00, 0x00, SETUP)[1] == (0x90, 0x00))
check("an unverified PIN refuses seed import", send(0x6C, 64, 0x00, SEED)[1] == (0x9C, 0x06),
      "0x9C06 is card_transmit()'s cue to verify the cached PIN and try again")
check("an unverified PIN refuses key derivation",
      send(0x6D, 0x00, 0x40)[1] == (0x9C, 0x06))
check("the PIN verifies", send(0x42, 0x00, 0x00, PIN)[1] == (0x90, 0x00))

check("an unseeded card has no authentikey", send(0x73, 0x00, 0x00)[1] == (0x9C, 0x14))
check("and derives nothing", send(0x6D, 0x00, 0x40)[1] == (0x9C, 0x14))
check("a seed too short to be one is refused",
      send(0x6C, 8, 0x00, 8 * b"\x00")[1] == (0x9C, 0x0F))

response, sw = send(0x6C, len(SEED), 0x00, SEED)
check("the seed imports", sw == (0x90, 0x00), str(sw))
# The import answer is an authentikey answer, and this is the parser the wallet
# will run over it. It recovers the pubkey from the signature and raises unless
# it is the one the message claims, so reaching an ECPubkey at all is the check.
parser = CardDataParser()
authentikey = parser.parse_bip32_get_authentikey(response)
check("pysatochip recovers an authentikey out of it", authentikey is not None,
      authentikey.get_public_key_bytes(True).hex())
check("a second seed is refused", send(0x6C, len(SEED), 0x00, SEED)[1] == (0x9C, 0x17))
check("the card now reports itself seeded", read_status(service_c)["is_seeded"])

check("GET AUTHENTIKEY answers with the same key",
      CardDataParser().parse_bip32_get_authentikey(send(0x73, 0x00, 0x00)[0]) == authentikey)

master, sw = send(0x6D, 0x00, 0x40)
check("the master key derives", sw == (0x90, 0x00), str(sw))
pubkey, chaincode = parser.parse_bip32_get_extendedkey(master)
check("its fingerprint is the test vector's",
      hash160(pubkey.get_public_key_bytes(True))[:4].hex() == FINGERPRINT, FINGERPRINT)

path = path_bytes(84 + 0x80000000, 0x80000000, 0x80000000)
account, sw = send(0x6D, len(path) // 4, 0x40, path)
pubkey, chaincode = parser.parse_bip32_get_extendedkey(account)
expected = bip32.HDKey.from_seed(SEED).derive("m/84h/0h/0h")
check("m/84'/0'/0' matches the same seed derived outside the card",
      (pubkey.get_public_key_bytes(True), bytes(chaincode))
      == (expected.sec(), expected.chain_code))

check("the private key is not for export", send(0x6D, 0x00, 0x42)[1] == (0x6D, 0x00))
check("nor is BIP85 entropy", send(0x6D, 0x00, 0x44)[1] == (0x6D, 0x00))
check("an instruction nobody implemented still says so",
      send(0x6E, 0x00, 0x00)[1] == (0x6D, 0x00))

check("Card C published as seeded", tray.published[2] & 0x02 == 0x02, hex(tray.published[2]))
check("Card B is still blank", simulated_card.CARDS[1].is_seeded is False)

check("the wrong PIN does not erase the seed",
      send(0x77, 6, 0x00, b"999999")[1] == (0x9C, 0x02) and simulated_card.CARDS[2].is_seeded)
check("the right one does", send(0x77, len(PIN), 0x00, PIN)[1] == (0x90, 0x00))
check("and the card is unseeded again", read_status(service_c)["is_seeded"] is False)
check("with no authentikey left", send(0x73, 0x00, 0x00)[1] == (0x9C, 0x14))

# A JavaCard applet loses its PIN state when it is deselected, and every flow
# here selects the applet on insertion, so this is what makes the gate above real
# rather than something one VERIFY PIN opens for the life of the page.
connection.transmit([0x00, 0xA4, 0x04, 0x00, len(simulated_card.SATOCHIP_AID)]
                    + simulated_card.SATOCHIP_AID)
check("selecting the applet drops the verified PIN",
      send(0x6C, len(SEED), 0x00, SEED)[1] == (0x9C, 0x06))

check("the card asks for no secure channel",
      connection.transmit([0xB0, 0x3C, 0x00, 0x00])[0][11] == 0x00,
      "pysatochip only wraps APDUs when byte 11 of GET STATUS is set")

sys.exit(report())
