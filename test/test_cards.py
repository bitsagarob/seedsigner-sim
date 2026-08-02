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

from harness import check, report

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

sys.exit(report())
