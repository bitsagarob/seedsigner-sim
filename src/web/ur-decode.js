// Reading the animated QRs a SeedSigner shows.
//
// Neither a signed transaction nor an exported account key fits in one QR, so
// the device emits a ur:crypto-psbt or a ur:crypto-account in parts and cycles
// through them for ever. The coordinator on the phone has to collect them and
// put the message back together, which is what a real coordinator does with a
// real device.
//
// Nothing below cares which of them it is holding: the parts of any UR are
// numbered, mixed and checksummed the same way, so the type is read off the
// first part, carried along, and handed back with the payload for the caller to
// make sense of. Hardcoding one type here is what made the device's own default
// export unreadable, and there was never anything type-specific to hardcode.
//
// The part that has to be got right is the fountain code. The first N parts a
// device emits are the plain fragments of the message, in order, and everything
// after that is a *mixture*: several fragments XORed together, with which ones
// decided by a seeded random number generator that both ends run. Collecting
// only the plain fragments would work exactly once, on a first pass where not a
// single frame was missed, and then never again, because the plain fragments
// never come round a second time. Missing one frame of sixteen is not unlikely,
// and if that were the failure mode the last step of the tutorial would work
// most of the time, which is worse than not working.
//
// So this decodes the mixtures too: the same xoshiro256** generator, the same
// alias-method sampler and the same Fisher-Yates shuffle the encoder uses to
// choose what went into each one, and then XOR the known fragments back out
// until every fragment is known. That is the whole of the UR fountain scheme,
// and it is why a part that arrives late still helps.
//
// The bytewords alphabet and the generator are from the UR specification. Only
// the two letters per word that the minimal encoding uses are kept here.
// test/test_tutorial.py checks all of it against the firmware's own encoder,
// including a run that is given nothing but mixtures.

(function (scope) {
  "use strict";

  var MINIMAL =
    "aeadaoaxaaahamatayasbkbdbnbtbabsbebybgbwbbbzcmchcscfcycwcecackct" +
    "cxclcpcndkdadsdidedtdrdndwdpdmdldyeheyeoeeecenemetesftfrfnfsfmfh" +
    "fzfpfwfxfyfefgflfdgagegrgsgtglgwgdgygmgughgohfhghdhkhthphhhlhyhe" +
    "hnhsidiaieihiyioisinimjejzjnjtjljojsjpjkjykpkoktkskkknkgkekikblb" +
    "lalylflslrlplnltloldlelulklgmnmymhmemomumwmdmtmsmknlnyndnsntnnne" +
    "nboyoeotoxonolospdptpkpypspmplpepfpaprqdqzrerprlrorhrdrkrfryrnrs" +
    "rtsesasrssskswstspsosgsbsfsntotktitttdtetytltbtstptatnuyuoutueur" +
    "vtvyvovlvevwvavdvswlwdwmwpwewywswtwnwzwfwkykynylyaytzszoztzczezm";

  var LOOKUP = {};
  for (var i = 0; i < 256; i++) LOOKUP[MINIMAL.substr(i * 2, 2)] = i;

  // ------------------------------------------------------------ CRC32

  var CRC_TABLE = (function () {
    var table = new Uint32Array(256);
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      table[n] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    var crc = 0xffffffff;
    for (var i = 0; i < bytes.length; i++) {
      crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  // ------------------------------------------------------------ SHA-256
  //
  // Here rather than crypto.subtle because the generator below seeds itself
  // with one, in the middle of a synchronous decode of a frame that will be
  // gone in a sixth of a second. The whole chain is checked against the
  // firmware's own encoder, so a mistake in it does not survive the tests.

  var K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);

  function sha256(bytes) {
    var h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                             0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    var length = bytes.length;
    var padded = new Uint8Array(((length + 9 + 63) >> 6) << 6);
    padded.set(bytes);
    padded[length] = 0x80;
    var bits = length * 8;
    for (var b = 0; b < 4; b++) padded[padded.length - 1 - b] = (bits >>> (8 * b)) & 0xff;

    var w = new Uint32Array(64);
    function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

    for (var block = 0; block < padded.length; block += 64) {
      for (var t = 0; t < 16; t++) {
        w[t] = (padded[block + t * 4] << 24) | (padded[block + t * 4 + 1] << 16)
             | (padded[block + t * 4 + 2] << 8) | padded[block + t * 4 + 3];
      }
      for (var s = 16; s < 64; s++) {
        var s0 = rotr(w[s - 15], 7) ^ rotr(w[s - 15], 18) ^ (w[s - 15] >>> 3);
        var s1 = rotr(w[s - 2], 17) ^ rotr(w[s - 2], 19) ^ (w[s - 2] >>> 10);
        w[s] = (w[s - 16] + s0 + w[s - 7] + s1) >>> 0;
      }
      var a = h[0], bb = h[1], c = h[2], d = h[3];
      var e = h[4], f = h[5], g = h[6], hh = h[7];
      for (var r = 0; r < 64; r++) {
        var S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        var ch = (e & f) ^ (~e & g);
        var temp1 = (hh + S1 + ch + K[r] + w[r]) >>> 0;
        var S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        var maj = (a & bb) ^ (a & c) ^ (bb & c);
        var temp2 = (S0 + maj) >>> 0;
        hh = g; g = f; f = e; e = (d + temp1) >>> 0;
        d = c; c = bb; bb = a; a = (temp1 + temp2) >>> 0;
      }
      h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + bb) >>> 0;
      h[2] = (h[2] + c) >>> 0; h[3] = (h[3] + d) >>> 0;
      h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0;
      h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0;
    }
    var out = new Uint8Array(32);
    for (var o = 0; o < 8; o++) {
      out[o * 4] = h[o] >>> 24; out[o * 4 + 1] = (h[o] >>> 16) & 0xff;
      out[o * 4 + 2] = (h[o] >>> 8) & 0xff; out[o * 4 + 3] = h[o] & 0xff;
    }
    return out;
  }

  // ------------------------------------------------------- xoshiro256**

  var MASK64 = (1n << 64n) - 1n;

  function Xoshiro(seed) {
    var digest = sha256(seed);
    this.s = [];
    for (var i = 0; i < 4; i++) {
      var v = 0n;
      for (var n = 0; n < 8; n++) v = (v << 8n) | BigInt(digest[i * 8 + n]);
      this.s.push(v);
    }
  }

  function rotl(x, k) {
    return ((x << BigInt(k)) | (x >> BigInt(64 - k))) & MASK64;
  }

  Xoshiro.prototype.next = function () {
    var result = (rotl((this.s[1] * 5n) & MASK64, 7) * 9n) & MASK64;
    var t = (this.s[1] << 17n) & MASK64;
    this.s[2] ^= this.s[0];
    this.s[3] ^= this.s[1];
    this.s[1] ^= this.s[2];
    this.s[0] ^= this.s[3];
    this.s[2] ^= t;
    this.s[3] = rotl(this.s[3], 45);
    return result;
  };

  Xoshiro.prototype.nextDouble = function () {
    return Number(this.next()) / 18446744073709551616;
  };

  Xoshiro.prototype.nextInt = function (low, high) {
    return Math.floor(this.nextDouble() * (high - low + 1) + low);
  };

  // The alias method, as the encoder's RandomSampler builds it, so that the
  // same two random numbers pick the same degree at both ends.
  function sampler(probabilities) {
    var n = probabilities.length;
    var total = probabilities.reduce(function (sum, p) { return sum + p; }, 0);
    var P = probabilities.map(function (p) { return (p * n) / total; });
    var small = [], large = [];
    for (var i = n - 1; i >= 0; i--) (P[i] < 1 ? small : large).push(i);

    var probs = new Array(n).fill(0), aliases = new Array(n).fill(0);
    while (small.length && large.length) {
      var a = small.pop(), g = large.pop();
      probs[a] = P[a];
      aliases[a] = g;
      P[g] += P[a] - 1;
      (P[g] < 1 ? small : large).push(g);
    }
    while (large.length) probs[large.pop()] = 1;
    while (small.length) probs[small.pop()] = 1;

    return function (rng) {
      var r1 = rng.nextDouble(), r2 = rng.nextDouble();
      var index = Math.floor(n * r1);
      return r2 < probs[index] ? index : aliases[index];
    };
  }

  function chooseDegree(seqLength, rng) {
    var probabilities = [];
    for (var i = 1; i <= seqLength; i++) probabilities.push(1 / i);
    return sampler(probabilities)(rng) + 1;
  }

  /** Which fragments went into this part. */
  function chooseFragments(seqNumber, seqLength, checksum) {
    if (seqNumber <= seqLength) return [seqNumber - 1];
    var seed = new Uint8Array(8);
    for (var i = 0; i < 4; i++) {
      seed[i] = (seqNumber >>> (8 * (3 - i))) & 0xff;
      seed[4 + i] = (checksum >>> (8 * (3 - i))) & 0xff;
    }
    var rng = new Xoshiro(seed);
    var degree = chooseDegree(seqLength, rng);
    var remaining = [];
    for (var n = 0; n < seqLength; n++) remaining.push(n);
    var shuffled = [];
    while (remaining.length) shuffled.push(remaining.splice(rng.nextInt(0, remaining.length - 1), 1)[0]);
    return shuffled.slice(0, degree).sort(function (a, b) { return a - b; });
  }

  // ------------------------------------------------------------ bytewords

  function bytewords(body) {
    if (body.length % 2) throw new Error("truncated bytewords");
    var out = new Uint8Array(body.length / 2);
    for (var i = 0; i < out.length; i++) {
      var value = LOOKUP[body.substr(i * 2, 2)];
      if (value === undefined) throw new Error("not a byteword: " + body.substr(i * 2, 2));
      out[i] = value;
    }
    var payload = out.subarray(0, out.length - 4);
    var checksum = 0;
    for (var c = out.length - 4; c < out.length; c++) checksum = (checksum * 256) + out[c];
    if (checksum >>> 0 !== crc32(payload)) throw new Error("bytewords checksum does not match");
    return payload;
  }

  // Just enough CBOR: unsigned integers, byte strings and one array.
  function cbor(bytes) {
    var at = 0;
    function head() {
      var first = bytes[at++];
      var major = first >> 5, extra = first & 31;
      var value = extra;
      if (extra === 24) value = bytes[at++];
      else if (extra === 25) { value = (bytes[at] << 8) | bytes[at + 1]; at += 2; }
      else if (extra === 26) {
        value = ((bytes[at] << 24) >>> 0) + (bytes[at + 1] << 16) +
                (bytes[at + 2] << 8) + bytes[at + 3];
        at += 4;
      } else if (extra > 26) throw new Error("unexpected CBOR length");
      return { major: major, value: value };
    }
    return {
      item: function () {
        var it = head();
        if (it.major === 2) {
          var slice = bytes.subarray(at, at + it.value);
          at += it.value;
          return slice;
        }
        return it.value;
      },
      arrayLength: function () {
        var it = head();
        if (it.major !== 4) throw new Error("expected a CBOR array");
        return it.value;
      },
    };
  }

  function xorInto(target, other) {
    for (var i = 0; i < target.length; i++) target[i] ^= other[i];
    return target;
  }

  function subset(small, big) {
    return small.every(function (i) { return big.indexOf(i) !== -1; });
  }

  /**
   * Collects the parts of one UR until it has the whole message.
   *
   * receive() takes whatever the QR reader saw; done() says whether the message
   * is complete; type() names what it turned out to be and payload() hands back
   * the CBOR it carries. psbt() is payload() with the byte string a
   * ur:crypto-psbt wraps its transaction in taken off.
   */
  function collector() {
    var type = null;
    var single = null;
    var seqLength = null, messageLength = null, checksum = null;
    var simple = {};      // fragment index -> bytes
    var mixed = [];       // { indexes: [...], data }
    var known = 0;

    function unwrap(message) {
      return cbor(message).item();     // a CBOR byte string wrapping the PSBT
    }

    /** Take every fragment we already know out of this part. */
    function reduce(part) {
      Object.keys(simple).forEach(function (index) {
        var at = part.indexes.indexOf(Number(index));
        if (at !== -1 && part.indexes.length > 1) {
          xorInto(part.data, simple[index]);
          part.indexes.splice(at, 1);
        }
      });
      return part;
    }

    function absorb(part) {
      reduce(part);
      if (part.indexes.length !== 1) {
        // Not yet reducible on its own; keep it, it may be useful later.
        if (!mixed.some(function (m) { return String(m.indexes) === String(part.indexes); })) {
          mixed.push(part);
        }
        return;
      }
      var index = part.indexes[0];
      if (simple[index] !== undefined) return;
      simple[index] = part.data;
      known++;
      // A newly known fragment may unlock some of the mixtures being held.
      var pending = mixed;
      mixed = [];
      pending.forEach(absorb);
    }

    /**
     * The message, whole, and never a message a CRC32 did not agree with.
     *
     * There are two roads to it and each carries its own check: a single part
     * ends in four bytewords that are a CRC32 of everything before them, which
     * bytewords() refuses it over, and a message put back together out of parts
     * is checked here against the checksum every one of those parts declared.
     * Neither check stands in for the other and neither is skipped.
     */
    function message() {
      if (single) return single;
      var whole = new Uint8Array(seqLength * simple[0].length);
      for (var n = 0; n < seqLength; n++) whole.set(simple[n], n * simple[0].length);
      whole = whole.subarray(0, messageLength);
      if (crc32(whole) !== checksum) throw new Error("the reassembled ur:" + type + " is corrupt");
      return whole;
    }

    return {
      receive: function (text) {
        if (!text) return false;
        var lower = String(text).toLowerCase();
        var head = /^ur:([a-z0-9-]+)\//.exec(lower);
        if (!head) return false;
        // One message per collector. Two UR types on one screen means the
        // device has moved on to something else, and the parts of one message
        // are nothing but noise to another: the fountain numbers them against
        // their own message's length and checksum.
        if (type === null) type = head[1];
        else if (type !== head[1]) return false;
        var pieces = lower.split("/");
        if (pieces.length === 2) {
          single = bytewords(pieces[1]);
          return true;
        }
        var part = cbor(bytewords(pieces[2]));
        part.arrayLength();
        var seqNumber = part.item();
        seqLength = part.item();
        messageLength = part.item();
        checksum = part.item();
        var data = part.item();
        absorb({
          indexes: chooseFragments(seqNumber, seqLength, checksum),
          data: Uint8Array.from(data),
        });
        return true;
      },

      done: function () {
        return single !== null || (seqLength !== null && known >= seqLength);
      },

      parts: function () { return seqLength; },
      have: function () { return single ? 1 : known; },
      type: function () { return type; },

      payload: function () { return message(); },

      psbt: function () {
        if (type !== "crypto-psbt") throw new Error("that is a ur:" + type + ", not a transaction");
        return unwrap(message());
      },
    };
  }

  scope.URDecode = {
    collector: collector,
    crc32: crc32,
    sha256: sha256,
    chooseFragments: chooseFragments,
  };
})(typeof self !== "undefined" ? self : this);
