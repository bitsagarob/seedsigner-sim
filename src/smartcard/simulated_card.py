"""
Six simulated smartcards -- a SeedKeeper and a Satochip for each of three tray
slots -- and the one reader they go in and out of.

Browsers have no smartcard API at all, so the only honest place to fake a card is
the transport. pysatochip reaches a physical card through pyscard, and this module
replaces that layer with cards implemented in Python. Everything above it, the
whole of pysatochip and the whole of SeedSigner, runs unmodified against them,
which is the point: the demo exercises the real flows rather than a mock of them.

Two applets, because they are two different products and the wallet talks to them
differently. A Satochip holds one BIP32 master key and derives from it; a
SeedKeeper holds a list of labelled secrets and hands them back. Each answers its
own AID and its own instruction set, and neither answers the other's, which is how
CardConnector.card_select() settles on the right one.

There are three slots because a user needs to be able to tell one card from
another -- put a seed on Card A, check Card B is still blank, come back to Card A
and find it as it was left. Each slot holds one card of each type: swapping the
type in the tray is swapping the card, so the two have different CINs and so
different UIDs, and each keeps its own state while the other is out.

Each card starts blank, with nothing on it and setup not done, so the wallet
drives its own initialisation flow instead of a shortcut.

Cards live in CARDS, a module-level registry, so their state survives connect and
disconnect the way a card taken out of a reader and put back would.

Which card is in the reader, and which type it is, comes from the page through the
SharedArrayBuffer set up by install(). Reading it is what makes an empty reader an
empty reader: with nothing inserted, waiting for a card times out and transmitting
raises, exactly as they do when there is no card in a real one.
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
INS_SEEDKEEPER_IMPORT_SECRET = 0xA1
INS_SEEDKEEPER_EXPORT_SECRET = 0xA2
INS_SEEDKEEPER_RESET_SECRET = 0xA5
INS_SEEDKEEPER_LIST_HEADERS = 0xA6
INS_SEEDKEEPER_GET_STATUS = 0xA7

# P2 for the SeedKeeper's multi-step instructions. Import runs INIT / PROCESS* /
# FINAL; export and the header listing run INIT then NEXT until the card says it
# has nothing more.
OP_INIT = 0x01
OP_NEXT = 0x02
OP_FINAL = 0x03

# P1: whether the secret crosses in the clear or encrypted to another card's key.
# Only the plaintext half is implemented here.
EXPORT_PLAIN = 0x01

SW_OK = (0x90, 0x00)
SW_FILE_NOT_FOUND = (0x6A, 0x82)
SW_INS_NOT_SUPPORTED = (0x6D, 0x00)
SW_NO_MEMORY_LEFT = (0x9C, 0x01)
SW_AUTH_FAILED = (0x9C, 0x02)
SW_OPERATION_NOT_ALLOWED = (0x9C, 0x03)
SW_SETUP_NOT_DONE = (0x9C, 0x04)
SW_UNAUTHORIZED = (0x9C, 0x06)
SW_SECRET_NOT_FOUND = (0x9C, 0x08)
SW_IDENTITY_BLOCKED = (0x9C, 0x0C)
SW_INVALID_PARAMETER = (0x9C, 0x0F)
SW_INCORRECT_P1 = (0x9C, 0x10)
SW_INCORRECT_P2 = (0x9C, 0x11)
SW_SEQUENCE_END = (0x9C, 0x12)
SW_BIP32_UNINITIALIZED_SEED = (0x9C, 0x14)
SW_BIP32_ALREADY_SEEDED = (0x9C, 0x17)
SW_EXPORT_NOT_ALLOWED = (0x9C, 0x31)

# What a Satochip will hold a seed of, in bytes. A BIP39 seed is 64.
SEED_SIZES = range(16, 65)

SATOCHIP_AID = [0x53, 0x61, 0x74, 0x6F, 0x43, 0x68, 0x69, 0x70]
SEEDKEEPER_AID = [0x53, 0x65, 0x65, 0x64, 0x4B, 0x65, 0x65, 0x70, 0x65, 0x72]

# The one value of SEEDKEEPER_DIC_EXPORT_RIGHTS that lets a secret leave the card
# in the clear. The other three -- forbidden, encrypted only, authenticated only --
# are all refusals here, and refusing them is the whole of the policy this card
# enforces.
PLAINTEXT_EXPORT_ALLOWED = 0x01

# Where a secret came from, SEEDKEEPER_DIC_ORIGIN. The card sets this itself: it
# is a statement about how the bytes arrived, so the client does not get a say.
ORIGIN_PLAINTEXT_IMPORT = 0x01

# Object memory on a SeedKeeper, in bytes. The absolute number is a card's own
# business; what matters is that the wallet's estimate of what a secret will cost
# (seedkeeper_utils.calculate_seedkeeper_secret_size) and what this card actually
# charges for it are the same arithmetic, so "not enough space" means it.
SEEDKEEPER_MEMORY_BYTES = 32000

# How much of a secret comes back in one export answer. A response has to fit in
# an APDU, so a long secret arrives in pieces and the client reassembles it; 128
# is what CardConnector sends in, so it is what this sends back.
EXPORT_CHUNK = 128

# A JavaCard-shaped ATR. Nothing inspects its contents: pysatochip logs it and
# compares it against the Windows Hello virtual device it skips, so the only real
# requirement is that it is stable and is not that one. Every card shares it, as
# cards of one model would.
JAVACARD_ATR = [
    0x3B, 0xF9, 0x18, 0x00, 0xFF, 0x81, 0x31, 0xFE, 0x45,
    0x4A, 0x43, 0x4F, 0x50, 0x76, 0x32, 0x34, 0x31, 0xB7,
]

# GlobalPlatform GET DATA answers. pysatochip concatenates these three and hashes
# them into a card UID, so they need to be stable and, between cards, distinct.
# Only the CIN differs, because only one of them has to: its last two bytes are
# the applet's letter and the slot's digit, so the SeedKeeper and the Satochip a
# slot can hold are two cards rather than two names for one.
CPLC = [0x9F, 0x7F, 0x2A] + [0x42] * 42
IIN = [0x42, 0x49, 0x54, 0x53, 0x41, 0x47, 0x41]
CIN_PREFIX = [0x53, 0x49, 0x4D, 0x30]  # "SIM0", then the applet and the slot

CARD_COUNT = 3

# The two card types, and the order the tray packs them in. SeedKeeper is 0 so
# that a zeroed buffer already says what the default is -- see wallet-cards.js.
KIND_SEEDKEEPER = 0
KIND_SATOCHIP = 1
KIND_COUNT = 2

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
    """The two-byte big-endian prefix every sized field is measured with."""
    return [length >> 8, length & 0xFF]


def _read_size(data, offset=0):
    return (data[offset] << 8) + data[offset + 1]


def _signature(key, message):
    """A signature over a message's SHA-256, as a list of bytes.

    Signing without grinding because grinding exists to keep transaction
    signatures short, which matters nowhere here, and pure-Python secp256k1
    under WebAssembly is slow enough without signing some of them twice.
    """
    return list(key.sign(hashlib.sha256(bytes(message)).digest(),
                         grind=False).serialize())


def _signed(key, message):
    """A message with a signature over it appended, length-prefixed.

    Every BIP32 answer a Satochip gives is built out of these. The client does
    not verify the signature against a key it was told; it *recovers* the key
    from the signature and keeps the answer only if what comes back matches
    what the message claims, so the signature is both the proof and the half of
    the public key an x coordinate on its own leaves out.
    """
    signature = _signature(key, message)
    return list(message) + _size(len(signature)) + signature


class SimulatedCard:
    """What every card here is: an identity, a PIN, and one applet on top.

    The applet is the subclass. This holds the parts a Satochip and a SeedKeeper
    genuinely share -- the GlobalPlatform identity, card_setup(), VERIFY PIN, the
    status blob and the authentikey -- because pysatochip sends all of those to
    either card, in the same shape, and a second copy of them would drift.
    """

    # Filled in by the applet.
    AID = None
    APPLET = None
    CIN_BYTE = None
    PROTOCOL_VERSION = (0, 0)
    APPLET_VERSION = (0, 0)

    def __init__(self, index):
        self.index = index
        self.label = label_for(index)
        # The two bytes that make this card not the others.
        self.cin = CIN_PREFIX + [self.CIN_BYTE, 0x31 + index]

        self.protocol_version = self.PROTOCOL_VERSION
        self.applet_version = self.APPLET_VERSION
        # PIN0, PUK0, PIN1, PUK1
        self.remaining_tries = [5, 5, 5, 5]
        self.needs_2fa = False
        self.setup_done = False
        # Both set by setup, which is the only thing that can set them: a card
        # with no PIN on it is a card no PIN can be verified against.
        self.pin0 = None
        self.pin0_tries = 0
        # Whether this card's applet is the one currently selected. A card-edge
        # instruction is addressed to an applet, so a card whose SELECT was for
        # somebody else's AID answers none of them -- what is on the other end of
        # a failed SELECT is the card manager, which has no idea what a Satochip
        # is. That is what stops a Satochip from being half-driven through a
        # SeedKeeper flow before the first instruction it does not have.
        self.selected = False
        # Cleared by SELECT, because a JavaCard applet loses its PIN state when
        # it is deselected, and set by VERIFY PIN. Every applet instruction is
        # gated on it.
        self.pin_verified = False
        # The key the card signs its answers with, so that a client can tell one
        # card's answers from another's. Where it comes from is the applet's
        # business; that it exists at all is not.
        self.authentikey = None
        # A secure channel would encrypt every APDU with a session key negotiated
        # over ECDH. It protects the wire between reader and card, and here there
        # is no wire, so the card reports that it does not need one.
        self.needs_secure_channel = False

    @property
    def is_seeded(self):
        """Whether this card is carrying anything. What that means is the
        applet's to say, and it is what the tray's pill reads."""
        raise NotImplementedError

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
        if cla == CLA_CARDEDGE and self.selected:
            if ins == INS_GET_STATUS:
                return self._get_status()
            if ins == INS_SETUP:
                return self._setup(data)
            if ins == INS_VERIFY_PIN:
                return self._verify_pin(p1, data)
            answered = self._applet(ins, p1, p2, data)
            if answered is not None:
                return answered

        # Unknown instruction. card_transmit() returns any status it does not
        # recognise straight to the caller, so this ends the exchange rather than
        # spinning in its retry loop.
        return ([], *SW_INS_NOT_SUPPORTED)

    def _applet(self, ins, p1, p2, data):
        """The applet's own instructions, or None for one it does not have."""
        return None

    def _select(self, aid):
        """One applet is installed, so every other AID is absent.

        card_select() tries satochip, seedkeeper, satodime and satocash in turn
        and treats a non-9000 answer as "not this one", so answering honestly
        here is what makes it settle on the right one -- and what leaves a card
        of the wrong type unselected, and so unable to answer anything, when the
        wallet was looking for the other one.
        """
        self.pin_verified = False
        self.selected = list(aid) == self.AID
        if self.selected:
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
        applet's factory PIN, the sizes and ACLs are RFU, and the PUKs and PIN1
        have no instruction here that would ever ask for them -- the wallet sets
        all three to random bytes it then throws away.
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
        self._applet_setup()
        return ([], *SW_OK)

    def _applet_setup(self):
        """What the applet does once the card has a PIN, if anything."""

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

    def _pin_refused(self):
        """Why this card cannot answer, or None if it can.

        Every applet instruction is gated the same way, and each answer is one
        pysatochip recognises: 0x9C06 in particular is not an error to the
        client, it is card_transmit() being told to verify the PIN it has cached
        and send the whole command again.
        """
        if not self.setup_done:
            return ([], *SW_SETUP_NOT_DONE)
        if not self.pin_verified:
            return ([], *SW_UNAUTHORIZED)
        return None

    def _get_authentikey(self):
        """[coordx_size(2) | coordx | sig_size(2) | sig], signed by itself."""
        refused = self._pin_refused()
        if refused is not None:
            return refused
        if self.authentikey is None:
            return ([], *SW_BIP32_UNINITIALIZED_SEED)

        coordx = list(self.authentikey.sec()[1:])
        return (_signed(self.authentikey, _size(len(coordx)) + coordx), *SW_OK)

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


class SimulatedSatochip(SimulatedCard):
    """The signing card: one BIP32 master key, and derivations from it."""

    AID = SATOCHIP_AID
    APPLET = "Satochip"
    CIN_BYTE = ord("S")
    PROTOCOL_VERSION = (0, 12)
    APPLET_VERSION = (0, 12)

    def __init__(self, index):
        super().__init__(index)
        # The master key a seed puts on the card, and the only place that seed
        # survives at all. It lives here and nowhere else -- no filesystem, no
        # storage in the page -- so reloading is a factory-fresh card.
        self.master_key = None

    @property
    def is_seeded(self):
        return self.master_key is not None

    def _applet(self, ins, p1, p2, data):
        if ins == INS_BIP32_IMPORT_SEED:
            return self._import_seed(data)
        if ins == INS_BIP32_RESET_SEED:
            return self._reset_seed(data[:p1])
        if ins == INS_BIP32_GET_AUTHENTIKEY:
            return self._get_authentikey()
        if ins == INS_BIP32_GET_EXTENDED_KEY:
            return self._get_extended_key(p1, p2, data)
        return None

    def _import_seed(self, seed):
        """Take a master seed and derive the two keys a seeded Satochip holds.

        One is the BIP32 master key everything is derived from. The other is the
        authentikey, which the card signs its answers with. It is not random: its
        private key is the first 32 bytes of HmacSha512('Bitcoin seed2', seed),
        which is what CardConnector.get_authentikey_from_masterseed() recomputes
        to check a card against a seed it already knows. Deriving it that way
        here means this card's authentikey is the one a real Satochip carrying
        this seed would have.
        """
        refused = self._pin_refused()
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
        refused = self._pin_refused()
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
        refused = self._pin_refused()
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


class Secret:
    """One secret on a SeedKeeper: a header, and the bytes the header describes.

    The split matters. The client proposes the four fields that say what the
    secret *is* -- type, export rights, subtype and label -- and the card owns
    the rest: the id it files it under, where it came from, how often it has left,
    and the fingerprint. A client that could write its own fingerprint could
    hand back a secret that is not the one it stored, and seedkeeper_import_secret
    and seedkeeper_export_secret both check that fingerprint against a hash they
    compute themselves.
    """

    def __init__(self, sid, fields, payload):
        self.sid = sid
        self.type = fields[0]
        self.origin = ORIGIN_PLAINTEXT_IMPORT
        self.export_rights = fields[2]
        self.exports_plain = 0
        self.exports_secure = 0
        self.export_counter = 0
        self.subtype = fields[10]
        self.rfu2 = fields[11]
        self.label = list(fields[13:13 + fields[12]])
        self.payload = list(payload)
        self.fingerprint = list(hashlib.sha256(bytes(self.payload)).digest()[:4])

    @property
    def label_text(self):
        try:
            return bytes(self.label).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(self.label).hex()

    def header(self):
        """The 15 bytes plus label that parse_seedkeeper_header() unpacks."""
        return (_size(self.sid) + [self.type, self.origin, self.export_rights,
                                   self.exports_plain, self.exports_secure,
                                   self.export_counter]
                + self.fingerprint + [self.subtype, self.rfu2, len(self.label)]
                + self.label)

    def size_on_card(self):
        """What this costs, counted the way the wallet predicts it will be.

        seedkeeper_utils.calculate_seedkeeper_secret_size adds the header to the
        secret padded up to the next 16-byte boundary, always adding padding even
        when the length already lands on one. Charging anything else would make
        the wallet's "not enough space" warning a guess.
        """
        return len(self.header()) + len(self.payload) + 16 - len(self.payload) % 16


class SimulatedSeedKeeper(SimulatedCard):
    """The storage card: a list of labelled secrets, and a policy on each.

    It has an authentikey like a Satochip, but not from a seed -- there is no
    seed here to derive one from. A real SeedKeeper generates it on the card at
    setup; this one derives it from the card's own UID so that a given card is
    the same card every time the page is reloaded, which is what makes a test
    able to say which card signed something.
    """

    AID = SEEDKEEPER_AID
    APPLET = "SeedKeeper"
    CIN_BYTE = ord("K")
    # v2. The wallet reads the minor version to decide how to lay a seed out --
    # a v1 card takes the mnemonic as text, a v2 card takes the master seed plus
    # the entropy behind it -- so this is not decoration.
    PROTOCOL_VERSION = (0, 2)
    APPLET_VERSION = (0, 2)

    def __init__(self, index):
        super().__init__(index)
        # Everything the card is holding, by id, and the next id to hand out.
        # Here and nowhere else: no filesystem, no storage in the page, so
        # reloading is a factory-fresh card.
        self.secrets = {}
        self.next_sid = 1
        # The three instructions that run over several APDUs each keep their
        # place here. A real applet has exactly this state and loses it the same
        # way, which is why a client cannot interleave two of them.
        self.importing = None
        self.exporting = None
        self.listing = None

    @property
    def is_seeded(self):
        """A SeedKeeper is carrying something once it holds a secret. Nothing
        reads the status byte this feeds for a SeedKeeper -- the wallet only
        prints it for a Satochip -- but the tray's pill does."""
        return bool(self.secrets)

    def _applet_setup(self):
        self.authentikey = ec.PrivateKey(
            hashlib.sha256(b"seedkeeper authentikey" + bytes(self.uid)).digest())

    def _applet(self, ins, p1, p2, data):
        if ins == INS_BIP32_GET_AUTHENTIKEY:
            return self._get_authentikey()
        if ins == INS_SEEDKEEPER_GET_STATUS:
            return self._seedkeeper_status()
        if ins == INS_SEEDKEEPER_LIST_HEADERS:
            return self._list_headers(p2)
        if ins == INS_SEEDKEEPER_IMPORT_SECRET and p1 == EXPORT_PLAIN:
            return self._import_secret(p2, data)
        if ins == INS_SEEDKEEPER_EXPORT_SECRET and p1 == EXPORT_PLAIN:
            return self._export_secret(p2, data)
        if ins == INS_SEEDKEEPER_RESET_SECRET:
            return self._reset_secret(data)
        # Everything else, including the encrypted halves of import and export,
        # falls through to "instruction not supported". Those two need a session
        # key negotiated with a second card's public key, and there is no second
        # card here to negotiate with.
        return None

    def _used_memory(self):
        return sum(secret.size_on_card() for secret in self.secrets.values())

    def _seedkeeper_status(self):
        """[nb_secrets | total_memory | free_memory | logs...], all 2 bytes each.

        The log counters are zero and the last-log slot is empty because this
        card keeps no log. seedkeeper_get_status() reads them positionally and
        the wallet only ever asks it for free_memory.
        """
        refused = self._pin_refused()
        if refused is not None:
            return refused
        return (_size(len(self.secrets)) + _size(SEEDKEEPER_MEMORY_BYTES)
                + _size(SEEDKEEPER_MEMORY_BYTES - self._used_memory())
                + _size(0) + _size(0) + 7 * [0x00], *SW_OK)

    def _list_headers(self, p2):
        """One header per call, then 0x9C12 to say there are no more.

        seedkeeper_list_secret_headers() loops until it gets a status it does not
        recognise, so the end of the list is a status word rather than an empty
        answer.
        """
        refused = self._pin_refused()
        if refused is not None:
            return refused
        if p2 == OP_INIT:
            self.listing = sorted(self.secrets)
        elif p2 != OP_NEXT or self.listing is None:
            return ([], *SW_INCORRECT_P2)

        if not self.listing:
            return ([], *SW_SEQUENCE_END)
        return (self.secrets[self.listing.pop(0)].header(), *SW_OK)

    def _import_secret(self, p2, data):
        """Take a secret in three steps: its header, its bytes, and a commit.

        The header arrives without the id -- seedkeeper_import_secret strips the
        two bytes make_header() left room for, because the id is the card's to
        assign -- followed by the size the secret will occupy once padded, which
        is what the card reserves space for.
        """
        refused = self._pin_refused()
        if refused is not None:
            return refused

        if p2 == OP_INIT:
            fields, padded_size = data[:-2], _read_size(data, len(data) - 2)
            if len(fields) < 13 or len(fields) != 13 + fields[12]:
                return ([], *SW_INVALID_PARAMETER)
            wanted = 15 + fields[12] + padded_size
            if wanted > SEEDKEEPER_MEMORY_BYTES - self._used_memory():
                return ([], *SW_NO_MEMORY_LEFT)
            self.importing = (fields, [])
            return ([], *SW_OK)

        if self.importing is None or p2 not in (OP_NEXT, OP_FINAL):
            return ([], *SW_OPERATION_NOT_ALLOWED)

        fields, payload = self.importing
        payload += data[2:2 + _read_size(data)]
        if p2 == OP_NEXT:
            return ([], *SW_OK)

        secret = Secret(self.next_sid, fields, payload)
        self.secrets[secret.sid] = secret
        self.next_sid += 1
        self.importing = None
        _say(f"{self.label} stored secret {secret.sid}, "
             f"type 0x{secret.type:02x} subtype 0x{secret.subtype:02x}, "
             f"label {secret.label_text!r}, {len(secret.payload)} bytes, "
             f"fingerprint {bytes(secret.fingerprint).hex()}")
        # The client hashes the bytes it sent and compares; answering with the
        # card's own hash of what it stored is what makes that comparison mean
        # something.
        return (_size(secret.sid) + secret.fingerprint, *SW_OK)

    def _export_secret(self, p2, data):
        """Hand a secret back, if its own export rights allow it in the clear.

        The policy is the point of a SeedKeeper: a secret is stored with the
        terms it may leave under, and the card is the thing that enforces them.
        Only 'Plaintext export allowed' can be answered here, because plaintext
        is the only way out this card implements -- anything else is refused with
        0x9C31, which pysatochip reports as "export not allowed by SeedKeeper
        policy" rather than as a failure to read.
        """
        refused = self._pin_refused()
        if refused is not None:
            return refused

        if p2 == OP_INIT:
            secret = self.secrets.get(_read_size(data))
            if secret is None:
                return ([], *SW_SECRET_NOT_FOUND)
            if secret.export_rights != PLAINTEXT_EXPORT_ALLOWED:
                _say(f"{self.label} refused a plaintext export of secret "
                     f"{secret.sid}, export rights 0x{secret.export_rights:02x}")
                return ([], *SW_EXPORT_NOT_ALLOWED)
            secret.exports_plain = min(secret.exports_plain + 1, 0xFF)
            self.exporting = [secret, 0]
            _say(f"{self.label} exporting secret {secret.sid} in the clear, "
                 f"label {secret.label_text!r}")
            return (secret.header(), *SW_OK)

        if self.exporting is None or p2 != OP_NEXT:
            return ([], *SW_OPERATION_NOT_ALLOWED)

        secret, offset = self.exporting
        chunk = secret.payload[offset:offset + EXPORT_CHUNK]
        offset += len(chunk)
        self.exporting = [secret, offset]
        if offset < len(secret.payload):
            return (_size(len(chunk)) + chunk, *SW_OK)

        # Last chunk, so the signature comes with it -- that is how the client
        # knows it was the last one. It covers the header as well as the secret,
        # so a card cannot hand back the right bytes under someone else's label.
        self.exporting = None
        signature = _signature(self.authentikey, secret.header() + secret.payload)
        return (_size(len(chunk)) + chunk + _size(len(signature)) + signature, *SW_OK)

    def _reset_secret(self, data):
        """Forget one secret, freeing what it occupied."""
        refused = self._pin_refused()
        if refused is not None:
            return refused
        secret = self.secrets.pop(_read_size(data), None)
        if secret is None:
            return ([], *SW_SECRET_NOT_FOUND)
        _say(f"{self.label} erased secret {secret.sid}")
        return ([], *SW_OK)


# One of each type per slot, because swapping the type in the tray is swapping
# the card: they have different UIDs and each keeps its own state while the other
# is out. Indexed by KIND_*, so the order here is the order the tray packs.
CARDS = [[SimulatedSeedKeeper(index), SimulatedSatochip(index)]
         for index in range(CARD_COUNT)]


# ------------------------------------------------------------------ the reader

# The page's card tray, supplied by install(). Four calls: inserted(), kind(index),
# wait(timeout_ms) and publish(index, kind, state). See wallet-cards.js.
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
    card = current_card()
    _say(f"tray attached, {CARD_COUNT} slots, a SeedKeeper and a Satochip each, "
         f"reader {'empty' if card is None else card.label + ' ' + card.APPLET}")


def _say(message):
    if _log is not None:
        _log(f"[card] {message}")


def inserted_index():
    """Index of the card in the reader, or EMPTY."""
    if _tray is None:
        return _local_slot
    return int(_tray.inserted())


def kind_for(index):
    """Which type of card sits in that slot. The user's choice, so the page owns
    it; without a tray it is the default, which is a SeedKeeper."""
    if _tray is None:
        return KIND_SEEDKEEPER
    return int(_tray.kind(index))


def current_card():
    index = inserted_index()
    if not 0 <= index < CARD_COUNT:
        return None
    return CARDS[index][kind_for(index)]


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
        _say(f"{removed.label} removed, {removed.APPLET}")
    if added is not None:
        _say(f"{added.label} inserted, uid={added.uid_sha1}, {added.APPLET}")
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
             f"uid={_announced.uid_sha1}, {_announced.APPLET}")
    observer.update(monitor, (_services(_announced), []))


# ------------------------------------------------------------ published to page

# What the tray was last told about each card, so an unchanged one does not keep
# waking it. Both types of every slot, because the tray shows the state of
# whichever type the user has selected and cannot ask Python about the other.
_published = [[None] * KIND_COUNT for _ in range(CARD_COUNT)]


def _pack_state(card):
    """A card's state in one Int32, unpacked by describe() in wallet-cards.js."""
    flags = (0x01 if card.setup_done else 0) | (0x02 if card.is_seeded else 0)
    return flags | (min(card.remaining_tries[0], 0xFF) << 8)


def _publish_all():
    if _tray is None:
        return
    for index, slot in enumerate(CARDS):
        for kind, card in enumerate(slot):
            state = _pack_state(card)
            if _published[index][kind] != state:
                _published[index][kind] = state
                _tray.publish(index, kind, state)


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
        return list(JAVACARD_ATR)

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
        self.atr = list(JAVACARD_ATR)
        self.connection = None

    def createConnection(self):
        return SimulatedCardConnection(self.card)


class SimulatedReader:
    name = "Bitsaga simulated smartcard reader"

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
