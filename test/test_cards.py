"""
Drive the simulated card registry the way pysatochip does, without a browser.

This is the fast half of the smartcard story: no Pyodide, no page, just the
pyscard stand-in in src/smartcard being asked the same questions the wallet asks
it. If this fails, test_cards_browser.py will fail too, and this one says why in
two seconds rather than two minutes.

Both card types are here. A Satochip takes a seed and derives from it; a
SeedKeeper takes labelled secrets and hands them back under whatever export
rights they were stored with. The two answer different AIDs and different
instruction sets, and the checks below are as interested in what each one
refuses as in what it answers.
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
# is pysatochip's own parser. Both ship inside the smartcard wallet zip rather
# than being installed anywhere, and zipimport reads a pure-Python package
# straight out of one -- so this is the same copy the browser runs, not a
# lookalike from PyPI. The smartcard zip specifically: pysatochip is part of the
# card stack, which is the half of the fork stock does not have.
# Last on the path, not first: the zip carries a copy of the stand-in too, and
# the one under test is the checkout's.
WALLET_ZIP = harness.find_asset("wallet-smartcard.zip")
if WALLET_ZIP is None:
    sys.exit("no wallet-smartcard.zip: run build/build-wallet-zip.sh smartcard first")
sys.path.append(WALLET_ZIP)

from embit import bip32, bip39
from embit.hashes import hash160
from pysatochip.CardDataParser import CardDataParser
from pysatochip.JCconstants import (
    BIP39_WORDLIST_DIC,
    SEEDKEEPER_DIC_EXPORT_RIGHTS,
    SEEDKEEPER_DIC_ORIGIN,
    SEEDKEEPER_DIC_TYPE,
)

from smartcard import simulated_card
from smartcard.CardMonitoring import CardMonitor, CardObserver
from smartcard.CardRequest import CardRequest
from smartcard.Exceptions import (
    CardConnectionException,
    CardRequestTimeoutException,
    NoCardException,
)

SEEDKEEPER = simulated_card.KIND_SEEDKEEPER
SATOCHIP = simulated_card.KIND_SATOCHIP


class FakeTray:
    """Stands in for the page's half of the card tray, see wallet-cards.js."""

    def __init__(self):
        self.slot = simulated_card.EMPTY
        # SeedKeeper everywhere, which is what the page defaults to.
        self.kinds = [SEEDKEEPER] * simulated_card.CARD_COUNT
        self.published = {}

    def inserted(self):
        return self.slot

    def kind(self, index):
        return self.kinds[index]

    def wait(self, timeout_ms):
        return self.slot

    def publish(self, index, kind, state):
        self.published[(index, kind)] = state


def uid_of(card):
    return hashlib.sha1(bytes(card.uid)).hexdigest()


def select(service):
    """Pick the card's applet, which is the first thing RemovalObserver does when
    a card arrives. Card-edge instructions are addressed to an applet, so nothing
    below answers until this has happened."""
    connection = service.createConnection()
    connection.connect()
    aid = service.card.AID
    response, sw1, sw2 = connection.transmit([0x00, 0xA4, 0x04, 0x00, len(aid)] + aid)
    assert (sw1, sw2) == (0x90, 0x00), (sw1, sw2)
    return connection


def read_status(service):
    """What CardConnector.card_get_status() sends and unpacks. The applet has to
    have been selected first, which is why every insertion below selects."""
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


print("six cards in three slots")
check("three slots", len(simulated_card.CARDS) == 3)
check("a SeedKeeper and a Satochip in each",
      [[card.APPLET for card in slot] for slot in simulated_card.CARDS]
      == [["SeedKeeper", "Satochip"]] * 3)
check("labelled A B C",
      [slot[SEEDKEEPER].label for slot in simulated_card.CARDS] == ["Card A", "Card B", "Card C"])
cards = [card for slot in simulated_card.CARDS for card in slot]
uids = [uid_of(card) for card in cards]
check("six distinct UIDs", len(set(uids)) == 6, " ".join(u[:8] for u in uids))
check("independent state objects",
      len({id(card.remaining_tries) for card in cards}) == 6)

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
select(service)
uid_a = read_uid(service)
check("Card A is what waitforcard hands over", service.card.label == "Card A")
check("and it is a SeedKeeper, which is the default",
      service.card.APPLET == "SeedKeeper", service.card.APPLET)
check("Card A's UID matches its identity", uid_a == uid_of(simulated_card.CARDS[0][SEEDKEEPER]),
      uid_a[:12])
check("insertion reached the monitor", events == ["+Card A"], str(events))

print("state that survives a trip out of the reader")
simulated_card.CARDS[0][SEEDKEEPER].setup_done = True
simulated_card.CARDS[0][SEEDKEEPER].remaining_tries[0] = 3
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
select(service_b)
uid_b = read_uid(service_b)
check("Card B has a different UID", uid_b != uid_a, f"{uid_a[:12]} vs {uid_b[:12]}")
check("Card B is untouched by Card A's state",
      read_status(service_b) == {"pin_tries": 5, "is_seeded": False, "setup_done": False},
      str(read_status(service_b)))

tray.slot = 0
service_a = CardRequest(timeout=0).waitforcard()
select(service_a)
check("Card A came back as it was left", read_status(service_a) == before, str(before))
check("and it is still Card A", read_uid(service_a) == uid_a)

print("the card type is a property of the card, not a setting on it")
tray.kinds[0] = SATOCHIP
service_a_sat = CardRequest(timeout=0).waitforcard()
select(service_a_sat)
check("switching type hands over the other applet",
      service_a_sat.card.APPLET == "Satochip", service_a_sat.card.APPLET)
check("which is a different card, with its own UID",
      read_uid(service_a_sat) != uid_a, read_uid(service_a_sat)[:12])
check("factory fresh, because it is not the card that was set up",
      read_status(service_a_sat) == {"pin_tries": 5, "is_seeded": False, "setup_done": False},
      str(read_status(service_a_sat)))
tray.kinds[0] = SEEDKEEPER
service_a_again = CardRequest(timeout=0).waitforcard()
select(service_a_again)
check("and switching back finds the first card as it was left",
      read_status(service_a_again) == before)

print("what the tray gets told")
check("state published for every card of every slot",
      sorted(tray.published) == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)],
      str(sorted(tray.published)))
# bit0 setup_done, bit1 is_seeded, bits 8-15 PIN tries -- see describe() in wallet-cards.js
check("Card A's SeedKeeper published as initialised with 3 tries",
      tray.published[(0, SEEDKEEPER)] == 0x01 | (3 << 8), hex(tray.published[(0, SEEDKEEPER)]))
check("Card A's Satochip published as blank with 5 tries",
      tray.published[(0, SATOCHIP)] == 0x00 | (5 << 8), hex(tray.published[(0, SATOCHIP)]))

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
# and the four try counts is either RFU or never asked for again.
SETUP = ([0x00] + [0x05, 0x01, len(PIN)] + PIN + [16] + 16 * [0x00]
         + [0x01, 0x01, 16] + 16 * [0x00] + [16] + 16 * [0x00])


def send(ins, p1, p2, data=()):
    """One card-edge APDU, framed the way the CardConnector method that sends it
    frames one: a length byte even when there is nothing behind it, or -- with
    data=None -- no length byte at all, which is how the SeedKeeper's status and
    listing instructions go out."""
    body = [] if data is None else [len(data)] + list(data)
    response, sw1, sw2 = connection.transmit([0xB0, ins, p1, p2] + body)
    return response, (sw1, sw2)


def path_bytes(*indices):
    """A BIP32 path as CardDataParser.bip32path2bytes() would encode it."""
    return b"".join(index.to_bytes(4, "big") for index in indices)


print("a seed on a Satochip")
tray.slot = 2
tray.kinds[2] = SATOCHIP
service_c = CardRequest(timeout=0).waitforcard()
connection = select(service_c)
check("a card answers nothing until its applet is selected",
      connection.transmit([0xB0, 0x3C, 0x00, 0x00, 0x00])[1:] == (0x90, 0x00)
      and (connection.transmit([0x00, 0xA4, 0x04, 0x00, len(simulated_card.SEEDKEEPER_AID)]
                               + simulated_card.SEEDKEEPER_AID)[1:] == (0x6A, 0x82))
      and connection.transmit([0xB0, 0x3C, 0x00, 0x00, 0x00])[1:] == (0x6D, 0x00),
      "a failed SELECT leaves the card manager on the other end, and it has "
      "never heard of a Satochip")
select(service_c)

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
check("nor does a Satochip answer a SeedKeeper instruction",
      send(0xA6, 0x00, 0x01, None)[1] == (0x6D, 0x00),
      "listing secrets on a card that has none is not an empty list, it is a "
      "card that cannot be asked")

check("Card C's Satochip published as seeded",
      tray.published[(2, SATOCHIP)] & 0x02 == 0x02, hex(tray.published[(2, SATOCHIP)]))
check("Card B's Satochip is still blank",
      simulated_card.CARDS[1][SATOCHIP].is_seeded is False)

check("the wrong PIN does not erase the seed",
      send(0x77, 6, 0x00, b"999999")[1] == (0x9C, 0x02)
      and simulated_card.CARDS[2][SATOCHIP].is_seeded)
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

# --------------------------------------------------- a secret on a SeedKeeper

# What SaveToSeedkeeperView writes for a BIP39 seed on a SeedKeeper v2, built
# here the same way it builds it: the master seed, the wordlist the entropy is
# in, that entropy, and the passphrase. Laid out here rather than imported
# because CardConnector cannot be imported outside the browser -- it reaches
# OpenSSL through the `cryptography` wheel, which only exists in Pyodide here.
# The entropy is recovered from the vector rather than pasted, by embit out of
# wallet.zip; the wallet uses the `mnemonic` package for the same job, which
# cannot be zipimported because it reads its wordlist as a file.
ENTROPY = list(bip39.mnemonic_to_bytes(MNEMONIC))
ENGLISH = [code for code, name in BIP39_WORDLIST_DIC.items() if name == "english"][0]
MASTERSEED_SECRET = ([len(SEED)] + list(SEED) + [ENGLISH]
                     + [len(ENTROPY)] + ENTROPY + [0])

TYPE_MASTERSEED = [code for code, name in SEEDKEEPER_DIC_TYPE.items()
                   if name == "Masterseed"][0]
TYPE_PASSWORD = [code for code, name in SEEDKEEPER_DIC_TYPE.items()
                 if name == "Password"][0]
PLAINTEXT_EXPORT = [code for code, name in SEEDKEEPER_DIC_EXPORT_RIGHTS.items()
                    if name == "Plaintext export allowed"][0]
EXPORT_FORBIDDEN = [code for code, name in SEEDKEEPER_DIC_EXPORT_RIGHTS.items()
                    if name == "Export forbidden"][0]


def header_fields(secret_type, export_rights, label, subtype=0x00):
    """What CardConnector.make_header() builds, minus the two bytes of id that
    seedkeeper_import_secret() strips before sending it: the id is the card's to
    assign, and so are the counters and the fingerprint this leaves at zero."""
    label_list = list(label.encode("utf-8"))
    return ([secret_type, 0x00, export_rights] + 3 * [0x00] + 4 * [0x00]
            + [subtype, 0x00, len(label_list)] + label_list)


def import_secret(fields, payload, chunk=128):
    """seedkeeper_import_secret()'s three steps: header, bytes, commit."""
    padded = len(payload) + 16 - len(payload) % 16
    response, sw = send(0xA1, 0x01, 0x01, fields + [padded >> 8, padded & 0xFF])
    if sw != (0x90, 0x00):
        return response, sw
    offset = 0
    while len(payload) - offset > chunk:
        piece = payload[offset:offset + chunk]
        response, sw = send(0xA1, 0x01, 0x02, [chunk >> 8, chunk & 0xFF] + piece)
        if sw != (0x90, 0x00):
            return response, sw
        offset += chunk
    left = payload[offset:]
    return send(0xA1, 0x01, 0x03, [len(left) >> 8, len(left) & 0xFF] + left)


def export_secret(sid):
    """seedkeeper_export_secret()'s loop, including its own end condition: the
    last chunk is the one that arrives with a signature behind it."""
    sid_bytes = [sid >> 8, sid & 0xFF]
    header, sw = send(0xA2, 0x01, 0x01, sid_bytes)
    if sw != (0x90, 0x00):
        return None, None, sw
    secret, signature = [], None
    while True:
        response, sw = send(0xA2, 0x01, 0x02, sid_bytes)
        assert sw == (0x90, 0x00), sw
        size = (response[0] << 8) + response[1]
        secret += response[2:2 + size]
        if size + 2 < len(response):
            signature = response[size + 4:]
            break
    return header, (secret, signature), sw


def free_memory():
    response, sw = send(0xA7, 0x00, 0x00, None)
    assert sw == (0x90, 0x00), sw
    return (response[4] << 8) + response[5]


print("a secret on a SeedKeeper")
tray.slot = 1
tray.kinds[1] = SEEDKEEPER
service_sk = CardRequest(timeout=0).waitforcard()
check("the reader is holding a SeedKeeper", service_sk.card.APPLET == "SeedKeeper")

connection = service_sk.createConnection()
connection.connect()
check("a SeedKeeper answers the SeedKeeper AID",
      connection.transmit([0x00, 0xA4, 0x04, 0x00, len(simulated_card.SEEDKEEPER_AID)]
                          + simulated_card.SEEDKEEPER_AID)[1:] == (0x90, 0x00))
check("and not the Satochip one",
      connection.transmit([0x00, 0xA4, 0x04, 0x00, len(simulated_card.SATOCHIP_AID)]
                          + simulated_card.SATOCHIP_AID)[1:] == (0x6A, 0x82),
      "which is what makes card_select() move on to the next applet")
connection = select(service_sk)
check("nor does it derive BIP32 keys", send(0x6D, 0x00, 0x40)[1] == (0x6D, 0x00))

check("an uninitialised SeedKeeper cannot be asked for its free space",
      send(0xA7, 0x00, 0x00, None)[1] == (0x9C, 0x04))
check("setup takes the PIN", send(0x2A, 0x00, 0x00, SETUP)[1] == (0x90, 0x00))
check("an unverified PIN refuses the status", send(0xA7, 0x00, 0x00, None)[1] == (0x9C, 0x06))
check("an unverified PIN refuses the listing",
      send(0xA6, 0x00, 0x01, None)[1] == (0x9C, 0x06))
check("an unverified PIN refuses an import",
      send(0xA1, 0x01, 0x01, header_fields(TYPE_MASTERSEED, PLAINTEXT_EXPORT, "x")
           + [0x00, 0x10])[1] == (0x9C, 0x06))
check("the PIN verifies", send(0x42, 0x00, 0x00, PIN)[1] == (0x90, 0x00))

check("a fresh SeedKeeper lists nothing", send(0xA6, 0x00, 0x01, None)[1] == (0x9C, 0x12),
      "0x9C12 is the end of the sequence, which for an empty card is the start")
check("and exporting a secret it does not have says so",
      send(0xA2, 0x01, 0x01, [0x00, 0x01])[1] == (0x9C, 0x08))
empty_space = free_memory()
check("it reports all of its memory free", empty_space == 32000, str(empty_space))

fields = header_fields(TYPE_MASTERSEED, PLAINTEXT_EXPORT, FINGERPRINT, subtype=0x01)
response, sw = import_secret(fields, MASTERSEED_SECRET)
check("the master seed imports", sw == (0x90, 0x00), str(sw))
sid = (response[0] << 8) + response[1]
check("the card files it under an id", sid == 1, str(sid))
check("and answers with its own hash of what it stored",
      bytes(response[2:6]).hex() == hashlib.sha256(bytes(MASTERSEED_SECRET)).hexdigest()[:8],
      bytes(response[2:6]).hex())
check("the card now reports itself carrying something", read_status(service_sk)["is_seeded"])
check("space went down by the header plus the padded secret",
      empty_space - free_memory() == 15 + len(FINGERPRINT) + 96,
      f"{empty_space - free_memory()} bytes for {len(MASTERSEED_SECRET)} of secret")

# The listing is the wallet's index into the card, and this is the parser it
# reads it with -- pysatochip's own, out of wallet.zip.
listed, sw = send(0xA6, 0x00, 0x01, None)
check("the secret is listed", sw == (0x90, 0x00), str(sw))
header = CardDataParser().parse_seedkeeper_header(listed)
check("pysatochip parses the header the card wrote",
      (header["id"], header["label"], header["subtype"]) == (sid, FINGERPRINT, 0x01),
      str((header["id"], header["label"], header["subtype"])))
check("as a Masterseed", SEEDKEEPER_DIC_TYPE.get(header["type"]) == "Masterseed",
      hex(header["type"]))
check("that may be exported in the clear",
      SEEDKEEPER_DIC_EXPORT_RIGHTS.get(header["export_rights"]) == "Plaintext export allowed")
check("imported in plaintext, which is the card's word and not the client's",
      SEEDKEEPER_DIC_ORIGIN.get(header["origin"]) == "Plaintext import", hex(header["origin"]))
check("with a fingerprint of the secret, not the zeros the client sent",
      header["fingerprint"] == hashlib.sha256(bytes(MASTERSEED_SECRET)).hexdigest()[:8],
      header["fingerprint"])
check("and that is the whole list", send(0xA6, 0x00, 0x02, None)[1] == (0x9C, 0x12))

header_bytes, (secret, signature), sw = export_secret(sid)
check("the secret exports", sw == (0x90, 0x00), str(sw))
check("byte for byte what went in", secret == MASTERSEED_SECRET,
      f"{len(secret)} bytes back for {len(MASTERSEED_SECRET)} in")
# The signature is over the header as well as the secret, so a card cannot hand
# back the right bytes under someone else's label. verify_signature() recovers a
# key from it and raises unless that key is the one it was given.
authentikey = CardDataParser().parse_bip32_get_authentikey(send(0x73, 0x00, 0x00)[0])
CardDataParser().verify_signature(header_bytes + secret, signature, authentikey)
check("signed by the card's authentikey, over the header and the secret together", True)
try:
    CardDataParser().verify_signature(header_bytes + [secret[0] ^ 0xFF] + secret[1:],
                                      signature, authentikey)
    check("and that signature does not cover a secret it did not sign", False)
except ValueError:
    check("and that signature does not cover a secret it did not sign", True)

exported = CardDataParser().parse_seedkeeper_header(header_bytes)
check("the export counter went up", exported["export_nbplain"] == 1,
      str(exported["export_nbplain"]))

# A long one, to make the multi-APDU paths real: import sends 128 bytes at a
# time and export answers 128 at a time, and neither loop runs at all for a
# secret that fits in one.
LONG = list(range(256))
response, sw = import_secret(header_fields(TYPE_PASSWORD, PLAINTEXT_EXPORT, "long"), LONG)
check("a secret longer than one APDU imports", sw == (0x90, 0x00), str(sw))
long_sid = (response[0] << 8) + response[1]
_, (long_secret, _), sw = export_secret(long_sid)
check("and comes back in one piece", long_secret == LONG,
      f"{len(long_secret)} bytes of {len(LONG)}")

# Export rights are the point of a SeedKeeper: what a secret may be used for is
# stored with it, and the card is the thing that enforces it.
response, sw = import_secret(header_fields(TYPE_PASSWORD, EXPORT_FORBIDDEN, "sealed"),
                             list(b"never leaves"))
check("a secret can be stored with export forbidden", sw == (0x90, 0x00), str(sw))
sealed_sid = (response[0] << 8) + response[1]
check("and the card refuses to hand it over in the clear",
      send(0xA2, 0x01, 0x01, [sealed_sid >> 8, sealed_sid & 0xFF])[1] == (0x9C, 0x31),
      "0x9C31 is what pysatochip reports as 'export not allowed by SeedKeeper policy'")
check("while the one that allows it still exports",
      export_secret(sid)[2] == (0x90, 0x00))
check("an encrypted export is not implemented rather than quietly plaintext",
      send(0xA2, 0x02, 0x01, [sid >> 8, sid & 0xFF])[1] == (0x6D, 0x00))

check("deleting a secret the card does not have says so",
      send(0xA5, 0x00, 0x00, [0xFF, 0xFF])[1] == (0x9C, 0x08))
before_delete = free_memory()
check("deleting one it does have works",
      send(0xA5, 0x00, 0x00, [long_sid >> 8, long_sid & 0xFF])[1] == (0x90, 0x00))
check("and gives the space back", free_memory() > before_delete,
      f"{before_delete} -> {free_memory()}")
check("the deleted secret is gone",
      send(0xA2, 0x01, 0x01, [long_sid >> 8, long_sid & 0xFF])[1] == (0x9C, 0x08))

select(service_sk)
check("selecting the applet drops the verified PIN here too",
      send(0xA6, 0x00, 0x01, None)[1] == (0x9C, 0x06))

check("Card B's SeedKeeper published as carrying a secret",
      tray.published[(1, SEEDKEEPER)] & 0x02 == 0x02, hex(tray.published[(1, SEEDKEEPER)]))
check("Card C's SeedKeeper is untouched by any of it",
      simulated_card.CARDS[2][SEEDKEEPER].secrets == {}
      and simulated_card.CARDS[2][SEEDKEEPER].setup_done is False)

sys.exit(report())
