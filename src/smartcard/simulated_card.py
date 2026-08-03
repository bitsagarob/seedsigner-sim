"""
Three simulated Satochips, and the one reader they go in and out of.

Browsers have no smartcard API at all, so the only honest place to fake a card is
the transport. pysatochip reaches a physical card through pyscard, and this module
replaces that layer with cards implemented in Python. Everything above it, the
whole of pysatochip and the whole of SeedSigner, runs unmodified against them,
which is the point: the demo exercises the real flows rather than a mock of them.

There are three cards because a user needs to be able to tell one from another --
put a seed on Card A, check Card B is still blank, come back to Card A and find it
as it was left. They are identical except for their CIN, which is enough: pysatochip
hashes CPLC+IIN+CIN into the UID it uses to distinguish cards, so different CINs
make these genuinely different cards rather than three names for one.

Each card starts blank, with no seed and setup not done, so the wallet drives its
own initialisation flow instead of a shortcut.

Cards live in CARDS, a module-level registry, so their state survives connect and
disconnect the way a card taken out of a reader and put back would.

Which card is in the reader comes from the page, through the SharedArrayBuffer set
up by install(). Reading it is what makes an empty reader an empty reader: with
nothing inserted, waiting for a card times out and transmitting raises, exactly as
they do when there is no card in a real one.
"""

import hashlib
import hmac
import time

from embit import bip32, ec

from smartcard.Exceptions import CardConnectionException, NoCardException

# CLAs and INSs, from pysatochip's CardConnector and JCconstants.
CLA_ISO = 0x00
CLA_GP = 0x80
CLA_CARDEDGE = 0xB0

INS_SELECT = 0xA4
INS_GET_DATA = 0xCA
INS_GET_STATUS = 0x3C
INS_SETUP = 0x2A
INS_VERIFY_PIN = 0x42
INS_BIP32_IMPORT_SEED = 0x6C
INS_BIP32_GET_EXTENDED_KEY = 0x6D
INS_BIP32_GET_AUTHENTIKEY = 0x73
INS_BIP32_RESET_SEED = 0x77

SW_OK = (0x90, 0x00)
SW_FILE_NOT_FOUND = (0x6A, 0x82)
SW_INS_NOT_SUPPORTED = (0x6D, 0x00)
SW_AUTH_FAILED = (0x9C, 0x02)
SW_OPERATION_NOT_ALLOWED = (0x9C, 0x03)
SW_SETUP_NOT_DONE = (0x9C, 0x04)
SW_UNAUTHORIZED = (0x9C, 0x06)
SW_IDENTITY_BLOCKED = (0x9C, 0x0C)
SW_INVALID_PARAMETER = (0x9C, 0x0F)
SW_INCORRECT_P1 = (0x9C, 0x10)
SW_BIP32_UNINITIALIZED_SEED = (0x9C, 0x14)
SW_BIP32_ALREADY_SEEDED = (0x9C, 0x17)

# What a Satochip will hold a seed of, in bytes. A BIP39 seed is 64.
SEED_SIZES = range(16, 65)

SATOCHIP_AID = [0x53, 0x61, 0x74, 0x6F, 0x43, 0x68, 0x69, 0x70]

# A JavaCard-shaped ATR. Nothing inspects its contents: pysatochip logs it and
# compares it against the Windows Hello virtual device it skips, so the only real
# requirement is that it is stable and is not that one. All three cards share it,
# as three cards of one model would.
SATOCHIP_ATR = [
    0x3B, 0xF9, 0x18, 0x00, 0xFF, 0x81, 0x31, 0xFE, 0x45,
    0x4A, 0x43, 0x4F, 0x50, 0x76, 0x32, 0x34, 0x31, 0xB7,
]

# GlobalPlatform GET DATA answers. pysatochip concatenates these three and hashes
# them into a card UID, so they need to be stable and, between cards, distinct.
# Only the CIN differs, because only one of them has to.
CPLC = [0x9F, 0x7F, 0x2A] + [0x42] * 42
IIN = [0x42, 0x49, 0x54, 0x53, 0x41, 0x47, 0x41]
CIN_PREFIX = [0x53, 0x49, 0x4D, 0x30, 0x30]  # "SIM00", then the card's own digit

CARD_COUNT = 3

# No card in the reader. Same sentinel the tray writes, see wallet-cards.js.
EMPTY = -1

# How long a wait for a card parks before looking again. The page is what puts a
# card in the reader and it only gets to do that while this thread is parked, so
# the slice is short enough to keep the wait responsive and long enough not to
# spin. Matched to how browser_camera parks on a frame, for the same reason.
_WAIT_SLICE_MS = 250


def label_for(index):
    """Card A, Card B, Card C. The same rule the tray uses, see wallet-cards.js."""
    return "Card " + chr(ord("A") + index)


def _blob(data, offset):
    """A length-prefixed field, and where the one after it starts."""
    length = data[offset]
    return data[offset + 1:offset + 1 + length], offset + 1 + length


def _size(length):
    """The two-byte big-endian prefix every BIP32 answer measures a field with."""
    return [length >> 8, length & 0xFF]


def _signed(key, message):
    """A message with a signature over its SHA-256 appended, length-prefixed.

    Every BIP32 answer a Satochip gives is built out of these. The client does
    not verify the signature against a key it was told; it *recovers* the key
    from the signature and keeps the answer only if what comes back matches
    what the message claims, so the signature is both the proof and the half of
    the public key an x coordinate on its own leaves out.

    Signing without grinding because grinding exists to keep transaction
    signatures short, which matters nowhere here, and pure-Python secp256k1
    under WebAssembly is slow enough without signing some of them twice.
    """
    signature = list(key.sign(hashlib.sha256(bytes(message)).digest(),
                              grind=False).serialize())
    return list(message) + _size(len(signature)) + signature


class SimulatedCard:
    """One card: its identity, its state, and one handler per APDU it understands."""

    def __init__(self, index):
        self.index = index
        self.label = label_for(index)
        # The one byte that makes this card not the others.
        self.cin = CIN_PREFIX + [0x31 + index]

        self.protocol_version = (0, 12)
        self.applet_version = (0, 12)
        # PIN0, PUK0, PIN1, PUK1
        self.remaining_tries = [5, 5, 5, 5]
        self.needs_2fa = False
        self.setup_done = False
        # Both set by setup, which is the only thing that can set them: a card
        # with no PIN on it is a card no PIN can be verified against.
        self.pin0 = None
        self.pin0_tries = 0
        # Cleared by SELECT, because a JavaCard applet loses its PIN state when
        # it is deselected, and set by VERIFY PIN. Every BIP32 instruction below
        # is gated on it.
        self.pin_verified = False
        # The two keys a seed puts on the card, and the only place the seed
        # survives at all. Both live here and nowhere else -- no filesystem, no
        # storage in the page -- so reloading is a factory-fresh card.
        self.master_key = None
        self.authentikey = None
        # A secure channel would encrypt every APDU with a session key negotiated
        # over ECDH. It protects the wire between reader and card, and here there
        # is no wire, so the card reports that it does not need one.
        self.needs_secure_channel = False

    @property
    def is_seeded(self):
        return self.master_key is not None

    @property
    def uid(self):
        return CPLC + IIN + self.cin

    @property
    def uid_sha1(self):
        """The identity pysatochip settles on, derived the same way it derives it.

        Duplicated from RemovalObserver so the log line below names a card the
        way the wallet will, which is what makes the two comparable when a flow
        picks up the wrong card.
        """
        return hashlib.sha1(bytes(self.uid)).hexdigest()

    def transmit(self, apdu):
        """Answer one APDU, returning pyscard's (response, sw1, sw2)."""
        if len(apdu) < 4:
            raise CardConnectionException(f"malformed APDU: {apdu}")

        cla, ins, p1, p2 = apdu[0], apdu[1], apdu[2], apdu[3]
        data = apdu[5:5 + apdu[4]] if len(apdu) > 5 else []

        if cla == CLA_ISO and ins == INS_SELECT:
            return self._select(data)
        if cla == CLA_GP and ins == INS_GET_DATA:
            return self._get_data(p1, p2)
        if cla == CLA_CARDEDGE and ins == INS_GET_STATUS:
            return self._get_status()
        if cla == CLA_CARDEDGE and ins == INS_SETUP:
            return self._setup(data)
        if cla == CLA_CARDEDGE and ins == INS_VERIFY_PIN:
            return self._verify_pin(p1, data)
        if cla == CLA_CARDEDGE and ins == INS_BIP32_IMPORT_SEED:
            return self._import_seed(data)
        if cla == CLA_CARDEDGE and ins == INS_BIP32_RESET_SEED:
            return self._reset_seed(data[:p1])
        if cla == CLA_CARDEDGE and ins == INS_BIP32_GET_AUTHENTIKEY:
            return self._get_authentikey()
        if cla == CLA_CARDEDGE and ins == INS_BIP32_GET_EXTENDED_KEY:
            return self._get_extended_key(p1, p2, data)

        # Unknown instruction. card_transmit() returns any status it does not
        # recognise straight to the caller, so this ends the exchange rather than
        # spinning in its retry loop.
        return ([], *SW_INS_NOT_SUPPORTED)

    def _select(self, aid):
        """Only the Satochip applet is present, so every other AID is absent.

        card_select() tries satochip, seedkeeper, satodime and satocash in turn
        and treats a non-9000 answer as "not this one", so answering honestly
        here is what makes it settle on Satochip.
        """
        self.pin_verified = False
        if list(aid) == SATOCHIP_AID:
            return ([], *SW_OK)
        return ([], *SW_FILE_NOT_FOUND)

    def _get_data(self, p1, p2):
        blob = {(0x9F, 0x7F): CPLC, (0x00, 0x42): IIN, (0x00, 0x45): self.cin}.get((p1, p2))
        if blob is None:
            return ([], *SW_FILE_NOT_FOUND)
        return (list(blob), *SW_OK)

    def _setup(self, data):
        """Take a PIN and become an initialised card.

        The layout is card_setup()'s, in CardConnector:

            pin_length(1) | pin | pin_tries0(1) | ublk_tries0(1) |
            pin0_length(1) | pin0 | ublk0_length(1) | ublk0 |
            pin_tries1(1) | ublk_tries1(1) | pin1_length(1) | pin1 |
            ublk1_length(1) | ublk1 | memsize(2) | memsize2(2) | ACL(3) |
            option_flags(2) | hmacsha160_key(20) | amount_limit(8)

        Only PIN0 and the four try counts are kept. The leading pin is the
        applet's factory PIN, the sizes and ACLs are RFU on a Satochip, and the
        PUKs and PIN1 have no instruction here that would ever ask for them --
        the wallet sets all three to random bytes it then throws away.
        """
        if self.setup_done:
            # Setup is once per card. Not 0x9C06: card_transmit() answers that
            # one by verifying a PIN and sending the whole command again.
            return ([], *SW_OPERATION_NOT_ALLOWED)

        offset = 1 + data[0]
        pin_tries0, ublk_tries0 = data[offset], data[offset + 1]
        pin0, offset = _blob(data, offset + 2)
        _, offset = _blob(data, offset)
        pin_tries1, ublk_tries1 = data[offset], data[offset + 1]

        self.pin0 = pin0
        self.pin0_tries = pin_tries0
        self.remaining_tries = [pin_tries0, ublk_tries0, pin_tries1, ublk_tries1]
        self.setup_done = True
        return ([], *SW_OK)

    def _verify_pin(self, pin_nbr, pin):
        """Check a PIN, spending a try when it is wrong.

        The count of tries left goes in the status word, not just in GET STATUS:
        card_verify_PIN() reads it out of the low bits of the 0x63Cx it gets
        back, and that is what the wallet puts on screen. Blocked is a separate
        answer rather than a fourteenth wrong-PIN one because it is the answer
        the client stops asking on.
        """
        if not self.setup_done:
            return ([], *SW_SETUP_NOT_DONE)
        if pin_nbr != 0:
            # PIN1 is set at setup and never used, so it is not really here.
            return ([], *SW_INCORRECT_P1)
        if self.remaining_tries[0] == 0:
            return ([], *SW_IDENTITY_BLOCKED)

        if list(pin) == self.pin0:
            self.remaining_tries[0] = self.pin0_tries
            self.pin_verified = True
            return ([], *SW_OK)

        self.remaining_tries[0] -= 1
        self.pin_verified = False
        # Four bits is all the status word has for the count, and a wallet that
        # allowed more tries than that would be reporting the wrong number.
        return ([], 0x63, 0xC0 | min(self.remaining_tries[0], 0x0F))

    def _bip32_refused(self):
        """Why this card cannot answer a BIP32 question, or None if it can.

        All four instructions below are gated the same way, and each answer is
        one pysatochip recognises: 0x9C06 in particular is not an error to the
        client, it is card_transmit() being told to verify the PIN it has cached
        and send the whole command again.
        """
        if not self.setup_done:
            return ([], *SW_SETUP_NOT_DONE)
        if not self.pin_verified:
            return ([], *SW_UNAUTHORIZED)
        return None

    def _import_seed(self, seed):
        """Take a master seed and derive the two keys a seeded Satochip holds.

        One is the BIP32 master key everything is derived from. The other is the
        authentikey, which the card signs its answers with so that a client can
        tell one card's derivations from another's. It is not random: its
        private key is the first 32 bytes of HmacSha512('Bitcoin seed2', seed),
        which is what CardConnector.get_authentikey_from_masterseed() recomputes
        to check a card against a seed it already knows. Deriving it that way
        here means this card's authentikey is the one a real Satochip carrying
        this seed would have.
        """
        refused = self._bip32_refused()
        if refused is not None:
            return refused
        if self.is_seeded:
            # A seed is imported once. Overwriting one silently is how a wallet
            # ends up deriving from a key nobody backed up.
            return ([], *SW_BIP32_ALREADY_SEEDED)
        if len(seed) not in SEED_SIZES:
            return ([], *SW_INVALID_PARAMETER)

        self.master_key = bip32.HDKey.from_seed(bytes(seed))
        self.authentikey = ec.PrivateKey(
            hmac.new(b"Bitcoin seed2", bytes(seed), hashlib.sha512).digest()[:32])
        _say(f"{self.label} seeded with {len(seed)} bytes, master fingerprint "
             f"{self.master_key.my_fingerprint.hex()}")
        return self._get_authentikey()

    def _reset_seed(self, pin):
        """Forget the seed, if the PIN sent with the command is the right one.

        The PIN travels in this command rather than being taken from an earlier
        VERIFY PIN, because this is the instruction that destroys the key.
        """
        refused = self._bip32_refused()
        if refused is not None:
            return refused
        if list(pin) != self.pin0:
            return ([], *SW_AUTH_FAILED)
        if not self.is_seeded:
            return ([], *SW_BIP32_UNINITIALIZED_SEED)

        self.master_key = None
        self.authentikey = None
        _say(f"{self.label} seed erased")
        return ([], *SW_OK)

    def _get_authentikey(self):
        """[coordx_size(2) | coordx | sig_size(2) | sig], signed by itself."""
        refused = self._bip32_refused()
        if refused is not None:
            return refused
        if not self.is_seeded:
            return ([], *SW_BIP32_UNINITIALIZED_SEED)

        coordx = list(self.authentikey.sec()[1:])
        return (_signed(self.authentikey, _size(len(coordx)) + coordx), *SW_OK)

    def _get_extended_key(self, depth, option_flags, data):
        """[chaincode | coordx_size(2) | coordx | sig | authentikey's sig].

        Two signatures, the second over everything the first one produced. The
        derived key signs to prove the coordx is its own, and the authentikey
        signs the lot to prove the derivation came from this card;
        parse_bip32_get_extendedkey() rejects the answer unless the key it
        recovers from that second signature is the authentikey it already holds,
        which is how a client notices it is talking to a different card.

        The top bit of coordx_size is the card asking to be sent back the y
        coordinate it could not spare the time to compute. There is no time to
        spare here, so it is never set and INS 0x74 never arrives.
        """
        refused = self._bip32_refused()
        if refused is not None:
            return refused
        if not self.is_seeded:
            return ([], *SW_BIP32_UNINITIALIZED_SEED)
        if option_flags & 0x06:
            # 0x02 asks for the private key, which a Satochip does not export at
            # all, and 0x04 is BIP85. Neither is implemented, and neither is
            # reachable from this wallet.
            return ([], *SW_INS_NOT_SUPPORTED)

        path = [int.from_bytes(bytes(data[i:i + 4]), "big")
                for i in range(0, 4 * depth, 4)]
        child = self.master_key.derive(path)
        coordx = list(child.sec()[1:])
        message = list(child.chain_code) + _size(len(coordx)) + coordx
        return (_signed(self.authentikey, _signed(child.key, message)), *SW_OK)

    def _get_status(self):
        """The 12-byte status blob card_get_status() unpacks by position."""
        return ([
            self.protocol_version[0],
            self.protocol_version[1],
            self.applet_version[0],
            self.applet_version[1],
            self.remaining_tries[0],
            self.remaining_tries[1],
            self.remaining_tries[2],
            self.remaining_tries[3],
            0x01 if self.needs_2fa else 0x00,
            0x01 if self.is_seeded else 0x00,
            0x01 if self.setup_done else 0x00,
            0x01 if self.needs_secure_channel else 0x00,
        ], *SW_OK)


CARDS = [SimulatedCard(index) for index in range(CARD_COUNT)]


# ------------------------------------------------------------------ the reader

# The page's card tray, supplied by install(). Three calls: inserted(),
# wait(timeout_ms) and publish(index, state). See wallet-cards.js.
_tray = None
_log = None

# Which card is in the reader when there is no tray to ask -- a plain Python
# session, or a page that never mounted one. Card A, so anything that just wants
# a card and does not care which still finds one.
_local_slot = 0


def install(js_cards, log=None):
    """Let the page decide what is in the reader, and tell it what it is holding.

    Without this the reader keeps a card of its own, which is what makes this
    package usable from a plain Python prompt and from a page that has no tray.
    """
    global _tray, _log
    _tray = js_cards
    _log = log
    _publish_all()
    _say(f"tray attached, {CARD_COUNT} cards, reader "
         f"{'empty' if inserted_index() == EMPTY else label_for(inserted_index())}")


def _say(message):
    if _log is not None:
        _log(f"[card] {message}")


def inserted_index():
    """Index of the card in the reader, or EMPTY."""
    if _tray is None:
        return _local_slot
    return int(_tray.inserted())


def current_card():
    index = inserted_index()
    return CARDS[index] if 0 <= index < CARD_COUNT else None


def insert(index):
    """Put a card in the reader.

    Only has any effect without a tray: once one is attached the page owns the
    slot, and what the user can see is the truth.
    """
    global _local_slot
    _local_slot = index


def eject():
    global _local_slot
    _local_slot = EMPTY


def wait_for_card(timeout):
    """Return the card in the reader, or None, having waited if asked to."""
    card = _wait_for_card(timeout)
    if card is None:
        _say("asked for a card, reader is empty")
    return card


def _wait_for_card(timeout):
    """The waiting itself.

    pyscard measures its CardRequest timeout in seconds, where None waits forever
    and 0 looks once; pysatochip passes 0. Waiting is done in slices rather than
    one long park because the page is the only thing that can end it, and while
    this thread is parked the page is free -- which is the whole reason the
    wallet runs in a worker. Without a tray nothing can change the reader, so
    there is nothing to wait for.
    """
    card = current_card()
    if card is not None or timeout == 0 or _tray is None:
        return card

    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        slice_ms = _WAIT_SLICE_MS
        if deadline is not None:
            left_ms = (deadline - time.monotonic()) * 1000
            if left_ms <= 0:
                return None
            slice_ms = min(slice_ms, left_ms)
        _tray.wait(slice_ms)
        card = current_card()
        if card is not None:
            return card


# --------------------------------------------------------- insert/remove events

# Monitors wanting to hear about cards arriving and leaving, and the card they
# were last told about.
_monitors = []
_announced = None
_polling = False


def poll():
    """Tell the monitors about anything that has changed since the last look.

    pyscard runs a background thread that watches the readers and notifies
    observers from it. There is no such thread here: the worker is single
    threaded and the stand-in for threading.Thread drops anything loop-shaped,
    which a poller is. So the polling happens on the caller's thread instead, at
    every point where the wallet reaches into this package. That is enough for
    the case that matters -- a card that was already in the reader when a
    CardConnector was built -- and it costs nothing when nothing has changed.
    """
    global _announced, _polling

    if _polling:
        # An observer's update() talks to the card, which polls again. One pass
        # is enough.
        return
    card = current_card()
    if card is _announced:
        return

    removed, added = _announced, card
    _announced = card
    if removed is not None:
        _say(f"{removed.label} removed")
    if added is not None:
        _say(f"{added.label} inserted, uid={added.uid_sha1}")
    _polling = True
    try:
        for monitor in list(_monitors):
            for observer in list(monitor.observers):
                observer.update(monitor, (_services(added), _services(removed)))
    finally:
        _polling = False


def _services(card):
    return [SimulatedCardService(card)] if card is not None else []


def register_monitor(monitor):
    if monitor not in _monitors:
        _monitors.append(monitor)


def unregister_monitor(monitor):
    if monitor in _monitors:
        _monitors.remove(monitor)


def announce_present(monitor, observer):
    """Hand a newly registered observer the cards already in the reader.

    A card that was inserted before anyone was watching generates no event of its
    own, so registering is the only one it will ever have. pyscard fires it, and
    pysatochip leans on it: RemovalObserver does its whole connect-and-identify
    on the back of this call.
    """
    global _announced
    _announced = current_card()
    if _announced is None:
        _say("a watcher registered, reader is empty")
    else:
        _say(f"a watcher registered, {_announced.label} is in the reader, "
             f"uid={_announced.uid_sha1}")
    observer.update(monitor, (_services(_announced), []))


# ------------------------------------------------------------ published to page

# What the tray was last told, so an unchanged card does not keep waking it.
_published = [None] * CARD_COUNT


def _pack_state(card):
    """A card's state in one Int32, unpacked by describe() in wallet-cards.js."""
    flags = (0x01 if card.setup_done else 0) | (0x02 if card.is_seeded else 0)
    return flags | (min(card.remaining_tries[0], 0xFF) << 8)


def _publish_all():
    if _tray is None:
        return
    for index, card in enumerate(CARDS):
        state = _pack_state(card)
        if _published[index] != state:
            _published[index] = state
            _tray.publish(index, state)


# ------------------------------------------------------------- pyscard surface


class SimulatedCardConnection:
    """pyscard's CardConnection: connect, transmit, disconnect."""

    def __init__(self, card=None):
        self.card = card if card is not None else current_card()
        self.observers = []

    def connect(self, *args, **kwargs):
        if self.card is None:
            raise NoCardException("no card in the reader")
        return self

    def disconnect(self):
        poll()

    def addObserver(self, observer):
        self.observers.append(observer)

    def deleteObserver(self, observer):
        if observer in self.observers:
            self.observers.remove(observer)

    def getReader(self):
        return SimulatedReader.name

    def getATR(self):
        return list(SATOCHIP_ATR)

    def transmit(self, apdu, protocol=None):
        # A connection is to one card, not to the reader, so a card that has been
        # taken out cannot answer -- and answering anyway would let a flow run to
        # completion against a card the user is holding in their hand.
        if self.card is None or current_card() is not self.card:
            raise CardConnectionException("card removed from the reader")
        response = self.card.transmit(list(apdu))
        _publish_all()
        return response


class SimulatedCardService:
    """What pyscard hands to observers and returns from waitforcard()."""

    def __init__(self, card=None):
        self.card = card if card is not None else current_card()
        self.atr = list(SATOCHIP_ATR)
        self.connection = None

    def createConnection(self):
        return SimulatedCardConnection(self.card)


class SimulatedReader:
    name = "Bitsaga simulated Satochip reader"

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.name

    def createConnection(self):
        return SimulatedCardConnection()


def card_service():
    """The card in the reader as pyscard would present it, or None if empty."""
    card = current_card()
    if card is None:
        return None
    return SimulatedCardService(card)
