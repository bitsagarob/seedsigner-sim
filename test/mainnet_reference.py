"""Bitcoin worked out here, so the wallet's answers have something to be wrong against.

test_mainnet.py drives the simulator to mainnet, exports an xpub through the
device's own screens and has it sign a transaction. Neither of those is worth
anything unless the expected answer comes from somewhere else, so this file is
that somewhere else: BIP39, BIP32, the SLIP-132 version bytes, the BIP143
sighash and ECDSA verification, written out from the specifications with nothing
but hashlib underneath.

Nothing here imports embit, and nothing here reads wallet.zip. That is the whole
point. The wallet derives with embit and signs with embit; if this file also did,
the test would be embit agreeing with itself and would pass just as happily on a
wallet that derived every key wrongly in the same way.

RIPEMD-160 is written out rather than taken from hashlib, for two reasons. It is
the one hash that OpenSSL 3 hides behind its legacy provider, so hashlib.new()
raises on a good many hosts, and a test that skips itself on the machines it was
meant to protect is not a test. And a key fingerprint is a hash160, so writing it
out keeps the second half of that hash on this side of the fence too.

None of this is a Bitcoin library. It handles exactly the cases the test needs --
compressed keys, one P2WPKH input, SIGHASH_ALL -- and it is deliberately literal
rather than fast or general. The published vectors in check_published_vectors()
are what say it is right, and the test runs them as named checks before it trusts
a single value here.

Every key in this file comes from a published test mnemonic. Nothing derived here
should ever hold value.
"""

import hashlib
import hmac


# --- RIPEMD-160 --------------------------------------------------------------
# Straight from the specification's tables. Anchored on the standard "abc"
# vector in check_published_vectors().

_RL = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
       7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
       3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
       1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
       4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13)
_RR = (5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
       6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
       15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
       8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
       12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11)
_SL = (11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
       7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
       11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
       11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
       9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6)
_SR = (8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
       9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
       9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
       15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
       8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11)
_KL = (0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E)
_KR = (0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000)
_MASK = 0xFFFFFFFF


def _rol(value, bits):
    return ((value << bits) | (value >> (32 - bits))) & _MASK


def _mix(round_index, x, y, z):
    if round_index < 16:
        return x ^ y ^ z
    if round_index < 32:
        return (x & y) | (~x & z)
    if round_index < 48:
        return (x | ~y) ^ z
    if round_index < 64:
        return (x & z) | (y & ~z)
    return x ^ (y | ~z)


def ripemd160(message: bytes) -> bytes:
    padded = message + b"\x80"
    padded += b"\x00" * ((56 - len(padded) % 64) % 64)
    padded += (len(message) * 8).to_bytes(8, "little")

    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    for start in range(0, len(padded), 64):
        block = padded[start:start + 64]
        words = [int.from_bytes(block[i:i + 4], "little") for i in range(0, 64, 4)]
        al, bl, cl, dl, el = h
        ar, br, cr, dr, er = h
        for j in range(80):
            t = (_rol((al + _mix(j, bl, cl, dl) + words[_RL[j]] + _KL[j // 16]) & _MASK,
                      _SL[j]) + el) & _MASK
            al, bl, cl, dl, el = el, t, bl, _rol(cl, 10), dl
            t = (_rol((ar + _mix(79 - j, br, cr, dr) + words[_RR[j]] + _KR[j // 16]) & _MASK,
                      _SR[j]) + er) & _MASK
            ar, br, cr, dr, er = er, t, br, _rol(cr, 10), dr
        h = [(h[1] + cl + dr) & _MASK, (h[2] + dl + er) & _MASK, (h[3] + el + ar) & _MASK,
             (h[4] + al + br) & _MASK, (h[0] + bl + cr) & _MASK]
    return b"".join(word.to_bytes(4, "little") for word in h)


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def double_sha256(data: bytes) -> bytes:
    return sha256(sha256(data))


def hash160(data: bytes) -> bytes:
    return ripemd160(sha256(data))


# --- secp256k1 ---------------------------------------------------------------
# The curve's published parameters, and the two operations that need it: turning
# a private key into a public one, and checking a signature.

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def point_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        slope = 3 * a[0] * a[0] * pow(2 * a[1], -1, P) % P
    else:
        slope = (b[1] - a[1]) * pow(b[0] - a[0], -1, P) % P
    x = (slope * slope - a[0] - b[0]) % P
    return (x, (slope * (a[0] - x) - a[1]) % P)


def point_mul(scalar, point=G):
    result = None
    addend = point
    while scalar:
        if scalar & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        scalar >>= 1
    return result


def serialize_point(point) -> bytes:
    """SEC1 compressed, which is the only form any of this uses."""
    return bytes([2 + (point[1] & 1)]) + point[0].to_bytes(32, "big")


def public_key(secret: int) -> bytes:
    return serialize_point(point_mul(secret))


def verify_signature(public_key_bytes: bytes, message_hash: bytes, r: int, s: int) -> bool:
    """Textbook ECDSA verification against an already-serialized public key.

    The public key is not decompressed: every key this test checks a signature
    for is one it derived itself, so the caller passes the point in and this
    only has to agree that the signature was made by it.
    """
    if not (1 <= r < N and 1 <= s < N):
        return False
    point = _decompress(public_key_bytes)
    if point is None:
        return False
    z = int.from_bytes(message_hash, "big")
    s_inverse = pow(s, -1, N)
    candidate = point_add(point_mul(z * s_inverse % N),
                          point_mul(r * s_inverse % N, point))
    return candidate is not None and candidate[0] % N == r


def _decompress(serialized: bytes):
    """The point behind a compressed key, or None if there is not one.

    y^2 = x^3 + 7, and p % 4 == 3, so the square roots are a single exponentiation.
    """
    if len(serialized) != 33 or serialized[0] not in (2, 3):
        return None
    x = int.from_bytes(serialized[1:], "big")
    y = pow(x * x * x + 7, (P + 1) // 4, P)
    if pow(y, 2, P) != (x * x * x + 7) % P:
        return None
    if y & 1 != serialized[0] & 1:
        y = P - y
    return (x, y)


def decode_der(signature: bytes):
    """(r, s) out of the DER encoding a PSBT carries a signature in."""
    if len(signature) < 8 or signature[0] != 0x30 or signature[1] != len(signature) - 2:
        raise ValueError("not a DER sequence")
    if signature[2] != 0x02:
        raise ValueError("no r")
    r_len = signature[3]
    r = int.from_bytes(signature[4:4 + r_len], "big")
    rest = signature[4 + r_len:]
    if rest[0] != 0x02 or rest[1] != len(rest) - 2:
        raise ValueError("no s")
    return r, int.from_bytes(rest[2:], "big")


# --- base58check -------------------------------------------------------------

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58check(payload: bytes) -> str:
    data = payload + double_sha256(payload)[:4]
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, remainder = divmod(number, 58)
        out = _B58[remainder] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


# --- BIP39 and BIP32 ---------------------------------------------------------
# hashlib's PBKDF2 is the one thing here that is somebody else's code, and it is
# a different somebody: under Pyodide the wallet has no OpenSSL at all and runs
# pycryptodome's PBKDF2 instead, so even this is not a shared code path.

def bip39_seed(mnemonic: str, passphrase: str = "") -> bytes:
    return hashlib.pbkdf2_hmac("sha512", mnemonic.encode("utf-8"),
                               ("mnemonic" + passphrase).encode("utf-8"), 2048, 64)


HARDENED = 0x80000000


def master_key(seed: bytes):
    digest = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    return int.from_bytes(digest[:32], "big"), digest[32:]


def derive_child(secret: int, chain_code: bytes, index: int):
    if index >= HARDENED:
        data = b"\x00" + secret.to_bytes(32, "big") + index.to_bytes(4, "big")
    else:
        data = public_key(secret) + index.to_bytes(4, "big")
    digest = hmac.new(chain_code, data, hashlib.sha512).digest()
    child = (int.from_bytes(digest[:32], "big") + secret) % N
    return child, digest[32:]


def parse_path(path: str):
    parts = [p for p in path.strip().split("/") if p and p != "m"]
    indices = []
    for part in parts:
        if part[-1] in "'h":
            indices.append(int(part[:-1]) + HARDENED)
        else:
            indices.append(int(part))
    return indices


def fingerprint(public_key_bytes: bytes) -> bytes:
    return hash160(public_key_bytes)[:4]


class Key:
    """One node of the tree, and enough of its parent to serialize an xpub."""

    def __init__(self, secret, chain_code, depth=0, parent=b"\x00" * 4, index=0):
        self.secret = secret
        self.chain_code = chain_code
        self.depth = depth
        self.parent = parent
        self.index = index

    @property
    def public(self) -> bytes:
        return public_key(self.secret)

    @property
    def fingerprint(self) -> bytes:
        return fingerprint(self.public)

    def child(self, index: int) -> "Key":
        secret, chain_code = derive_child(self.secret, self.chain_code, index)
        return Key(secret, chain_code, self.depth + 1, self.fingerprint, index)

    def derive(self, path: str) -> "Key":
        key = self
        for index in parse_path(path):
            key = key.child(index)
        return key

    def extended_public_key(self, version: bytes) -> str:
        return base58check(version + bytes([self.depth]) + self.parent
                           + self.index.to_bytes(4, "big") + self.chain_code + self.public)


def root_from_mnemonic(mnemonic: str, passphrase: str = "") -> Key:
    secret, chain_code = master_key(bip39_seed(mnemonic, passphrase))
    return Key(secret, chain_code)


# The version bytes that decide the four letters an extended key starts with.
# BIP32's own xpub, and the two SLIP-132 versions SeedSigner exports native
# segwit under: zpub for single sig, Zpub for multisig.
VERSION_XPUB = bytes.fromhex("0488b21e")
VERSION_ZPUB = bytes.fromhex("04b24746")
VERSION_ZPUB_MULTISIG = bytes.fromhex("02aa7ed3")


# --- transactions ------------------------------------------------------------

def varint(n: int) -> bytes:
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + n.to_bytes(2, "little")
    return b"\xfe" + n.to_bytes(4, "little")


def p2wpkh_script(key_hash: bytes) -> bytes:
    """OP_0 <20-byte key hash>, the whole of a native segwit output script."""
    return b"\x00\x14" + key_hash


def unsigned_transaction(txid: bytes, vout: int, outputs, sequence=0xFFFFFFFD, locktime=0):
    """Version 2, one input, no witness: what a PSBT carries in its global map.

    `outputs` is a list of (amount, script_pubkey).
    """
    data = (2).to_bytes(4, "little")
    data += varint(1) + txid[::-1] + vout.to_bytes(4, "little") + varint(0)
    data += sequence.to_bytes(4, "little")
    data += varint(len(outputs))
    for amount, script in outputs:
        data += amount.to_bytes(8, "little") + varint(len(script)) + script
    return data + locktime.to_bytes(4, "little")


def bip143_sighash(txid, vout, script_code, amount, outputs,
                   sequence=0xFFFFFFFD, locktime=0, sighash_type=1) -> bytes:
    """The message a segwit v0 input is signed over, per BIP143.

    One input, so hashPrevouts and hashSequence each cover exactly it.
    """
    outpoint = txid[::-1] + vout.to_bytes(4, "little")
    hash_prevouts = double_sha256(outpoint)
    hash_sequence = double_sha256(sequence.to_bytes(4, "little"))
    serialized_outputs = b"".join(
        amount_.to_bytes(8, "little") + varint(len(script)) + script
        for amount_, script in outputs)
    hash_outputs = double_sha256(serialized_outputs)

    preimage = (2).to_bytes(4, "little")
    preimage += hash_prevouts + hash_sequence + outpoint
    preimage += varint(len(script_code)) + script_code
    preimage += amount.to_bytes(8, "little") + sequence.to_bytes(4, "little")
    preimage += hash_outputs + locktime.to_bytes(4, "little")
    preimage += sighash_type.to_bytes(4, "little")
    return double_sha256(preimage)


# --- PSBT --------------------------------------------------------------------
# BIP174's key/value maps, written and read by hand. Only the records this test
# needs exist here.

PSBT_MAGIC = b"psbt\xff"
PSBT_GLOBAL_UNSIGNED_TX = 0x00
PSBT_IN_WITNESS_UTXO = 0x01
PSBT_IN_PARTIAL_SIG = 0x02
PSBT_IN_BIP32_DERIVATION = 0x06


def _record(key: bytes, value: bytes) -> bytes:
    return varint(len(key)) + key + varint(len(value)) + value


def build_psbt(txid, vout, amount, script_pubkey, outputs,
               signing_public_key, master_fingerprint, derivation_path) -> bytes:
    """An unsigned PSBT for one P2WPKH input the given key can sign.

    The input is fabricated: `txid` names a transaction that does not exist, so
    the UTXO it spends does not exist either and nothing here could be broadcast
    even if somebody tried.
    """
    tx = unsigned_transaction(txid, vout, outputs)
    data = PSBT_MAGIC
    data += _record(bytes([PSBT_GLOBAL_UNSIGNED_TX]), tx)
    data += b"\x00"

    witness_utxo = amount.to_bytes(8, "little") + varint(len(script_pubkey)) + script_pubkey
    derivation = master_fingerprint + b"".join(
        index.to_bytes(4, "little") for index in parse_path(derivation_path))
    data += _record(bytes([PSBT_IN_WITNESS_UTXO]), witness_utxo)
    data += _record(bytes([PSBT_IN_BIP32_DERIVATION]) + signing_public_key, derivation)
    data += b"\x00"

    data += b"\x00"  # the single output's (empty) map
    return data


def _read_varint(data, offset):
    first = data[offset]
    if first < 0xFD:
        return first, offset + 1
    if first == 0xFD:
        return int.from_bytes(data[offset + 1:offset + 3], "little"), offset + 3
    if first == 0xFE:
        return int.from_bytes(data[offset + 1:offset + 5], "little"), offset + 5
    return int.from_bytes(data[offset + 1:offset + 9], "little"), offset + 9


def _read_map(data, offset):
    """One BIP174 map: {key: value} up to its 0x00 separator."""
    records = {}
    while offset < len(data):
        key_len, offset = _read_varint(data, offset)
        if key_len == 0:
            return records, offset
        key = data[offset:offset + key_len]
        offset += key_len
        value_len, offset = _read_varint(data, offset)
        records[key] = data[offset:offset + value_len]
        offset += value_len
    raise ValueError("PSBT map ran off the end")


def read_psbt(data: bytes):
    """(unsigned tx bytes, {public key: signature}) out of a signed PSBT.

    Enough of BIP174 to check what came back: the transaction, so it can be
    compared with the one that went in, and the partial signatures, which are
    what the wallet was asked for.
    """
    if not data.startswith(PSBT_MAGIC):
        raise ValueError("not a PSBT")
    globals_, offset = _read_map(data, len(PSBT_MAGIC))
    inputs, _ = _read_map(data, offset)
    signatures = {key[1:]: value for key, value in inputs.items()
                  if key[:1] == bytes([PSBT_IN_PARTIAL_SIG])}
    return globals_[bytes([PSBT_GLOBAL_UNSIGNED_TX])], signatures


# --- published vectors -------------------------------------------------------

# BIP32's own test vector 1: seed 000102030405060708090a0b0c0d0e0f, and the
# extended public keys the BIP publishes for three nodes of the tree it grows.
# m/0'/1 is the one that matters most here: reaching it correctly needs both a
# hardened and a normal derivation, and its parent fingerprint and child number
# have to be right or the base58 differs.
BIP32_SEED = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
BIP32_VECTOR = [
    ("m", "xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1"
          "Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8"),
    ("m/0'", "xpub68Gmy5EdvgibQVfPdqkBBCHxA5htiqg55crXYuXoQRKfDBFA1WEjWgP6LHhwBZeNK1VTs"
             "fTFUHCdrfp1bgwQ9xv5ski8PX9rL2dZXvgGDnw"),
    ("m/0'/1", "xpub6ASuArnXKPbfEwhqN6e3mwBcDTgzisQN1wXN9BJcM47sSikHjJf3UFHKkNAWbWMiGj7Wf"
               "5uMash7SyYq527Hqck2AxYysAA7xmALppuCkwQ"),
]

# One of BIP39's own vectors, mnemonic to seed, at the passphrase the BIP's test
# file uses. This is what pins the PBKDF2 arguments: 2048 rounds of HMAC-SHA512
# over the mnemonic, salted with "mnemonic" and the passphrase.
BIP39_MNEMONIC = "legal winner thank year wave sausage worth useful legal winner thank yellow"
BIP39_PASSPHRASE = "TREZOR"
BIP39_SEED = ("2e8905819b8723fe2c1d161860e5ee1830318dbf49a83bd451cfb8440c28bd6f"
              "a457fe1296106559a3c80937a1c1069be3a3a5bd381ee6260e8d9739fce1f607")

# The RIPEMD-160 specification's own example.
RIPEMD160_ABC = "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc"


def check_published_vectors():
    """[(what, passed, detail)] for everything in this file that has a published answer.

    Run as real checks by the test rather than as an assert, because the whole
    file is only worth anything if it agrees with values somebody else wrote
    down first. If any of these fail, nothing further in the test means anything.
    """
    results = [("RIPEMD-160 of \"abc\" is the specification's digest",
                ripemd160(b"abc").hex() == RIPEMD160_ABC, ripemd160(b"abc").hex())]

    seed = bip39_seed(BIP39_MNEMONIC, BIP39_PASSPHRASE)
    results.append(("BIP39's own vector reaches the seed the BIP publishes",
                    seed.hex() == BIP39_SEED, seed.hex()[:32] + "..."))

    secret, chain_code = master_key(BIP32_SEED)
    root = Key(secret, chain_code)
    for path, expected in BIP32_VECTOR:
        got = root.derive(path).extended_public_key(VERSION_XPUB)
        results.append((f"BIP32 test vector 1 at {path}", got == expected, got))

    # And that a signature this file makes is one it verifies, so a later
    # "verified" is a statement about the signature rather than about a
    # verifier that says yes to anything. Signed here with a fixed nonce
    # because none of it leaves this function.
    secret = 0x1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF
    message = double_sha256(b"a message nobody will ever broadcast")
    k = 0x0F1E2D3C4B5A69788796A5B4C3D2E1F00F1E2D3C4B5A69788796A5B4C3D2E1F0
    point = point_mul(k)
    r = point[0] % N
    s = pow(k, -1, N) * (int.from_bytes(message, "big") + r * secret) % N
    results.append(("a signature made here verifies here",
                    verify_signature(public_key(secret), message, r, s), ""))
    results.append(("and one bit of it does not",
                    not verify_signature(public_key(secret), message, r, s ^ 1), ""))
    return results


if __name__ == "__main__":
    for name, passed, detail in check_published_vectors():
        print(("  ok   " if passed else "  FAIL ") + name + (f"  {detail}" if detail else ""))
