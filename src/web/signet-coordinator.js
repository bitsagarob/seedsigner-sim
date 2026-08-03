// The coordinator, on the page.
//
// A signing device signs; it does not know what a wallet owns, what a fee is,
// or where a transaction goes. That job belongs to a coordinator, and in the
// tutorial the coordinator is the phone: this file is what runs inside it.
//
// It does five things and nothing else:
//
//   * turn three account keys exported by the device into one 2 of 3 descriptor
//   * derive an address from that descriptor, which means real BIP32 public
//     derivation and real secp256k1 point addition
//   * build a PSBT spending one output, with everything the device needs in it
//   * put two partial signatures from the device into a finished transaction
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

  function serialiseUnsigned(input, outputs) {
    return concat([
      u32le(2),
      varint(1),
      unhex(input.txid).reverse(), u32le(input.vout), varint(0), u32le(0xfffffffd),
      varint(outputs.length),
    ].concat(outputs.map(function (out) {
      return concat([u64le(out.value), varint(out.script.length), out.script]);
    })).concat([u32le(0)]));
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

  /** Every partial signature in a PSBT's first input, by public key. */
  function partialSignatures(psbtBase64) {
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
    transactionOutputs: transactionOutputs,
    buildPsbt: buildPsbt,
    partialSignatures: partialSignatures,
    finalise: finalise,
    network: network,
  };
})(typeof self !== "undefined" ? self : this);
