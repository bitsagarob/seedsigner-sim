// The coordinator, on the page.
//
// A signing device signs; it does not know what a wallet owns, what a fee is,
// or where a transaction goes. That job belongs to a coordinator, and in the
// tutorial the coordinator is the phone: this file is what runs inside it.
//
// It does five things and nothing else:
//
//   * turn account keys exported by the device into a descriptor: three of them
//     into the tutorial's 2 of 3, or one of them into a single signature wallet
//   * derive an address from that descriptor, which means real BIP32 public
//     derivation and real secp256k1 point addition
//   * build a PSBT over the outputs being spent, with everything the device
//     needs in it
//   * put the signatures that come back into a finished transaction
//   * talk to Bitsaga Signet over HTTPS: the faucet, and the proof endpoints
//
// It is written out rather than pulled in because every library that does this
// is far larger than the part of it used here, and a page that claims to be
// checkable should not ship a megabyte of unread JavaScript to derive one
// address. Nothing here is novel: it is BIP32, BIP141, BIP174 and bech32, and
// the values it produces are checked against the wallet's own embit in
// test/test_tutorial.py.
//
// Bitsaga Signet is a signet, so it uses testnet's address prefixes and
// testnet's coin type. None of these coins are real bitcoin.

(function (scope) {
  "use strict";

  var API = "https://signet.bitsaga.be/api";

  // ------------------------------------------------------------ bytes

  function hex(bytes) {
    var out = "";
    for (var i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, "0");
    return out;
  }

  function unhex(text) {
    var out = new Uint8Array(text.length / 2);
    for (var i = 0; i < out.length; i++) out[i] = parseInt(text.substr(i * 2, 2), 16);
    return out;
  }

  function concat(parts) {
    var length = parts.reduce(function (n, p) { return n + p.length; }, 0);
    var out = new Uint8Array(length);
    var at = 0;
    parts.forEach(function (part) { out.set(part, at); at += part.length; });
    return out;
  }

  function fromBase64(text) {
    var raw = atob(text);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function toBase64(bytes) {
    var raw = "";
    for (var i = 0; i < bytes.length; i++) raw += String.fromCharCode(bytes[i]);
    return btoa(raw);
  }

  function u32le(value) {
    return new Uint8Array([value & 0xff, (value >>> 8) & 0xff,
                           (value >>> 16) & 0xff, (value >>> 24) & 0xff]);
  }

  function u64le(value) {
    var out = new Uint8Array(8);
    var big = BigInt(value);
    for (var i = 0; i < 8; i++) {
      out[i] = Number(big & 0xffn);
      big >>= 8n;
    }
    return out;
  }

  function varint(value) {
    if (value < 0xfd) return new Uint8Array([value]);
    if (value <= 0xffff) return new Uint8Array([0xfd, value & 0xff, value >> 8]);
    return concat([new Uint8Array([0xfe]), u32le(value)]);
  }

  // A cursor over a byte string, because everything below reads one.
  function reader(bytes) {
    var at = 0;
    return {
      left: function () { return bytes.length - at; },
      take: function (n) { var out = bytes.subarray(at, at + n); at += n; return out; },
      byte: function () { return bytes[at++]; },
      u32: function () {
        var v = bytes[at] | (bytes[at + 1] << 8) | (bytes[at + 2] << 16) |
                (bytes[at + 3] << 24);
        at += 4;
        return v >>> 0;
      },
      u64: function () {
        var v = 0n;
        for (var i = 7; i >= 0; i--) v = (v << 8n) | BigInt(bytes[at + i]);
        at += 8;
        return v;
      },
      varint: function () {
        var first = bytes[at++];
        if (first < 0xfd) return first;
        if (first === 0xfd) { at += 2; return bytes[at - 2] | (bytes[at - 1] << 8); }
        if (first === 0xfe) return this.u32();
        throw new Error("8 byte lengths are not expected here");
      },
    };
  }

  // ------------------------------------------------------------ hashing

  function sha256(bytes) {
    return crypto.subtle.digest("SHA-256", bytes).then(function (buffer) {
      return new Uint8Array(buffer);
    });
  }

  function sha256d(bytes) {
    return sha256(bytes).then(sha256);
  }

  function hmacSha512(key, data) {
    return crypto.subtle.importKey(
      "raw", key, { name: "HMAC", hash: "SHA-512" }, false, ["sign"]
    ).then(function (handle) {
      return crypto.subtle.sign("HMAC", handle, data);
    }).then(function (buffer) {
      return new Uint8Array(buffer);
    });
  }

  // ------------------------------------------------------------ RIPEMD-160
  //
  // The one hash a single signature address needs and the one hash a browser
  // will not do: crypto.subtle knows SHA-1 and the SHA-2 family and nothing
  // else, so unlike every other digest above it there is nowhere to borrow this
  // from. Two lines of five rounds each over the same sixteen message words,
  // with the word orders, rotations and constants from the specification.
  //
  // A 2 of 3 never needs it, which is why it was not here before: the witness
  // program of a P2WSH is a SHA-256 and nothing more. A P2WPKH pays to the
  // hash160 of a public key, and hash160 is this over a SHA-256.

  var RMD_L = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
    3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
    1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
    4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13];
  var RMD_R = [
    5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
    6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
    15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
    8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
    12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11];
  var RMD_SL = [
    11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
    7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
    11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
    11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
    9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6];
  var RMD_SR = [
    8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
    9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
    9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
    15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
    8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11];
  var RMD_KL = [0x00000000, 0x5a827999, 0x6ed9eba1, 0x8f1bbcdc, 0xa953fd4e];
  var RMD_KR = [0x50a28be6, 0x5c4dd124, 0x6d703ef3, 0x7a6d76e9, 0x00000000];

  function rotl32(value, bits) {
    return ((value << bits) | (value >>> (32 - bits))) >>> 0;
  }

  // The five nonlinear functions, in the order the left line uses them; the
  // right line walks the same five backwards, which is what round 4 - r is.
  function rmdMix(round, x, y, z) {
    if (round === 0) return (x ^ y ^ z) >>> 0;
    if (round === 1) return ((x & y) | (~x & z)) >>> 0;
    if (round === 2) return ((x | ~y) ^ z) >>> 0;
    if (round === 3) return ((x & z) | (y & ~z)) >>> 0;
    return (x ^ (y | ~z)) >>> 0;
  }

  function ripemd160(bytes) {
    var h = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0];
    // The length goes in the last eight bytes little endian, and only the low
    // four of those can be reached by anything this hashes.
    var padded = new Uint8Array((((bytes.length + 8) >> 6) + 1) * 64);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    var bits = bytes.length * 8;
    for (var b = 0; b < 4; b++) padded[padded.length - 8 + b] = (bits >>> (8 * b)) & 0xff;

    var x = new Uint32Array(16);
    for (var block = 0; block < padded.length; block += 64) {
      for (var w = 0; w < 16; w++) {
        x[w] = (padded[block + w * 4] | (padded[block + w * 4 + 1] << 8) |
                (padded[block + w * 4 + 2] << 16) |
                (padded[block + w * 4 + 3] << 24)) >>> 0;
      }
      var al = h[0], bl = h[1], cl = h[2], dl = h[3], el = h[4];
      var ar = h[0], br = h[1], cr = h[2], dr = h[3], er = h[4];
      for (var j = 0; j < 80; j++) {
        var round = j >> 4;
        var t = (rotl32((al + rmdMix(round, bl, cl, dl) + x[RMD_L[j]] +
                         RMD_KL[round]) >>> 0, RMD_SL[j]) + el) >>> 0;
        al = el; el = dl; dl = rotl32(cl, 10); cl = bl; bl = t;
        t = (rotl32((ar + rmdMix(4 - round, br, cr, dr) + x[RMD_R[j]] +
                     RMD_KR[round]) >>> 0, RMD_SR[j]) + er) >>> 0;
        ar = er; er = dr; dr = rotl32(cr, 10); cr = br; br = t;
      }
      var carried = (h[1] + cl + dr) >>> 0;
      h[1] = (h[2] + dl + er) >>> 0;
      h[2] = (h[3] + el + ar) >>> 0;
      h[3] = (h[4] + al + br) >>> 0;
      h[4] = (h[0] + bl + cr) >>> 0;
      h[0] = carried;
    }
    var out = new Uint8Array(20);
    for (var o = 0; o < 5; o++) {
      out[o * 4] = h[o] & 0xff;
      out[o * 4 + 1] = (h[o] >>> 8) & 0xff;
      out[o * 4 + 2] = (h[o] >>> 16) & 0xff;
      out[o * 4 + 3] = (h[o] >>> 24) & 0xff;
    }
    return out;
  }

  function hash160(bytes) {
    return sha256(bytes).then(ripemd160);
  }

  // ------------------------------------------------------------ base58

  var B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

  function base58Decode(text) {
    var value = 0n;
    for (var i = 0; i < text.length; i++) {
      var digit = B58.indexOf(text[i]);
      if (digit < 0) throw new Error("not base58: " + text[i]);
      value = value * 58n + BigInt(digit);
    }
    var body = [];
    while (value > 0n) {
      body.unshift(Number(value & 0xffn));
      value >>= 8n;
    }
    for (var z = 0; z < text.length && text[z] === "1"; z++) body.unshift(0);
    return Uint8Array.from(body);
  }

  function base58Encode(bytes) {
    var value = 0n;
    for (var i = 0; i < bytes.length; i++) value = (value << 8n) | BigInt(bytes[i]);
    var out = "";
    while (value > 0n) {
      out = B58[Number(value % 58n)] + out;
      value /= 58n;
    }
    for (var z = 0; z < bytes.length && bytes[z] === 0; z++) out = "1" + out;
    return out;
  }

  function base58CheckDecode(text) {
    var raw = base58Decode(text);
    return sha256d(raw.subarray(0, raw.length - 4)).then(function (digest) {
      for (var i = 0; i < 4; i++) {
        if (digest[i] !== raw[raw.length - 4 + i]) throw new Error("bad base58 checksum");
      }
      return raw.subarray(0, raw.length - 4);
    });
  }

  function base58CheckEncode(payload) {
    return sha256d(payload).then(function (digest) {
      return base58Encode(concat([payload, digest.subarray(0, 4)]));
    });
  }

  // ------------------------------------------------------------ bech32

  var BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";

  function bech32Polymod(values) {
    var generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
    var check = 1;
    values.forEach(function (value) {
      var top = check >>> 25;
      check = ((check & 0x1ffffff) << 5) ^ value;
      for (var i = 0; i < 5; i++) if ((top >>> i) & 1) check ^= generator[i];
    });
    return check;
  }

  function bech32Address(hrp, program) {
    var data = [0];  // witness version 0
    var bits = 0, value = 0;
    for (var i = 0; i < program.length; i++) {
      value = (value << 8) | program[i];
      bits += 8;
      while (bits >= 5) {
        bits -= 5;
        data.push((value >> bits) & 31);
      }
    }
    // 32 bytes is 256 bits, which is not a whole number of five bit groups, so
    // the last one is padded rather than dropped.
    if (bits > 0) data.push((value << (5 - bits)) & 31);
    var expanded = [];
    for (var h = 0; h < hrp.length; h++) expanded.push(hrp.charCodeAt(h) >> 5);
    expanded.push(0);
    for (var l = 0; l < hrp.length; l++) expanded.push(hrp.charCodeAt(l) & 31);

    var polymod = bech32Polymod(expanded.concat(data).concat([0, 0, 0, 0, 0, 0])) ^ 1;
    var checksum = [];
    for (var c = 0; c < 6; c++) checksum.push((polymod >> (5 * (5 - c))) & 31);

    return hrp + "1" + data.concat(checksum).map(function (d) { return BECH32[d]; }).join("");
  }

  // ------------------------------------------------------------ secp256k1
  //
  // Only what public derivation needs: decompress a point, add two, and
  // multiply the generator by a scalar. Affine coordinates and a modular
  // inverse per addition, which is slow in principle and irrelevant here:
  // deriving one address is a few hundred of these.

  var P = 0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffefffffc2fn;
  var N = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141n;
  var GX = 0x79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798n;
  var GY = 0x483ada7726a3c4655da4fbfc0e1108a8fd17b448a68554199c47d08ffb10d4b8n;

  function mod(a, m) {
    var r = a % m;
    return r < 0n ? r + m : r;
  }

  function modInverse(a, m) {
    var old = mod(a, m), current = m;
    var oldCoefficient = 1n, coefficient = 0n;
    while (current !== 0n) {
      var quotient = old / current;
      var t = old - quotient * current;
      old = current; current = t;
      t = oldCoefficient - quotient * coefficient;
      oldCoefficient = coefficient; coefficient = t;
    }
    if (old !== 1n) throw new Error("not invertible");
    return mod(oldCoefficient, m);
  }

  function modPow(base, exponent, m) {
    var result = 1n, b = mod(base, m), e = exponent;
    while (e > 0n) {
      if (e & 1n) result = (result * b) % m;
      b = (b * b) % m;
      e >>= 1n;
    }
    return result;
  }

  function pointAdd(a, b) {
    if (!a) return b;
    if (!b) return a;
    var slope;
    if (a.x === b.x) {
      if (mod(a.y + b.y, P) === 0n) return null;
      slope = mod(3n * a.x * a.x * modInverse(2n * a.y, P), P);
    } else {
      slope = mod((b.y - a.y) * modInverse(b.x - a.x, P), P);
    }
    var x = mod(slope * slope - a.x - b.x, P);
    return { x: x, y: mod(slope * (a.x - x) - a.y, P) };
  }

  function generatorTimes(scalar) {
    var result = null;
    var addend = { x: GX, y: GY };
    var k = scalar;
    while (k > 0n) {
      if (k & 1n) result = pointAdd(result, addend);
      addend = pointAdd(addend, addend);
      k >>= 1n;
    }
    return result;
  }

  function decompress(bytes) {
    var x = 0n;
    for (var i = 1; i < 33; i++) x = (x << 8n) | BigInt(bytes[i]);
    // p is 3 mod 4, so the square root is one exponentiation.
    var y = modPow(mod(x * x * x + 7n, P), (P + 1n) / 4n, P);
    if ((y & 1n) !== BigInt(bytes[0] & 1)) y = P - y;
    return { x: x, y: y };
  }

  function compress(point) {
    var out = new Uint8Array(33);
    out[0] = (point.y & 1n) ? 3 : 2;
    var x = point.x;
    for (var i = 32; i >= 1; i--) {
      out[i] = Number(x & 0xffn);
      x >>= 8n;
    }
    return out;
  }

  function toBigInt(bytes) {
    var value = 0n;
    for (var i = 0; i < bytes.length; i++) value = (value << 8n) | BigInt(bytes[i]);
    return value;
  }

  // ------------------------------------------------------------ BIP32

  var TPUB_VERSION = unhex("043587cf");

  /** Split an extended public key into its parts. */
  function parseExtendedKey(text) {
    return base58CheckDecode(text).then(function (raw) {
      if (raw.length !== 78) throw new Error("an extended key is 78 bytes, not " + raw.length);
      return { chainCode: raw.subarray(13, 45), key: raw.subarray(45, 78), raw: raw };
    });
  }

  /**
   * Rewrite a SLIP-132 key (Vpub, the multisig-native-segwit flavour SeedSigner
   * exports) as a plain tpub. Same key, different four leading bytes: in a
   * descriptor the script type is already stated by wsh(...), so a version byte
   * that says it again is redundant information that could disagree.
   */
  function toTpub(text) {
    return base58CheckDecode(text).then(function (raw) {
      return base58CheckEncode(concat([TPUB_VERSION, raw.subarray(4)]));
    });
  }

  /** One step of unhardened public derivation. */
  function deriveOne(parent, index) {
    var data = concat([parent.key, u32le(index).reverse()]);
    return hmacSha512(parent.chainCode, data).then(function (I) {
      var tweak = toBigInt(I.subarray(0, 32));
      if (tweak >= N) throw new Error("derivation landed outside the curve order");
      var child = pointAdd(generatorTimes(tweak), decompress(parent.key));
      if (!child) throw new Error("derivation landed on the point at infinity");
      return { key: compress(child), chainCode: I.subarray(32, 64) };
    });
  }

  function derivePath(parent, path) {
    return path.reduce(function (chain, index) {
      return chain.then(function (node) { return deriveOne(node, index); });
    }, Promise.resolve(parent));
  }

  // ------------------------------------------------------------ the wallet

  // What SeedSigner puts in the QR when it exports a multisig account key:
  // [fingerprint/48'/1'/0'/2']Vpub..., the account itself and where it came
  // from. Both halves matter: the coordinator needs the path to tell the device
  // later which key of the three is its own.
  var XPUB_LINE = /^\[([0-9a-fA-F]{8})((?:\/\d+['h]?)+)\]([A-Za-z0-9]+)$/;

  function parseExportedKey(text) {
    var found = XPUB_LINE.exec(text.trim());
    if (!found) throw new Error("that is not an exported account key");
    return {
      fingerprint: found[1].toLowerCase(),
      path: found[2].replace(/'/g, "h"),
      key: found[3],
    };
  }

  function pathToIndices(path) {
    return path.split("/").filter(Boolean).map(function (part) {
      var hardened = /[h']$/.test(part);
      return (parseInt(part, 10) + (hardened ? 0x80000000 : 0)) >>> 0;
    });
  }

  /**
   * The 2 of 3 itself: three exported keys in, a descriptor and the machinery
   * to derive addresses from it out.
   *
   * sortedmulti rather than multi, because then the three cosigners do not have
   * to agree on an order: every one of them sorts the keys of each address the
   * same way, so any of them derives the same address from the same three keys.
   */
  function buildWallet(exportedKeys) {
    var parsed = exportedKeys.map(parseExportedKey);
    return Promise.all(parsed.map(function (key) { return toTpub(key.key); }))
      .then(function (tpubs) {
        var keys = parsed.map(function (key, i) {
          return { fingerprint: key.fingerprint, path: key.path, tpub: tpubs[i] };
        });
        var descriptor = "wsh(sortedmulti(2," + keys.map(function (key) {
          return "[" + key.fingerprint + key.path + "]" + key.tpub + "/{0,1}/*";
        }).join(",") + "))";
        return { keys: keys, descriptor: descriptor, threshold: 2 };
      });
  }

  /**
   * One address of that wallet, with everything a PSBT will need about it:
   * the witness script it pays into, the script pubkey itself, and which key
   * of each cosigner is in it.
   */
  function deriveAddress(wallet, branch, index) {
    return Promise.all(wallet.keys.map(function (key) {
      return parseExtendedKey(key.tpub).then(function (account) {
        return derivePath(account, [branch, index]).then(function (leaf) {
          return { fingerprint: key.fingerprint, path: key.path, pubkey: leaf.key };
        });
      });
    })).then(function (leaves) {
      // sortedmulti: lexicographic over the compressed keys, which is what
      // every cosigner does, so they all reach the same script.
      var sorted = leaves.slice().sort(function (a, b) {
        return hex(a.pubkey) < hex(b.pubkey) ? -1 : 1;
      });
      var script = concat([new Uint8Array([0x52])]   // OP_2
        .concat(sorted.map(function (leaf) {
          return concat([new Uint8Array([33]), leaf.pubkey]);
        }))
        .concat([new Uint8Array([0x53, 0xae])]));    // OP_3 OP_CHECKMULTISIG
      return sha256(script).then(function (program) {
        return {
          branch: branch, index: index,
          cosigners: sorted,
          witnessScript: script,
          scriptPubkey: concat([new Uint8Array([0x00, 0x20]), program]),
          address: bech32Address("tb", program),
        };
      });
    });
  }

  // ------------------------------------------- the single signature wallet

  /**
   * Just enough CBOR to read an exported account: unsigned integers, byte
   * strings, text, arrays, maps, tags and the two booleans.
   *
   * A map comes back as a plain object under its integer keys, which is all a
   * crypto-account and a crypto-hdkey have, and a tag as { tag, value },
   * because in a UR the tag is what says which of them a thing is.
   */
  function cborItem(read) {
    var first = read.byte();
    var major = first >> 5, extra = first & 31;
    var value = extra;
    if (extra === 24) value = read.byte();
    else if (extra === 25) value = (read.byte() << 8) | read.byte();
    else if (extra === 26) {
      value = 0;
      for (var b = 0; b < 4; b++) value = (value * 256) + read.byte();
    } else if (extra > 26) throw new Error("that CBOR is wider than an account key");

    if (major === 0) return value;
    if (major === 2) return read.take(value);
    if (major === 3) return String.fromCharCode.apply(null, read.take(value));
    if (major === 4) {
      var list = [];
      for (var i = 0; i < value; i++) list.push(cborItem(read));
      return list;
    }
    if (major === 5) {
      var map = {};
      for (var k = 0; k < value; k++) {
        var key = cborItem(read);
        map[key] = cborItem(read);
      }
      return map;
    }
    if (major === 6) return { tag: value, value: cborItem(read) };
    if (major === 7 && (value === 20 || value === 21)) return value === 21;
    throw new Error("unexpected CBOR in that account key");
  }

  /**
   * The crypto-hdkey buried in whatever the device exported.
   *
   * A crypto-account holds a list of output descriptors, and each of those is
   * the same key wrapped in the tags that say which script type it is meant
   * for. Which script type is the coordinator's choice here, not the device's,
   * so only the key is taken: this file makes P2WPKH addresses out of it. A
   * bare crypto-hdkey is that key with nothing around it.
   */
  function hdkeyIn(node) {
    if (node && node.tag !== undefined) return hdkeyIn(node.value);
    if (Array.isArray(node)) return hdkeyIn(node[0]);
    if (node && node[3] instanceof Uint8Array && node[4] instanceof Uint8Array) return node;
    if (node && Array.isArray(node[2])) return hdkeyIn(node[2]);
    throw new Error("there is no account key in that payload");
  }

  /** A crypto-hdkey's origin as a path: [84, true, 1, true, 0, true] is /84h/1h/0h. */
  function componentsToPath(components) {
    var path = "";
    for (var i = 0; i < components.length; i += 2) {
      path += "/" + components[i] + (components[i + 1] ? "h" : "");
    }
    return path;
  }

  /**
   * The account inside a UR payload, rebuilt as the tpub the rest of this file
   * works in.
   *
   * A crypto-hdkey carries the pieces of an extended key rather than the key
   * itself, so they go back together here in BIP32's own order: version, depth,
   * the parent's fingerprint, the child number this node was derived at, the
   * chain code, the key. Get the last two of those first three wrong and the
   * base58 differs from every other wallet's while the addresses still come out
   * right, which is a disagreement nobody notices until they try to restore.
   */
  function accountFromCbor(payload) {
    var hdkey = hdkeyIn(cborItem(reader(payload)));
    var origin = hdkey[6];                       // 6 is the origin keypath
    if (origin && origin.tag !== undefined) origin = origin.value;
    if (!origin || !Array.isArray(origin[1]) || origin[2] === undefined) {
      throw new Error("that account key does not say which seed it came from");
    }
    var components = origin[1];
    var depth = origin[3] === undefined ? components.length / 2 : origin[3];
    var child = components.length
      ? (components[components.length - 2] +
         (components[components.length - 1] ? 0x80000000 : 0)) >>> 0
      : 0;
    return base58CheckEncode(concat([
      TPUB_VERSION, new Uint8Array([depth]),
      u32le(hdkey[8] || 0).reverse(),            // 8 is the parent's fingerprint
      u32le(child).reverse(),
      hdkey[4], hdkey[3],                        // 4 is the chain code, 3 the key
    ])).then(function (tpub) {
      return {
        fingerprint: hex(u32le(origin[2]).reverse()),
        path: componentsToPath(components),
        tpub: tpub,
      };
    });
  }

  /**
   * One exported account, however the device wrote it down.
   *
   * Export Xpub gives either the line the multisig flow uses, which is the same
   * shape whatever the script type, or a UR: a crypto-account when the device
   * animates it, a crypto-hdkey inside it. The UR case takes the payload the UR
   * carries rather than the ur:... text, because a crypto-account rarely fits
   * in one frame and putting the parts of an animated one back together is
   * ur-decode.js's job, not this file's.
   */
  function parseAccount(exported) {
    if (typeof exported !== "string") return accountFromCbor(exported);
    if (/^ur:/i.test(exported.trim())) {
      throw new Error("collect the parts of that UR first and pass what it carries");
    }
    var key = parseExportedKey(exported);
    return toTpub(key.key).then(function (tpub) {
      return { fingerprint: key.fingerprint, path: key.path, tpub: tpub };
    });
  }

  /**
   * One account key is a whole wallet, shaped like the 2 of 3 above so that
   * everything downstream of it does not have to care which it has.
   *
   * wpkh rather than wsh: there is no script here at all. The output pays a key
   * hash directly, which is why nothing in this wallet has a witness script to
   * carry around and why the descriptor has no threshold in it.
   */
  function singleSigWallet(account) {
    return {
      keys: [account],
      descriptor: "wpkh([" + account.fingerprint + account.path + "]"
        + account.tpub + "/{0,1}/*)",
      threshold: 1,
    };
  }

  /**
   * One address of that wallet, with everything a PSBT will need about it.
   *
   * The witness program of a P2WPKH is the hash160 of the public key, twenty
   * bytes where a P2WSH has thirty-two, and the address is the same bech32
   * encoding of it under the same testnet prefix. There is nothing to hand the
   * device beyond the key: it rebuilds the script it signs over out of the
   * program itself, which is what BIP143 means by the scriptCode of a P2WPKH.
   */
  function deriveAddressSingle(wallet, branch, index) {
    var account = wallet.keys[0];
    return parseExtendedKey(account.tpub).then(function (node) {
      return derivePath(node, [branch, index]);
    }).then(function (leaf) {
      return hash160(leaf.key).then(function (program) {
        return {
          branch: branch, index: index,
          fingerprint: account.fingerprint,
          path: account.path + "/" + branch + "/" + index,
          pubkey: leaf.key,
          scriptPubkey: concat([new Uint8Array([0x00, 0x14]), program]),
          address: bech32Address("tb", program),
        };
      });
    });
  }

  // ------------------------------------------------------------ transactions

  /** The outputs of a raw transaction, enough to find which one paid us. */
  function transactionOutputs(rawHex) {
    var bytes = unhex(rawHex);
    var read = reader(bytes);
    read.u32();                                    // version
    if (bytes[4] === 0x00) { read.byte(); read.byte(); }   // the segwit marker and flag
    var inputs = read.varint();
    for (var i = 0; i < inputs; i++) {
      read.take(36);
      read.take(read.varint());
      read.u32();
    }
    var outputs = [];
    var count = read.varint();
    for (var o = 0; o < count; o++) {
      var value = read.u64();
      outputs.push({ index: o, value: value, script: hex(read.take(read.varint())) });
    }
    return outputs;
  }

  /**
   * Version 2, no witness, every input at the same sequence: the transaction a
   * PSBT carries in its global map, and the transaction whose id survives being
   * signed.
   *
   * The inputs go out in the order they arrive and nothing sorts them, because
   * BIP174 is positional: the second input map describes the second input here,
   * the second witness will finish it, and a coordinator that reordered any one
   * of those three would have the device sign one output and spend another.
   */
  function serialiseTx(inputs, outputs) {
    return concat([u32le(2), varint(inputs.length)]
      .concat(inputs.map(function (input) {
        return concat([unhex(input.txid).reverse(), u32le(input.vout),
                       varint(0), u32le(0xfffffffd)]);
      }))
      .concat([varint(outputs.length)])
      .concat(outputs.map(function (out) {
        return concat([u64le(out.value), varint(out.script.length), out.script]);
      }))
      .concat([u32le(0)]));
  }

  function serialiseUnsigned(input, outputs) {
    return serialiseTx([input], outputs);
  }

  function keyPair(key, value) {
    return concat([varint(key.length), key, varint(value.length), value]);
  }

  /**
   * A PSBT spending one output of this wallet, paying one address.
   *
   * Everything the device cannot know goes in: what the output being spent is
   * worth (a signer has no chain to look it up on, and BIP143 signs the value),
   * the script that output pays into, and which key of each cosigner appears in
   * that script with the path it came from, so the device can find its own.
   */
  function buildPsbt(input, source, destination, amount) {
    var unsigned = serialiseUnsigned(input, [{ value: amount, script: destination }]);
    var inputMap = [
      keyPair(new Uint8Array([0x01]),
              concat([u64le(input.value), varint(source.scriptPubkey.length),
                      source.scriptPubkey])),
      keyPair(new Uint8Array([0x05]), source.witnessScript),
    ];
    source.cosigners.forEach(function (leaf) {
      var indices = pathToIndices(leaf.path).concat([source.branch, source.index]);
      inputMap.push(keyPair(
        concat([new Uint8Array([0x06]), leaf.pubkey]),
        concat([unhex(leaf.fingerprint)].concat(indices.map(u32le)))));
    });
    return concat([
      unhex("70736274ff"),                                   // "psbt" and 0xff
      keyPair(new Uint8Array([0x00]), unsigned),
      new Uint8Array([0x00]),                                 // end of the globals
      concat(inputMap), new Uint8Array([0x00]),               // the one input
      new Uint8Array([0x00]),                                 // the one output
    ]);
  }

  /**
   * How big the finished transaction will be, before it exists.
   *
   * A fee rate is only a fee once there is a size to multiply it by, and a
   * P2WPKH spend's size is known in advance because every part of it is fixed
   * length. Per input: 36 bytes of outpoint, an empty scriptSig, 4 of sequence,
   * all of which weigh four times over, and a witness of a signature and a
   * public key, which weighs once. Per output: 8 of value and the script with
   * its length. Around all of them: version, the two counts, locktime, and the
   * marker and flag, which are witness bytes.
   *
   * The signature is taken at its 72 byte maximum. A low-S DER signature is
   * almost always 71, so this reads about a quarter of a virtual byte per input
   * high: a fee a shade over is a transaction that confirms, and a fee a shade
   * under is one that sits in nobody's mempool.
   */
  function estimateVsize(inputCount, scripts) {
    var paid = scripts.reduce(function (n, script) {
      return n + 8 + varint(script.length).length + script.length;
    }, 0);
    var base = 4 + varint(inputCount).length + inputCount * 41
      + varint(scripts.length).length + paid + 4;
    var witness = 2 + inputCount * (1 + 1 + 72 + 1 + 33);
    return Math.ceil((base * 4 + witness) / 4);
  }

  // What Bitcoin Core will not relay: an output worth less than a third of what
  // spending it would cost at the dust relay fee, which for a P2WPKH output is
  // 294 satoshis.
  var DUST = 294n;

  /**
   * A PSBT spending several outputs of a single signature wallet, paying
   * several addresses, with the fee worked out from a rate.
   *
   *   inputs:   [{ txid, vout, value, source }], source from deriveAddressSingle
   *   outputs:  [{ value, script }]
   *   change:   an address from deriveAddressSingle, or nothing
   *   feeRate:  satoshis per virtual byte
   *
   * Each input carries what the device cannot look up: what the output being
   * spent is worth, because BIP143 signs that value, and the script it pays
   * into, which is where the device gets the key hash it rebuilds the signed
   * script from. And each carries its own derivation, so a device holding the
   * seed can tell which of its keys this input is for.
   *
   * Nothing here states a sighash type. Left out, it means SIGHASH_ALL, which
   * is the only thing any of this wants; written down, it is one more field for
   * the device to disagree with.
   */
  function buildPsbtSingle(spend) {
    var inputs = spend.inputs;
    var outputs = spend.outputs.map(function (out) {
      return { value: BigInt(out.value), script: out.script };
    });
    var funded = inputs.reduce(function (total, input) {
      return total + BigInt(input.value);
    }, 0n);
    var paying = outputs.reduce(function (total, out) { return total + out.value; }, 0n);
    var changeAt = -1;
    if (funded < paying) throw new Error("these inputs do not cover that spend");

    if (spend.change) {
      var scripts = outputs.map(function (out) { return out.script; })
        .concat([spend.change.scriptPubkey]);
      var fee = BigInt(Math.ceil(estimateVsize(inputs.length, scripts) * spend.feeRate));
      var left = funded - paying - fee;
      if (left < 0n) throw new Error("these inputs do not cover that spend and its fee");
      // Change worth less than it would cost to spend is change nobody ever
      // moves, and an output the network may refuse to carry at all. Every real
      // wallet drops it and lets the fee have it, which is what happens here.
      if (left >= DUST) {
        changeAt = outputs.length;
        outputs.push({ value: left, script: spend.change.scriptPubkey });
      }
    }

    var maps = [keyPair(new Uint8Array([0x00]), serialiseTx(inputs, outputs)),
                new Uint8Array([0x00])];               // end of the globals
    inputs.forEach(function (input) {
      var source = input.source;
      maps.push(keyPair(new Uint8Array([0x01]),
                        concat([u64le(input.value), varint(source.scriptPubkey.length),
                                source.scriptPubkey])));
      maps.push(keyPair(concat([new Uint8Array([0x06]), source.pubkey]),
                        concat([unhex(source.fingerprint)]
                          .concat(pathToIndices(source.path).map(u32le)))));
      maps.push(new Uint8Array([0x00]));
    });
    outputs.forEach(function (out, at) {
      // The change output says whose it is, because a device that cannot
      // recognise an output as its own has to treat it as money leaving, and
      // would ask the visitor to approve sending the whole input away.
      if (at === changeAt) {
        // 0x02 here, not the 0x06 the input map above uses. BIP174 numbers the
        // key types per map: 0x06 is PSBT_IN_BIP32_DERIVATION in an input and
        // PSBT_OUT_TAP_TREE in an output, so writing 0x06 here does not fail,
        // it silently files the derivation under taproot. The device then finds
        // no derivation on the output, cannot recognise the change as its own,
        // and warns the visitor that the whole input is leaving. It signs
        // correctly and the change does come back, which is what made this
        // survive every test that looked at the chain instead of the screen.
        maps.push(keyPair(concat([new Uint8Array([0x02]), spend.change.pubkey]),
                          concat([unhex(spend.change.fingerprint)]
                            .concat(pathToIndices(spend.change.path).map(u32le)))));
      }
      maps.push(new Uint8Array([0x00]));
    });
    return toBase64(concat([unhex("70736274ff")].concat(maps)));
  }

  /**
   * Every key/value map in a PSBT, in BIP174's order: the globals, then one per
   * input, then one per output.
   */
  function psbtMaps(psbtBase64) {
    var read = reader(fromBase64(psbtBase64));
    if (hex(read.take(5)) !== "70736274ff") throw new Error("that is not a PSBT");
    var maps = [];
    while (read.left() > 0) {
      var map = [];
      for (;;) {
        var keyLength = read.varint();
        if (keyLength === 0) break;
        var key = read.take(keyLength);
        map.push({ key: key, value: read.take(read.varint()) });
      }
      maps.push(map);
    }
    return maps;
  }

  /** Every partial signature in a PSBT's first input, by public key. */
  function partialSignatures(psbtBase64) {
    var maps = psbtMaps(psbtBase64);
    var signatures = {};
    (maps[1] || []).forEach(function (entry) {
      if (entry.key[0] === 0x02) signatures[hex(entry.key.subarray(1))] = entry.value;
    });
    return signatures;
  }

  /**
   * Two signatures and the wallet's own script make a spendable transaction.
   *
   * The witness of a P2WSH multisig is the empty item CHECKMULTISIG pops and
   * throws away, then one signature per key *in the order the keys appear in
   * the script*, then the script itself. Signatures out of that order fail, and
   * because this is sortedmulti the order is the sorted one.
   */
  function finalise(input, source, destination, amount, signatures) {
    var wanted = source.cosigners
      .map(function (leaf) { return signatures[hex(leaf.pubkey)]; })
      .filter(Boolean);
    if (wanted.length < 2) {
      throw new Error("a 2 of 3 needs two signatures, and this has " + wanted.length);
    }
    var witness = [new Uint8Array([0])]
      .concat(wanted.slice(0, 2).map(function (signature) {
        return concat([varint(signature.length), signature]);
      }))
      .concat([concat([varint(source.witnessScript.length), source.witnessScript])]);

    var body = serialiseUnsigned(input, [{ value: amount, script: destination }]);
    // The same bytes with a marker, a flag and the witness spliced in: version,
    // then 0x00 0x01, then everything from the input count to just before the
    // locktime, then the witness, then the locktime.
    var signed = concat([
      body.subarray(0, 4), new Uint8Array([0x00, 0x01]),
      body.subarray(4, body.length - 4),
      varint(witness.length), concat(witness),
      body.subarray(body.length - 4),
    ]);
    // A transaction's id has never covered its witness, which is what lets two
    // different signatures over the same spend share one id.
    return sha256d(body).then(function (digest) {
      return { hex: hex(signed), txid: hex(digest.reverse()) };
    });
  }

  /**
   * One signature per input, and the transaction is finished.
   *
   * The witness of a P2WPKH input is two items and always the same two: the
   * signature the device made, and the public key it made it with. The key is
   * what the output being spent committed to, and the signature is what says
   * this spend of it was allowed.
   *
   * The transaction comes back out of the PSBT rather than being built a second
   * time from the inputs, because what is broadcast has to be what was signed,
   * byte for byte, and the PSBT holds the only copy of it both sides saw. The
   * witnesses go in in input order for the same reason the inputs went out in
   * the order they arrived.
   */
  function finaliseSingle(psbtBase64, inputs) {
    var maps = psbtMaps(psbtBase64);
    var unsigned = null;
    maps[0].forEach(function (entry) {
      if (entry.key.length === 1 && entry.key[0] === 0x00) unsigned = entry.value;
    });
    if (!unsigned) throw new Error("that PSBT does not carry a transaction");

    var witnesses = inputs.map(function (input, at) {
      var pubkey = input.source.pubkey;
      var wanted = hex(pubkey);
      var signature = null;
      (maps[1 + at] || []).forEach(function (entry) {
        if (entry.key[0] === 0x02 && hex(entry.key.subarray(1)) === wanted) {
          signature = entry.value;
        }
      });
      if (!signature) throw new Error("input " + at + " came back without a signature");
      return concat([varint(2), varint(signature.length), signature,
                     varint(pubkey.length), pubkey]);
    });

    // The same bytes with a marker, a flag and the witnesses spliced in, as in
    // finalise() above: version, then 0x00 0x01, then everything up to the
    // locktime, then the witnesses, then the locktime.
    return hex(concat([
      unsigned.subarray(0, 4), new Uint8Array([0x00, 0x01]),
      unsigned.subarray(4, unsigned.length - 4),
      concat(witnesses),
      unsigned.subarray(unsigned.length - 4),
    ]));
  }

  // ------------------------------------------------------------ the network

  // A request that never answers would leave the tutorial waiting forever with
  // nothing on screen to say so, which is worse than a refusal. Twenty seconds
  // is far longer than any of these calls takes and short enough to be a
  // failure a visitor can act on.
  var PATIENCE = 20000;

  function request(path, options) {
    var settings = Object.assign({ cache: "no-store" }, options || {});
    var giveUp = new AbortController();
    var timer = setTimeout(function () { giveUp.abort(); }, PATIENCE);
    settings.signal = giveUp.signal;
    return fetch(API + path, settings).then(function (response) {
      clearTimeout(timer);
      return response;
    }, function () {
      clearTimeout(timer);
      throw new Error(giveUp.signal.aborted
        ? "Bitsaga Signet did not answer in time"
        : "Bitsaga Signet is not reachable from this browser");
    }).then(function (response) {
      return response.json().catch(function () {
        throw new Error("Bitsaga Signet answered with something that is not JSON");
      }).then(function (body) {
        if (!response.ok) {
          var reason = new Error(body.error || ("Bitsaga Signet said " + response.status));
          reason.status = response.status;
          throw reason;
        }
        return body;
      });
    });
  }

  var network = {
    status: function () { return request("/status"); },

    claim: function (address) {
      return request("/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address: address }),
      });
    },

    // 404 until it is in a block, which is exactly what "confirmed" means, so
    // this is the confirmation check as well as the proof.
    proof: function (txid) {
      return request("/tx-proof?txid=" + encodeURIComponent(txid));
    },

    broadcast: function (rawHex) {
      return request("/broadcast", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tx: rawHex }),
      });
    },
  };

  scope.SignetCoordinator = {
    api: API,
    hex: hex, unhex: unhex, toBase64: toBase64, fromBase64: fromBase64,
    buildWallet: buildWallet,
    deriveAddress: deriveAddress,
    parseAccount: parseAccount,
    singleSigWallet: singleSigWallet,
    deriveAddressSingle: deriveAddressSingle,
    transactionOutputs: transactionOutputs,
    buildPsbt: buildPsbt,
    buildPsbtSingle: buildPsbtSingle,
    partialSignatures: partialSignatures,
    finalise: finalise,
    finaliseSingle: finaliseSingle,
    network: network,
  };
})(typeof self !== "undefined" ? self : this);
