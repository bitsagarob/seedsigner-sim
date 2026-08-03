/*
 * Procedural SeedSigner Plus device art.
 *
 * Drawn from the hardware, not invented: a landscape stadium shell with fully
 * semicircular end caps, a square-ish display behind a pale LCD frame set into a
 * dark well, five cream pill keys in a D-pad diamond on the left, three more
 * stacked on the right, and a smartcard standing proud of the front edge beside
 * the microSD slot. An earlier pass drew a portrait handheld with a rubber ring
 * pad and a speaker grille; none of that is on the real device.
 *
 * Standalone on purpose: nothing here touches SharedArrayBuffer, a worker or a
 * camera, so the same file can dress the live simulator and a marketing page
 * that has none of that. The only way out is the onKey callback.
 *
 * Everything is drawn rather than loaded because the pages using this send a CSP
 * with no external image, font or script origins.
 *
 * Lighting is one key from the upper left plus a soft fill. Anything shaded by a
 * gradient of its own would light itself in isolation and break that, so the
 * scene-wide paints below are userSpaceOnUse: a key near the bottom right is
 * darker than the same key near the top left because it samples a different
 * part of the same light.
 *
 * The screen cutout is published as a percentage of the viewBox, never as pixels,
 * so the live canvas keeps registration with the art at any rendered width.
 */
(function (global) {
  "use strict";

  var doc = global.document;
  if (!doc) return;

  var STYLE_ID = "ssd-style";
  var instances = 0;

  // Index into the wallet's BUTTON_NAMES. Mirrored rather than imported so this
  // file keeps no wallet dependency.
  var CHANNEL = {
    up: 1, down: 2, left: 3, right: 4, select: 5, key1: 6, key2: 7, key3: 8,
  };

  var CSS = [
    // No tap highlight and no selection: a thumb on a key must not paint a blue
    // box over it or start selecting the shell.
    ".ssd-root{position:relative;display:inline-block;line-height:0;max-width:100%;",
    "touch-action:manipulation;-webkit-tap-highlight-color:transparent;",
    "-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}",
    ".ssd-svg{display:block;width:100%;height:auto}",
    // Percentage geometry, so the cutout tracks the art through any resize.
    ".ssd-screen-slot{position:absolute;z-index:2;overflow:hidden;background:#000}",
    ".ssd-screen-slot>canvas{display:block;width:100%;height:100%}",
    ".ssd-glass{position:absolute;z-index:3;pointer-events:none}",
    ".ssd-ctl{pointer-events:none}",
    ".ssd-ctl .ssd-hover,.ssd-ctl .ssd-press{opacity:0}",
    // Only a live device reacts; the decorative build stays inert illustration.
    ".ssd-live .ssd-ctl{pointer-events:auto;cursor:pointer}",
    ".ssd-live .ssd-cap{transition:transform .07s ease-out}",
    ".ssd-live .ssd-ctl .ssd-hover{transition:opacity .13s ease}",
    // Hover only where there is a pointer that can hover. A touch that lands on
    // a key would otherwise leave it lit until something else was touched.
    "@media (hover:hover){.ssd-live .ssd-ctl:hover .ssd-hover{opacity:.2}}",
    // Pressed is a class rather than :active, because :active under touch is
    // whatever the browser feels like: Safari does not apply it at all without a
    // touch handler, and every engine drops it the moment a finger drifts. The
    // class goes on when the key is pressed and stays long enough to be seen.
    // The cap sinks, not the whole key: it goes down into its own side wall, so
    // the wall shortens under a press the way a real one does.
    ".ssd-live .ssd-ctl.ssd-down .ssd-cap{transform:translateY(var(--ssd-sink,2px))}",
    ".ssd-live .ssd-ctl.ssd-down .ssd-press{opacity:.42}",
    ".ssd-live .ssd-ctl.ssd-down .ssd-hover{opacity:.1}",
    ".ssd-live .ssd-ctl.ssd-down .ssd-shadow{opacity:.12}",
    ".ssd-live .ssd-ctl.ssd-down .ssd-gloss{opacity:.2}",
    "@media (prefers-reduced-motion:reduce){.ssd-live .ssd-ctl,",
    ".ssd-live .ssd-ctl .ssd-hover{transition:none}}",
  ].join("\n");

  // How long a key stays visibly down. A tap can be over in 40 milliseconds,
  // which is not long enough to see, so the state is held to this floor.
  var PRESSED_MS = 130;

  /**
   * One press per finger, from a drawn key and nowhere else.
   *
   * pointerdown rather than click: a key has to answer where a thumb lands,
   * and click arrives up to 300ms later on a phone. Nothing listens for mouse
   * events alongside it either, because a touch synthesises a mousedown of its
   * own afterwards and a device that answered both would send every key twice;
   * preventDefault here stops that synthesis, and with it the long-press menu
   * and the text selection, none of which a hardware button has.
   *
   * The pointer is remembered until it lifts, so a finger held on a key is one
   * press and no repeat -- the real device does not auto-repeat either -- and a
   * second finger arriving while the first is down is not a second press.
   */
  function bindControls(svgEl, onKey) {
    var pointer = null;    // the pointer holding a key down, if any
    var key = null;        // and the key it is holding
    var since = 0;

    function release() {
      if (!key) return;
      var released = key, waited = Date.now() - since;
      key = null;
      pointer = null;
      if (waited >= PRESSED_MS) released.classList.remove("ssd-down");
      else setTimeout(function () { released.classList.remove("ssd-down"); },
                      PRESSED_MS - waited);
    }

    function begin(event) {
      if (event.isPrimary === false) return;
      var hit = event.target.closest && event.target.closest("[data-ssd-channel]");
      if (!hit) return;
      event.preventDefault();
      // Any press still open ends here rather than wedging the device shut if
      // its pointerup was never delivered.
      release();
      key = hit;
      pointer = event.pointerId;
      since = Date.now();
      hit.classList.add("ssd-down");
      onKey(parseInt(hit.getAttribute("data-ssd-channel"), 10));
    }

    if (global.PointerEvent) {
      svgEl.addEventListener("pointerdown", begin);
      // On the window: a finger that slides off the key before it lifts still
      // ends the press, and so does the browser taking the gesture away.
      var end = function (event) { if (pointer === event.pointerId) release(); };
      global.addEventListener("pointerup", end);
      global.addEventListener("pointercancel", end);
    } else {
      svgEl.addEventListener("mousedown", begin);
      global.addEventListener("mouseup", release);
    }
  }

  function injectStyle() {
    if (doc.getElementById(STYLE_ID)) return;
    var el = doc.createElement("style");
    el.id = STYLE_ID;
    el.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(el);
  }

  function n(v) { return Math.round(v * 100) / 100; }
  function pct(a, b) { return n(a / b * 100) + "%"; }

  function roundRectPath(x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    return "M" + n(x + r) + " " + n(y) + "H" + n(x + w - r) +
      "a" + n(r) + " " + n(r) + " 0 0 1 " + n(r) + " " + n(r) +
      "V" + n(y + h - r) +
      "a" + n(r) + " " + n(r) + " 0 0 1 " + n(-r) + " " + n(r) +
      "H" + n(x + r) +
      "a" + n(r) + " " + n(r) + " 0 0 1 " + n(-r) + " " + n(-r) +
      "V" + n(y + r) +
      "a" + n(r) + " " + n(r) + " 0 0 1 " + n(r) + " " + n(-r) + "Z";
  }

  // Every key on the real device is a fully radiused tic-tac, so the pill is the
  // only control shape here; a "circle" is just a pill as wide as it is tall.
  function pillPath(cx, cy, w, h) {
    return roundRectPath(cx - w / 2, cy - h / 2, w, h, h / 2);
  }

  // Stroking a path with its own paint rounds off sharp corners, which is how a
  // key gets a moulded edge instead of a die-cut one.
  function paint(shape, fill, grow, cls) {
    var grown = grow > 0
      ? ' stroke="' + fill + '" stroke-width="' + n(grow * 2) + '" stroke-linejoin="round"'
      : "";
    return '<path' + (cls ? ' class="' + cls + '"' : "") +
      ' d="' + shape + '" fill="' + fill + '"' + grown + "/>";
  }

  /*
   * Every number below is off a square-on photograph of the hardware, divided
   * through by the height of the glass so it lands in design units. They are
   * asymmetric because the device is: the LCD module sits high and left in its
   * well, and both key clusters sit far closer to the screen than to the end
   * caps, which is what leaves the big empty semicircles the shell is known for.
   * Centring either cluster in its gutter is the single thing that made earlier
   * passes read as a games console rather than as this device.
   */
  function layout(screenW, screenH, scale, withCard) {
    var sw = Math.round(screenW * scale);
    var sh = Math.round(screenH * scale);
    var u = sh / 480;                    // one design unit; the art is pure ratio
    var L = { u: u, sw: sw, sh: sh };

    L.edge = 12 * u;                     // chamfer band around the shell
    // Glass -> dark well. Wider right and below because the module is not centred.
    L.well = { l: 18 * u, t: 19 * u, r: 45 * u, b: 29 * u };
    // Glass -> the module's own pale frame, the light band that shows along the
    // bottom of the screen and down its right side on the real thing.
    L.frame = { l: 1 * u, t: 1 * u, r: 23 * u, b: 21 * u };
    // Well -> shell edge. The port renders 4:3 where the hardware glass is a
    // little squarer, so the two big gutters carry the 15u of slack that leaves.
    L.padGut = 474 * u;                  // shell left edge -> well
    L.keyGut = 289 * u;                  // well -> shell right edge
    L.railT = 29 * u;
    L.railB = 26 * u;

    L.bodyW = L.padGut + L.well.l + sw + L.well.r + L.keyGut;
    L.bodyH = L.railT + L.well.t + sh + L.well.b + L.railB;
    L.radius = L.bodyH / 2;              // a true stadium, not a rounded rect

    // The card standing out of the front edge sets the bottom padding, so an
    // empty reader gets that vertical space back rather than reserving it.
    L.withCard = withCard;
    L.cardW = 500 * u;
    L.cardH = 300 * u;
    L.cardBite = 44 * u;                 // how far its top hides inside the shell

    L.padX = 12 * u;
    L.padT = 22 * u;
    L.padB = withCard ? L.cardH - L.cardBite + 44 * u : 58 * u;

    L.bodyX = L.padX;
    L.bodyY = L.padT;
    L.viewW = L.bodyW + L.padX * 2;
    L.viewH = L.bodyH + L.padT + L.padB;

    L.screenX = L.bodyX + L.padGut + L.well.l;
    L.screenY = L.bodyY + L.railT + L.well.t;
    L.cx = L.bodyX + L.bodyW / 2;
    L.cy = L.bodyY + L.bodyH / 2;

    // Measured from the shell edges rather than centred in their gutters: both
    // clusters sit well inboard, tucked against the screen.
    L.padCx = L.bodyX + 268 * u;                       // D-pad centre
    L.keyCx = L.bodyX + L.bodyW - 182 * u;             // 1/2/3 column centre

    // How far a cap's top face is seen displaced from its own base at the far
    // edge of the shell. Scales with the shell, so the effect is the same
    // photograph at any rendered size.
    L.lift = 5.5 * u;

    L.cardX = L.bodyX + L.bodyW * 0.53 - L.cardW / 2;
    L.cardY = L.bodyY + L.bodyH - L.cardBite;
    return L;
  }

  function defs(id, L) {
    var u = L.u;
    var space = 'gradientUnits="userSpaceOnUse"';
    var bodyBox = ' x1="' + n(L.bodyX) + '" y1="' + n(L.bodyY) + '" x2="' +
      n(L.bodyX + L.bodyW) + '" y2="' + n(L.bodyY + L.bodyH) + '"';
    return [
      "<defs>",
      // Shell top face: key light upper-left, falling away to the lower right.
      // Flatter than a glossy consumer shell: the real one is a matte grey.
      '<linearGradient id="', id, '-body" ', space, bodyBox, ">",
      '<stop offset="0" stop-color="#55585c"/>',
      '<stop offset=".40" stop-color="#45484c"/>',
      '<stop offset=".75" stop-color="#383b3f"/>',
      '<stop offset="1" stop-color="#2e3134"/>',
      "</linearGradient>",
      // A chamfer facet is lit by its own orientation, not by where it sits, so
      // the bevel is shaded per edge: these run across the whole ring and
      // brighten the up-facing and left-facing facets along their full length.
      '<linearGradient id="', id, '-chamV" x1="0" y1="0" x2="0" y2="1">',
      '<stop offset="0" stop-color="#ffffff" stop-opacity=".6"/>',
      '<stop offset="', n(L.edge / L.bodyH * 0.85), '" stop-color="#ffffff" stop-opacity=".46"/>',
      '<stop offset="', n(L.edge / L.bodyH * 2.2), '" stop-color="#ffffff" stop-opacity=".08"/>',
      '<stop offset=".12" stop-color="#ffffff" stop-opacity="0"/>',
      '<stop offset=".88" stop-color="#000000" stop-opacity="0"/>',
      '<stop offset="', n(1 - L.edge / L.bodyH * 1.1), '" stop-color="#000000" stop-opacity=".2"/>',
      '<stop offset="1" stop-color="#000000" stop-opacity=".46"/>',
      "</linearGradient>",
      '<linearGradient id="', id, '-chamH" x1="0" y1="0" x2="1" y2="0">',
      '<stop offset="0" stop-color="#ffffff" stop-opacity=".34"/>',
      '<stop offset="', n(L.edge / L.bodyW * 0.85), '" stop-color="#ffffff" stop-opacity=".26"/>',
      '<stop offset="', n(L.edge / L.bodyW * 2.2), '" stop-color="#ffffff" stop-opacity=".05"/>',
      '<stop offset=".12" stop-color="#ffffff" stop-opacity="0"/>',
      '<stop offset=".88" stop-color="#000000" stop-opacity="0"/>',
      '<stop offset="', n(1 - L.edge / L.bodyW * 1.1), '" stop-color="#000000" stop-opacity=".18"/>',
      '<stop offset="1" stop-color="#000000" stop-opacity=".4"/>',
      "</linearGradient>",
      '<linearGradient id="', id, '-chamD" x1="0" y1="0" x2="1" y2="1">',
      '<stop offset="0" stop-color="#ffffff" stop-opacity=".13"/>',
      '<stop offset=".32" stop-color="#ffffff" stop-opacity="0"/>',
      '<stop offset=".45" stop-color="#000000" stop-opacity="0"/>',
      '<stop offset="1" stop-color="#000000" stop-opacity=".13"/>',
      "</linearGradient>",
      // A broad soft source skimming the face, which is most of what separates a
      // photographed shell from a filled rectangle.
      '<linearGradient id="', id, '-sheen" ', space,
      ' x1="', n(L.bodyX), '" y1="', n(L.bodyY), '" x2="',
      n(L.bodyX + L.bodyW * 0.78), '" y2="', n(L.bodyY + L.bodyH), '">',
      '<stop offset="0" stop-color="#ffffff" stop-opacity="0"/>',
      '<stop offset=".14" stop-color="#ffffff" stop-opacity=".045"/>',
      '<stop offset=".26" stop-color="#ffffff" stop-opacity=".012"/>',
      '<stop offset=".44" stop-color="#ffffff" stop-opacity="0"/>',
      "</linearGradient>",
      // Same facet inverted: a recess turns its lit wall towards the lower right.
      '<linearGradient id="', id, '-recess" x1="0" y1="0" x2="1" y2="1">',
      '<stop offset="0" stop-color="#0c0e11"/>',
      '<stop offset=".35" stop-color="#181b20"/>',
      '<stop offset=".72" stop-color="#3c424b"/>',
      '<stop offset="1" stop-color="#59606b"/>',
      "</linearGradient>",
      // The LCD module's own frame, which on the hardware is a pale grey band
      // showing along the bottom of the glass and down its right side because
      // the module sits high and left in the well. Graded by where it sits in
      // the scene like everything else raised.
      '<linearGradient id="', id, '-bezel" ', space, bodyBox, ">",
      '<stop offset="0" stop-color="#8b8982"/>',
      '<stop offset=".45" stop-color="#73716b"/>',
      '<stop offset="1" stop-color="#55544f"/>',
      "</linearGradient>",
      '<radialGradient id="', id, '-keylight" ', space,
      ' cx="', n(L.bodyX + L.bodyW * 0.2), '" cy="', n(L.bodyY + L.bodyH * 0.05),
      '" r="', n(L.bodyW * 0.95), '">',
      '<stop offset="0" stop-color="#ffffff" stop-opacity=".09"/>',
      '<stop offset=".55" stop-color="#ffffff" stop-opacity=".016"/>',
      '<stop offset="1" stop-color="#ffffff" stop-opacity="0"/>',
      "</radialGradient>",
      // The one light every raised part is graded against.
      '<linearGradient id="', id, '-scene" ', space, bodyBox, ">",
      '<stop offset="0" stop-color="#ffffff" stop-opacity=".07"/>',
      '<stop offset=".42" stop-color="#ffffff" stop-opacity="0"/>',
      '<stop offset=".52" stop-color="#000000" stop-opacity="0"/>',
      '<stop offset="1" stop-color="#000000" stop-opacity=".09"/>',
      "</linearGradient>",
      // Cream key caps. Off-white and slightly warm, not paper white.
      '<linearGradient id="', id, '-key" x1=".18" y1="0" x2=".8" y2="1">',
      '<stop offset="0" stop-color="#f4f1ea"/>',
      '<stop offset=".45" stop-color="#e2ded4"/>',
      '<stop offset="1" stop-color="#bdb9ae"/>',
      "</linearGradient>",
      // The side wall of a cap. Which wall of a key is on show is decided by
      // where the key sits (see parallax below), and a wall is lit by which way
      // it faces: the inward wall of a key on the left of the shell turns right,
      // away from the light, and the inward wall of one on the right turns back
      // into it. A single scene-wide ramp therefore shades every wall correctly,
      // because position and facing are the same fact here.
      '<linearGradient id="', id, '-wall" ', space, bodyBox, ">",
      '<stop offset="0" stop-color="#4f4d47"/>',
      '<stop offset=".5" stop-color="#6f6b64"/>',
      '<stop offset="1" stop-color="#948e84"/>',
      "</linearGradient>",
      '<linearGradient id="', id, '-keyRim" x1=".2" y1="0" x2=".8" y2="1">',
      '<stop offset="0" stop-color="#ffffff" stop-opacity=".4"/>',
      '<stop offset=".4" stop-color="#8d8a82" stop-opacity=".22"/>',
      '<stop offset="1" stop-color="#000000" stop-opacity=".4"/>',
      "</linearGradient>",
      // Barely there: the caps are matte moulded plastic, not gel.
      '<linearGradient id="', id, '-gloss" x1=".3" y1="0" x2=".6" y2="1">',
      '<stop offset="0" stop-color="#ffffff" stop-opacity=".16"/>',
      '<stop offset=".5" stop-color="#ffffff" stop-opacity=".015"/>',
      '<stop offset="1" stop-color="#ffffff" stop-opacity="0"/>',
      "</linearGradient>",
      // The smartcard: dark matte PVC catching the same key light.
      '<linearGradient id="', id, '-card" ', space,
      ' x1="', n(L.cardX), '" y1="', n(L.cardY), '" x2="', n(L.cardX + L.cardW),
      '" y2="', n(L.cardY + L.cardH), '">',
      '<stop offset="0" stop-color="#2e3238"/>',
      '<stop offset=".45" stop-color="#1e2126"/>',
      '<stop offset="1" stop-color="#131518"/>',
      "</linearGradient>",
      '<linearGradient id="', id, '-wellTop" x1="0" y1="0" x2="0" y2="1">',
      '<stop offset="0" stop-color="#000000" stop-opacity=".85"/>',
      '<stop offset="1" stop-color="#000000" stop-opacity="0"/>',
      "</linearGradient>",
      '<linearGradient id="', id, '-slot" x1="0" y1="0" x2="0" y2="1">',
      '<stop offset="0" stop-color="#04060a"/>',
      '<stop offset=".62" stop-color="#0c0f13"/>',
      '<stop offset="1" stop-color="#454b55"/>',
      "</linearGradient>",
      // Two shadows: a tight contact patch, and a wide ambient one that lifts the
      // device off a page nearly as dark as the shadow itself.
      '<filter id="', id, '-drop" x="-40%" y="-40%" width="180%" height="200%">',
      '<feDropShadow dx="0" dy="', n(26 * u), '" stdDeviation="', n(32 * u),
      '" flood-color="#000000" flood-opacity=".55"/>',
      "</filter>",
      '<filter id="', id, '-soft" x="-60%" y="-60%" width="220%" height="220%">',
      '<feGaussianBlur stdDeviation="', n(5 * u), '"/>',
      "</filter>",
      '<filter id="', id, '-contact" x="-40%" y="-200%" width="180%" height="500%">',
      '<feGaussianBlur stdDeviation="', n(9 * u), '"/>',
      "</filter>",
      '<filter id="', id, '-btnShadow" x="-70%" y="-70%" width="240%" height="260%">',
      '<feGaussianBlur stdDeviation="', n(4.5 * u), '"/>',
      "</filter>",
      // Matte plastic: without a little grain the gradients read as vector fills.
      '<filter id="', id, '-grain" x="0" y="0" width="100%" height="100%">',
      '<feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" stitchTiles="stitch"/>',
      '<feColorMatrix type="saturate" values="0"/>',
      "</filter>",
      "</defs>",
    ].join("");
  }

  // Drawn before the shell so the front edge overlaps its top: the card is
  // inserted, not resting on top.
  function cardArt(id, L) {
    var u = L.u, out = [];
    var r = 10 * u;

    out.push('<ellipse cx="', n(L.cardX + L.cardW / 2), '" cy="', n(L.cardY + L.cardH),
      '" rx="', n(L.cardW * 0.46), '" ry="', n(9 * u),
      '" fill="#000000" opacity=".55" filter="url(#', id, '-contact)"/>');

    out.push('<rect x="', n(L.cardX), '" y="', n(L.cardY), '" width="', n(L.cardW),
      '" height="', n(L.cardH), '" rx="', n(r), '" fill="url(#', id, '-card)"/>');
    out.push('<rect x="', n(L.cardX), '" y="', n(L.cardY), '" width="', n(L.cardW),
      '" height="', n(L.cardH), '" rx="', n(r),
      '" fill="none" stroke="#000000" stroke-opacity=".5" stroke-width="', n(1.8 * u), '"/>');
    // Top-left lit lip, the only edge of the card facing the key light.
    out.push('<path d="M', n(L.cardX + r), ' ', n(L.cardY), 'H', n(L.cardX + L.cardW - r),
      'M', n(L.cardX), ' ', n(L.cardY + L.cardH - r), 'V', n(L.cardY + r),
      '" fill="none" stroke="#ffffff" stroke-opacity=".12" stroke-width="', n(1.6 * u), '"/>');
    return out.join("");
  }

  function bodyArt(id, L) {
    var u = L.u, x = L.bodyX, y = L.bodyY, w = L.bodyW, h = L.bodyH;
    var ix = x + L.edge, iy = y + L.edge;
    var iw = w - L.edge * 2, ih = h - L.edge * 2;
    var outer = roundRectPath(x, y, w, h, L.radius);
    var inner = roundRectPath(ix, iy, iw, ih, ih / 2);
    var band = ' d="' + outer + inner + '" fill-rule="evenodd"';
    var out = [];

    out.push('<ellipse cx="', n(x + w / 2), '" cy="', n(y + h + 5 * u),
      '" rx="', n(w * 0.44), '" ry="', n(11 * u),
      '" fill="#000000" opacity=".7" filter="url(#', id, '-contact)"/>');

    out.push('<path d="', outer, '" fill="#23262b" filter="url(#', id, '-drop)"/>');
    out.push('<path', band, ' fill="#42464d"/>');
    out.push('<path', band, ' fill="url(#', id, '-chamV)"/>');
    out.push('<path', band, ' fill="url(#', id, '-chamH)"/>');
    out.push('<path', band, ' fill="url(#', id, '-chamD)"/>');
    // Fill light on the shadow side, so the silhouette survives a near-black
    // page. Clipped to the shell: it is a hand-drawn arc rather than the real
    // curve, and where it strays outside the outline it used to leave a bright
    // nub in mid air off the right cap.
    out.push('<clipPath id="', id, '-shell"><path d="', outer, '"/></clipPath>');
    out.push('<g clip-path="url(#', id, '-shell)"><path d="M', n(x + w - 1), ' ', n(y + h * 0.34),
      'a', n(L.radius), ' ', n(L.radius), ' 0 0 1 ', n(-L.radius * 0.62), ' ', n(L.radius * 0.96),
      'H', n(x + w * 0.42), '" fill="none" stroke="#ffffff" stroke-opacity=".11"',
      ' stroke-width="', n(1.8 * u), '"/></g>');

    out.push('<clipPath id="', id, '-face"><path d="', inner, '"/></clipPath>');
    out.push('<path d="', inner, '" fill="url(#', id, '-body)"/>');
    out.push('<path d="', inner, '" fill="url(#', id, '-keylight)"/>');
    out.push('<path d="', inner, '" fill="url(#', id, '-sheen)"/>');
    out.push('<g clip-path="url(#', id, '-face)"><rect x="', n(ix), '" y="', n(iy),
      '" width="', n(iw), '" height="', n(ih), '" filter="url(#', id,
      '-grain)" opacity=".055" style="mix-blend-mode:overlay"/></g>');
    return out.join("");
  }

  function screenArt(id, L) {
    var u = L.u, x = L.screenX, y = L.screenY, w = L.sw, h = L.sh;
    var well = L.well, fr = L.frame;
    var out = [];

    // Dark well.
    out.push('<rect x="', n(x - well.l), '" y="', n(y - well.t),
      '" width="', n(w + well.l + well.r), '" height="', n(h + well.t + well.b),
      '" rx="', n(15 * u), '" fill="url(#', id, '-recess)"/>');
    out.push('<rect x="', n(x - well.l), '" y="', n(y - well.t),
      '" width="', n(w + well.l + well.r), '" height="', n(h + well.t + well.b),
      '" rx="', n(15 * u),
      '" fill="none" stroke="#000000" stroke-opacity=".55" stroke-width="', n(2.2 * u), '"/>');

    // The LCD module's own pale frame, sitting inside the well and off to the
    // top left of it, so what shows is a band under the glass and a strip down
    // its right. Not a border: on the hardware there is nothing to see above or
    // left of the glass, and drawing it all the way round reads as a bezel.
    out.push('<rect x="', n(x - fr.l), '" y="', n(y - fr.t),
      '" width="', n(w + fr.l + fr.r), '" height="', n(h + fr.t + fr.b),
      '" rx="', n(5 * u), '" fill="url(#', id, '-bezel)"/>');
    out.push('<rect x="', n(x - fr.l), '" y="', n(y - fr.t),
      '" width="', n(w + fr.l + fr.r), '" height="', n(h + fr.t + fr.b),
      '" rx="', n(5 * u),
      '" fill="none" stroke="#000000" stroke-opacity=".45" stroke-width="', n(1.4 * u), '"/>');

    // Glass.
    out.push('<clipPath id="', id, '-well"><rect x="', n(x), '" y="', n(y),
      '" width="', n(w), '" height="', n(h), '" rx="', n(4 * u), '"/></clipPath>');
    out.push('<rect x="', n(x), '" y="', n(y), '" width="', n(w), '" height="', n(h),
      '" rx="', n(4 * u), '" fill="#04060a"/>');
    out.push('<g clip-path="url(#', id, '-well)">',
      '<rect x="', n(x), '" y="', n(y), '" width="', n(w), '" height="', n(26 * u),
      '" fill="url(#', id, '-wellTop)" opacity=".8"/>',
      '<rect x="', n(x - 2 * u), '" y="', n(y - 2 * u), '" width="', n(w + 4 * u),
      '" height="', n(h + 4 * u), '" rx="', n(4 * u), '" fill="none" stroke="#000000"',
      ' stroke-width="', n(10 * u), '" opacity=".7" filter="url(#', id, '-soft)"/></g>');
    return out.join("");
  }

  /*
   * A key stands proud of the plate and the camera is over the middle of the
   * shell, so a cap's top face is seen displaced outwards from its own base:
   * the keys on the left show the wall on their right, the ones on the right
   * show the wall on their left, and only a key dead centre shows none. It is
   * the same thing that makes a tower at the edge of an aerial photograph lean
   * away from the middle, and it is most of what separates a moulded cap from a
   * sticker. The vertical term falls out much smaller than the horizontal one
   * because it is the same displacement over a shell that is half as tall.
   */
  function parallax(L, cx, cy) {
    var reach = L.bodyW / 2;
    return {
      x: L.lift * (cx - L.cx) / reach,
      y: L.lift * (cy - L.cy) / reach,
    };
  }

  function control(id, L, spec, live) {
    var u = L.u, grow = spec.grow, rim = 0.45 * u;
    var off = parallax(L, spec.cx, spec.cy);
    // The base sits on the plate; the cap floats outwards off it, and the sliver
    // of base left showing on the inward side is the wall.
    var base = pillPath(spec.cx, spec.cy, spec.w, spec.h);
    var cap = pillPath(spec.cx + off.x, spec.cy + off.y, spec.w, spec.h);
    return [
      '<g class="ssd-ctl" data-ssd-channel="', spec.channel, '" data-ssd-control="', spec.name,
      '" style="--ssd-sink:', n(2.8 * u), 'px" role="button">',
      live ? "<title>" + spec.label + "</title>" : "",
      '<g class="ssd-shadow" opacity=".55" transform="translate(0 ', n(3.4 * u), ')">',
      '<path d="', base, '" fill="#000000" stroke="#000000" stroke-width="', n(grow * 2 + 2 * u),
      '" stroke-linejoin="round" filter="url(#', id, '-btnShadow)"/></g>',
      paint(base, "url(#" + id + "-wall)", grow),
      // Only the cap sinks under a press, into the wall it is standing on.
      '<g class="ssd-cap">',
      '<path d="', cap, '" fill="none" stroke="url(#', id,
      '-keyRim)" stroke-width="', n(grow * 2 + rim * 2), '" stroke-linejoin="round"/>',
      paint(cap, "url(#" + id + "-key)", grow),
      paint(cap, "url(#" + id + "-gloss)", grow, "ssd-gloss"),
      paint(cap, "url(#" + id + "-scene)", grow + rim),
      paint(cap, "#f7931a", grow, "ssd-hover"),
      paint(cap, "#000000", grow, "ssd-press"),
      "</g>",
      "</g>",
    ].join("");
  }

  // Five discrete keys in a diamond, and they are not all the same key turned
  // round: up and down are pills lying down, left and right are pills standing
  // up, and select is a true circle between them. The real device has no printed
  // glyphs on them, so neither does this; the accessible name carries the meaning.
  function padArt(id, L, live) {
    var u = L.u, cx = L.padCx, cy = L.cy;
    var armW = 105 * u, armH = 58 * u;   // up and down
    var sideW = 66 * u, sideH = 94 * u;  // left and right
    var mid = 103 * u;                   // select, as wide as it is tall
    var dx = 118 * u, dy = 116 * u;
    var grow = 3 * u;
    var out = [];

    // No well and no faceplate: on the real device these five caps stand
    // straight out of the flat top plate.
    var keys = [
      ["up", CHANNEL.up, "Up", cx, cy - dy, armW, armH],
      ["down", CHANNEL.down, "Down", cx, cy + dy, armW, armH],
      ["left", CHANNEL.left, "Left", cx - dx, cy, sideW, sideH],
      ["right", CHANNEL.right, "Right", cx + dx, cy, sideW, sideH],
      ["select", CHANNEL.select, "Select", cx, cy, mid, mid],
    ];
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      out.push(control(id, L, {
        name: k[0], channel: k[1], label: k[2], grow: grow,
        cx: k[3], cy: k[4], w: k[5], h: k[6],
      }, live));
    }
    return out.join("");
  }

  function keysArt(id, L, live) {
    var u = L.u, cx = L.keyCx, cy = L.cy;
    var kw = 100 * u, kh = 54 * u, gap = 109 * u;
    var grow = 3 * u;
    var out = [];
    var keys = [
      ["key1", CHANNEL.key1, "Key 1", cy - gap],
      ["key2", CHANNEL.key2, "Key 2", cy],
      ["key3", CHANNEL.key3, "Key 3", cy + gap],
    ];
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      out.push(control(id, L, {
        name: k[0], channel: k[1], label: k[2], grow: grow,
        cx: cx, cy: k[3], w: kw, h: kh,
      }, live));
    }
    return out.join("");
  }

  function render(container, options) {
    if (!container) throw new Error("SeedSignerDevice.render needs a container element");
    var o = options || {};
    var screenW = o.screenWidth > 0 ? o.screenWidth : 320;
    var screenH = o.screenHeight > 0 ? o.screenHeight : 240;
    var scale = o.scale > 0 ? o.scale : 2;
    var live = o.interactive !== false;
    var withCard = o.card !== false;
    var onKey = typeof o.onKey === "function" ? o.onKey : null;

    injectStyle();
    var id = "ssd" + (++instances);   // gradients and filters must not collide
    var L = layout(screenW, screenH, scale, withCard);

    var svg = [
      '<svg class="ssd-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ',
      n(L.viewW), " ", n(L.viewH), '" preserveAspectRatio="xMidYMid meet" role="img"',
      live ? "" : ' aria-hidden="true"', ">",
      "<title>SeedSigner Plus hardware wallet</title>",
      defs(id, L),
      withCard ? cardArt(id, L) : "",
      bodyArt(id, L),
      screenArt(id, L),
      padArt(id, L, live),
      keysArt(id, L, live),
      "</svg>",
    ].join("");

    // Percentages, not pixels: the slot has to keep registration with the art no
    // matter what width the page gives us. Pixel offsets were why the canvas
    // walked off the shell on a phone.
    var slotStyle = "left:" + pct(L.screenX, L.viewW) + ";top:" + pct(L.screenY, L.viewH) +
      ";width:" + pct(L.sw, L.viewW) + ";height:" + pct(L.sh, L.viewH) +
      ";border-radius:" + n(4 / L.viewW * 100) + "%";
    // Glass last and inert: it must never eat a click or hide the wallet's pixels.
    var glassStyle = slotStyle +
      ";background:linear-gradient(115deg,rgba(255,255,255,.075) 0%," +
      "rgba(255,255,255,.045) 13%,rgba(255,255,255,.012) 22%,rgba(255,255,255,0) 30%," +
      "rgba(255,255,255,0) 41%,rgba(255,255,255,.055) 47%,rgba(255,255,255,.012) 53%," +
      "rgba(255,255,255,0) 62%)" +
      ";box-shadow:inset 0 .8vw 1.6vw -.6vw rgba(0,0,0,.65)," +
      "inset 0 -.5vw 1.2vw -.8vw rgba(0,0,0,.5)";

    container.innerHTML = svg +
      '<div class="ssd-screen-slot" style="' + slotStyle + '"></div>' +
      '<div class="ssd-glass" style="' + glassStyle + '"></div>';
    container.classList.add("ssd-root");
    container.classList.toggle("ssd-live", live);
    // The natural width has to be a real length: the front page measures this
    // container with width:max-content before scaling it.
    container.style.width = n(L.viewW) + "px";
    // A landscape shell handed a whole desktop viewport is far wider than anyone
    // wants, so callers can cap it; either way it still shrinks to fit a phone.
    container.style.maxWidth = o.maxWidth ? "min(" + o.maxWidth + ",100%)" : "100%";
    // The shell's own proportions, published for a page that wants to fit it to
    // a viewport rather than only to a width.
    container.style.setProperty("--ssd-aspect", n(L.viewW / L.viewH));

    var svgEl = container.querySelector(".ssd-svg");
    var slotEl = container.querySelector(".ssd-screen-slot");
    // The screen is not a control. It used to be the select key, on the grounds
    // that it is the biggest target on the shell, and it surprised everybody who
    // touched it: on the home menu a tap anywhere opened the camera. A
    // SeedSigner has no touchscreen, so only the drawn keys answer here either.
    if (live && onKey) bindControls(svgEl, onKey);

    return {
      svg: svgEl,
      screen: slotEl,
      screenRect: { x: n(L.screenX), y: n(L.screenY), width: n(L.sw), height: n(L.sh) },
      width: n(L.viewW),
      height: n(L.viewH),
    };
  }

  global.SeedSignerDevice = { render: render, CHANNEL: CHANNEL };
})(typeof window !== "undefined" ? window : this);
