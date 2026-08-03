// A QR encoder, byte mode only, error correction level L, versions 1 to 20.
//
// The page already has jsQR, which only reads. The tutorial has to *show* codes
// as well: the coordinator on the phone holds a QR up to the device's camera,
// and that QR has to be a real one, drawn from real modules, because the
// device's own scan path decodes it with jsQR from the pixels on screen. A
// picture of a QR would not survive that, which is the point.
//
// Deliberately the smallest encoder that can carry what this tutorial carries:
// one mode, one error correction level, one mask. Byte mode covers everything
// here (a SeedQR's 48 digits are bytes too, and the wallet reads them as such).
// Level L is what SeedSigner itself draws with. Mask 0 is chosen rather than
// scored: any of the eight masks makes a valid code and a decoder tries them
// all, and the eight-way penalty scoring in the spec exists to help a camera
// pointed at paper under bad light, which is not the situation here.
//
// Everything else is the standard: ISO/IEC 18004 tables for block structure and
// alignment patterns, GF(256) Reed-Solomon for the checkwords, and the two BCH
// codes for the format and version information computed rather than tabulated.

(function (scope) {
  "use strict";

  // Per version at level L: EC codewords per block, then (blocks, data
  // codewords per block) for group 1 and, where the version has one, group 2.
  var BLOCKS_L = {
    1:  [7,  1, 19],            2:  [10, 1, 34],            3:  [15, 1, 55],
    4:  [20, 1, 80],            5:  [26, 1, 108],           6:  [18, 2, 68],
    7:  [20, 2, 78],            8:  [24, 2, 97],            9:  [30, 2, 116],
    10: [18, 2, 68, 2, 69],     11: [20, 4, 81],            12: [24, 2, 92, 2, 93],
    13: [26, 4, 107],           14: [30, 3, 115, 1, 116],   15: [22, 5, 87, 1, 88],
    16: [24, 5, 98, 1, 99],     17: [28, 1, 107, 5, 108],   18: [30, 5, 120, 1, 121],
    19: [28, 3, 113, 4, 114],   20: [28, 3, 107, 5, 108],
  };

  // Row and column centres of the alignment patterns, per version.
  var ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
    11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62], 14: [6, 26, 46, 66],
    15: [6, 26, 48, 70], 16: [6, 26, 50, 74], 17: [6, 30, 54, 78],
    18: [6, 30, 56, 82], 19: [6, 30, 58, 86], 20: [6, 34, 62, 90],
  };

  var MASK = 0;  // (row + column) % 2 === 0

  function dataCodewords(version) {
    var spec = BLOCKS_L[version];
    var total = spec[1] * spec[2];
    if (spec.length > 3) total += spec[3] * spec[4];
    return total;
  }

  // 4 bits of mode, then the character count: 8 bits up to version 9, 16 above.
  function capacity(version) {
    return dataCodewords(version) - (version < 10 ? 2 : 3);
  }

  function pickVersion(length) {
    for (var v = 1; v <= 20; v++) {
      if (capacity(v) >= length) return v;
    }
    throw new Error("payload of " + length + " bytes is too long for this encoder");
  }

  // ------------------------------------------------------------- GF(256)

  var EXP = new Uint8Array(512);
  var LOG = new Uint8Array(256);
  (function () {
    var x = 1;
    for (var i = 0; i < 255; i++) {
      EXP[i] = x;
      LOG[x] = i;
      x <<= 1;
      if (x & 0x100) x ^= 0x11d;   // the primitive polynomial QR uses
    }
    for (var j = 255; j < 512; j++) EXP[j] = EXP[j - 255];
  })();

  function mul(a, b) {
    if (a === 0 || b === 0) return 0;
    return EXP[LOG[a] + LOG[b]];
  }

  function generator(degree) {
    var poly = [1];
    for (var d = 0; d < degree; d++) {
      var next = new Array(poly.length + 1).fill(0);
      // (x + a^d): the shifted copy keeps its place, the scaled one moves down.
      for (var i = 0; i < poly.length; i++) {
        next[i] ^= poly[i];
        next[i + 1] ^= mul(poly[i], EXP[d]);
      }
      poly = next;
    }
    return poly;
  }

  function checkwords(data, count) {
    var poly = generator(count);
    var out = new Array(count).fill(0);
    for (var i = 0; i < data.length; i++) {
      var factor = data[i] ^ out[0];
      out.shift();
      out.push(0);
      for (var j = 0; j < count; j++) out[j] ^= mul(poly[j + 1], factor);
    }
    return out;
  }

  // ------------------------------------------------------- bits and blocks

  function encodeData(bytes, version) {
    var bits = [];
    function push(value, width) {
      for (var i = width - 1; i >= 0; i--) bits.push((value >> i) & 1);
    }

    push(4, 4);                                   // byte mode
    push(bytes.length, version < 10 ? 8 : 16);
    for (var i = 0; i < bytes.length; i++) push(bytes[i], 8);

    var total = dataCodewords(version) * 8;
    push(0, Math.min(4, total - bits.length));    // terminator, as much as fits
    while (bits.length % 8) bits.push(0);

    var codewords = [];
    for (var b = 0; b < bits.length; b += 8) {
      var byte = 0;
      for (var k = 0; k < 8; k++) byte = (byte << 1) | bits[b + k];
      codewords.push(byte);
    }
    // The two pad bytes the spec names, alternating, until the block is full.
    var pad = [0xec, 0x11];
    while (codewords.length < dataCodewords(version)) {
      codewords.push(pad[(codewords.length - bits.length / 8) % 2]);
    }
    return codewords;
  }

  function interleave(codewords, version) {
    var spec = BLOCKS_L[version];
    var ecCount = spec[0];
    var groups = [[spec[1], spec[2]]];
    if (spec.length > 3) groups.push([spec[3], spec[4]]);

    var blocks = [];
    var at = 0;
    groups.forEach(function (group) {
      for (var i = 0; i < group[0]; i++) {
        var data = codewords.slice(at, at + group[1]);
        at += group[1];
        blocks.push({ data: data, ec: checkwords(data, ecCount) });
      }
    });

    var out = [];
    var longest = Math.max.apply(null, blocks.map(function (b) { return b.data.length; }));
    for (var col = 0; col < longest; col++) {
      blocks.forEach(function (block) {
        if (col < block.data.length) out.push(block.data[col]);
      });
    }
    for (var e = 0; e < ecCount; e++) {
      blocks.forEach(function (block) { out.push(block.ec[e]); });
    }
    return out;
  }

  // ---------------------------------------------------------- the matrix

  // Both are BCH codes: divide by the generator, keep the remainder, and for
  // the format also mask it so an all-zero format is not all-zero modules.
  function bch(value, generatorPoly, bits) {
    var remainder = value;
    for (var i = bits - 1; i >= 0; i--) {
      if (remainder & (1 << (i + degree(generatorPoly)))) {
        remainder ^= generatorPoly << i;
      }
    }
    return remainder;
  }

  function degree(poly) {
    var d = -1;
    while (poly) { poly >>= 1; d++; }
    return d;
  }

  function formatBits(mask) {
    var value = (0x01 << 3) | mask;              // 0b01 is error correction L
    return ((value << 10) | bch(value << 10, 0x537, 5)) ^ 0x5412;
  }

  function versionBits(version) {
    return (version << 12) | bch(version << 12, 0x1f25, 6);
  }

  function build(codewords, version) {
    var size = version * 4 + 17;
    var modules = [];
    var reserved = [];
    for (var r = 0; r < size; r++) {
      modules.push(new Uint8Array(size));
      reserved.push(new Uint8Array(size));
    }

    function set(row, col, value) {
      modules[row][col] = value ? 1 : 0;
      reserved[row][col] = 1;
    }

    function finder(row, col) {
      for (var dr = -1; dr <= 7; dr++) {
        for (var dc = -1; dc <= 7; dc++) {
          var r = row + dr, c = col + dc;
          if (r < 0 || c < 0 || r >= size || c >= size) continue;
          var ring = Math.max(Math.abs(dr - 3), Math.abs(dc - 3));
          set(r, c, ring !== 2 && ring <= 3);
        }
      }
    }

    finder(0, 0);
    finder(0, size - 7);
    finder(size - 7, 0);

    for (var i = 8; i < size - 8; i++) {          // timing patterns
      set(6, i, i % 2 === 0);
      set(i, 6, i % 2 === 0);
    }

    // Alignment patterns sit at every pair of these centres except the three
    // that would land on a finder. They are allowed to sit on the timing
    // patterns, where the two agree module for module by design.
    var centres = ALIGN[version];
    var last = centres.length - 1;
    for (var ci = 0; ci <= last; ci++) {
      for (var cj = 0; cj <= last; cj++) {
        if ((ci === 0 && cj === 0) || (ci === 0 && cj === last) ||
            (ci === last && cj === 0)) continue;
        for (var dr = -2; dr <= 2; dr++) {
          for (var dc = -2; dc <= 2; dc++) {
            set(centres[ci] + dr, centres[cj] + dc,
                Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
          }
        }
      }
    }

    set(size - 8, 8, 1);                          // the dark module

    // Fifteen format bits, written twice: once around the top-left finder and
    // once split between the other two, so a damaged corner does not take the
    // error correction level and mask with it.
    var format = formatBits(MASK);
    for (var f = 0; f < 15; f++) {
      var bit = (format >> f) & 1;
      if (f < 6) set(f, 8, bit);
      else if (f === 6) set(7, 8, bit);
      else if (f === 7) set(8, 8, bit);
      else if (f === 8) set(8, 7, bit);
      else set(8, 14 - f, bit);

      if (f < 8) set(8, size - 1 - f, bit);
      else set(size - 15 + f, 8, bit);
    }

    if (version >= 7) {
      var info = versionBits(version);
      for (var v = 0; v < 18; v++) {
        var vbit = (info >> v) & 1;
        var vr = Math.floor(v / 3), vc = size - 11 + (v % 3);
        set(vr, vc, vbit);
        set(vc, vr, vbit);
      }
    }

    // Two columns at a time, bottom right upwards, skipping column 6.
    var bitIndex = 0;
    var upward = true;
    for (var col = size - 1; col > 0; col -= 2) {
      if (col === 6) col--;
      for (var step = 0; step < size; step++) {
        var row = upward ? size - 1 - step : step;
        for (var side = 0; side < 2; side++) {
          var c = col - side;
          if (reserved[row][c]) continue;
          var dark = 0;
          if (bitIndex < codewords.length * 8) {
            dark = (codewords[bitIndex >> 3] >> (7 - (bitIndex & 7))) & 1;
          }
          bitIndex++;
          if ((row + c) % 2 === 0) dark ^= 1;      // mask 0
          modules[row][c] = dark;
        }
      }
      upward = !upward;
    }

    return modules;
  }

  /** A QR for these bytes, as an array of rows of 0 and 1. */
  function matrix(payload) {
    var bytes = typeof payload === "string"
      ? new TextEncoder().encode(payload) : Uint8Array.from(payload);
    var version = pickVersion(bytes.length);
    return build(interleave(encodeData(bytes, version), version), version);
  }

  scope.QREncode = { matrix: matrix, capacity: capacity };
})(typeof self !== "undefined" ? self : this);
