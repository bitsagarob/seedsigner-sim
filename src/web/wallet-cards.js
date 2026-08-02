// The card tray, loaded by both the page and the worker.
//
// Three simulated smartcards live in the wallet's Python. Which of them is in
// the reader is the user's business, and the user is on the page, so the tray
// has to reach into the worker. The worker is permanently blocked inside
// SeedSigner's controller loop and can never come back to its event loop to
// service a postMessage, so a SharedArrayBuffer is the only channel that works
// here -- the same one, for the same reason, as the buttons and the camera.
//
// Both halves live in this file because they have to agree byte-for-byte on the
// layout of that buffer, and a layout written down twice is a layout that
// drifts. The page loads this with a script tag, the worker with importScripts.

(function (scope) {
  "use strict";

  // Int32 header slots.
  var INSERTED = 0;    // index of the card in the reader, or EMPTY. Page -> worker.
  var EVENT_SEQ = 1;   // bumped on every insert and eject, so the worker can park on it
  var STATE_SEQ = 2;   // bumped when the wallet republishes what a card looks like
  var CARD_STATE = 3;  // one slot per card, packed by simulated_card.py

  var CARD_COUNT = 3;
  var EMPTY = -1;
  // A card the wallet has not read yet. Only ever visible for the moment between
  // the tray appearing and Python's first publish, during boot.
  var UNKNOWN = -1;

  // Labels are a rule rather than a list, so the Python side can derive the same
  // ones without a second copy to keep in step. See simulated_card.py.
  function labelFor(index) {
    return "Card " + String.fromCharCode(65 + index);
  }

  var HEADER_BYTES = 64;
  var TOTAL_BYTES = HEADER_BYTES;

  // How often the tray notices that the wallet has republished a card's state.
  // Nothing waits on this; it only keeps the pills honest.
  var REPAINT_MS = 250;

  function header(sab) {
    return new Int32Array(sab, 0, HEADER_BYTES / 4);
  }

  function createBuffer() {
    var sab = new SharedArrayBuffer(TOTAL_BYTES);
    var hdr = header(sab);
    // Zero would mean "card 0 is in the reader" and "every card is blank",
    // neither of which is true before anyone says so, so both are set here.
    Atomics.store(hdr, INSERTED, EMPTY);
    for (var i = 0; i < CARD_COUNT; i++) Atomics.store(hdr, CARD_STATE + i, UNKNOWN);
    return sab;
  }

  // The three things a user can tell apart about a card, in one Int32 so the
  // whole tray fits in the header. simulated_card.py is the only writer.
  function describe(state) {
    if (state === UNKNOWN) return { known: false, label: "—", tries: null };
    var setupDone = (state & 1) !== 0;
    var seeded = (state & 2) !== 0;
    var kind = seeded ? "seeded" : (setupDone ? "initialised" : "blank");
    return { known: true, label: kind, kind: kind, tries: (state >> 8) & 0xff };
  }

  // ---------------------------------------------------------------- page half

  var CSS = [
    ".cardtray{display:flex;flex-direction:column;align-items:center;gap:.75rem;",
    "width:100%;font:13px/1.4 ui-sans-serif,system-ui,sans-serif;color:#d7dbe0}",

    ".cardtray-reader{display:flex;align-items:center;gap:.6rem;padding:.4rem;",
    "background:#16181c;border:1px solid #2a2e35;border-radius:8px;",
    "width:19rem;max-width:100%}",
    ".cardtray-slot{flex:1;display:flex;align-items:center;gap:.5rem;padding:.35rem .6rem;",
    "background:#0b0c0e;border:1px solid #23272e;border-radius:5px;",
    "box-shadow:inset 0 2px 5px rgba(0,0,0,.7)}",
    ".cardtray-lamp{width:.5rem;height:.5rem;border-radius:50%;background:#3a3f47;flex:none}",
    ".cardtray--live .cardtray-lamp{background:#f7931a;box-shadow:0 0 6px #f7931a}",
    ".cardtray-slotlabel{color:#7c848f}",
    ".cardtray--live .cardtray-slotlabel{color:#f7931a}",

    ".cardtray-eject{font:inherit;color:#7c848f;background:#1d2026;border:1px solid #2a2e35;",
    "border-radius:5px;padding:.35rem .7rem;cursor:pointer}",
    ".cardtray-eject:hover:not(:disabled){color:#d7dbe0;border-color:#3a3f47}",
    ".cardtray-eject:disabled{opacity:.35;cursor:default}",

    // Three cards at their natural width overflow a phone, so they shrink
    // together rather than the third one falling off the side.
    ".cardtray-row{display:flex;gap:.75rem;max-width:100%;justify-content:center}",
    // A definite width, not a flex-basis: the row's max-content size has to come
    // from this number, or the cards collapse to their text width at any size.
    ".cardtray-card{font:inherit;text-align:left;cursor:pointer;padding:.55rem .6rem;",
    "width:7.4rem;flex-shrink:1;min-width:0;",
    "display:flex;flex-direction:column;gap:.4rem;border-radius:7px;",
    "background:linear-gradient(160deg,#20242b,#15171b);border:1px solid #2f343c;",
    "color:#d7dbe0;transition:transform .12s ease,border-color .12s ease,box-shadow .12s ease}",
    ".cardtray-card:hover{border-color:#4a515c;transform:translateY(-2px)}",
    ".cardtray-card[aria-pressed=true]{border-color:#f7931a;transform:translateY(-7px);",
    "box-shadow:0 6px 14px rgba(0,0,0,.55),0 0 0 1px rgba(247,147,26,.35)}",
    ".cardtray-card:focus-visible{outline:2px solid #f7931a;outline-offset:2px}",

    ".cardtray-top{display:flex;align-items:center;justify-content:space-between;gap:.4rem}",
    ".cardtray-name{font-weight:600}",
    ".cardtray-in{font-size:.72rem;letter-spacing:.08em;color:#f7931a;visibility:hidden}",
    ".cardtray-card[aria-pressed=true] .cardtray-in{visibility:visible}",
    ".cardtray-pill{align-self:flex-start;font-size:.75rem;padding:.05rem .45rem;border-radius:999px;",
    "border:1px solid #363b44;color:#8b939e;background:#0e1013}",
    ".cardtray-pill[data-kind=initialised]{color:#9fb4d0;border-color:#3c4c63}",
    ".cardtray-pill[data-kind=seeded]{color:#f7931a;border-color:#6b4614;background:#1a1206}",
    ".cardtray-tries{font-size:.72rem;color:#7c848f;min-height:1em}"
  ].join("");

  var CHIP =
    '<svg width="26" height="20" viewBox="0 0 26 20" aria-hidden="true" focusable="false">' +
    '<rect x=".5" y=".5" width="25" height="19" rx="3" fill="#c9973a"/>' +
    '<rect x="2.5" y="2.5" width="21" height="15" rx="2" fill="#e0b25c"/>' +
    '<path d="M9 2.5v15M17 2.5v15M2.5 7h21M2.5 13h21" stroke="#a87e2c" stroke-width="1"/>' +
    "</svg>";

  function runPage(sab, container) {
    var root = typeof container === "string" ? document.getElementById(container) : container;
    if (!root) throw new Error("CardTray.runPage: no container");
    var hdr = header(sab);

    if (!document.getElementById("cardtray-css")) {
      var style = document.createElement("style");
      style.id = "cardtray-css";
      style.textContent = CSS;
      document.head.appendChild(style);
    }

    var tray = document.createElement("div");
    tray.className = "cardtray";

    var reader = document.createElement("div");
    reader.className = "cardtray-reader";
    reader.innerHTML =
      '<div class="cardtray-slot"><span class="cardtray-lamp"></span>' +
      '<span class="cardtray-slotlabel">Card reader empty</span></div>' +
      '<button type="button" class="cardtray-eject">Eject</button>';
    var slotLabel = reader.querySelector(".cardtray-slotlabel");
    var ejectButton = reader.querySelector(".cardtray-eject");

    var row = document.createElement("div");
    row.className = "cardtray-row";

    var cards = [];
    for (var i = 0; i < CARD_COUNT; i++) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "cardtray-card";
      button.dataset.index = String(i);
      button.setAttribute("aria-pressed", "false");
      button.innerHTML =
        '<span class="cardtray-top">' + CHIP + '<span class="cardtray-in">IN</span></span>' +
        '<span class="cardtray-name">' + labelFor(i) + "</span>" +
        '<span class="cardtray-pill">—</span>' +
        '<span class="cardtray-tries"></span>';
      row.appendChild(button);
      cards.push({
        button: button,
        pill: button.querySelector(".cardtray-pill"),
        tries: button.querySelector(".cardtray-tries")
      });
    }

    tray.appendChild(reader);
    tray.appendChild(row);
    root.appendChild(tray);

    function inserted() { return Atomics.load(hdr, INSERTED); }

    // It is one reader, so this is a move rather than an add: whatever was in it
    // comes out in the same breath.
    function move(index) {
      Atomics.store(hdr, INSERTED, index);
      // Bumped last: the sequence number is what tells a parked worker the slot
      // above is worth re-reading.
      Atomics.add(hdr, EVENT_SEQ, 1);
      Atomics.notify(hdr, EVENT_SEQ);
      paint();
    }

    function insert(index) { move(index); }
    function eject() { move(EMPTY); }
    function toggle(index) { move(inserted() === index ? EMPTY : index); }

    function paint() {
      var current = inserted();
      tray.classList.toggle("cardtray--live", current !== EMPTY);
      slotLabel.textContent = current === EMPTY ? "Card reader empty" : labelFor(current) + " inserted";
      ejectButton.disabled = current === EMPTY;

      for (var i = 0; i < CARD_COUNT; i++) {
        var card = cards[i];
        var state = describe(Atomics.load(hdr, CARD_STATE + i));
        card.button.setAttribute("aria-pressed", String(i === current));
        card.pill.textContent = state.label;
        if (state.known) card.pill.dataset.kind = state.kind;
        else delete card.pill.dataset.kind;
        // Only worth saying while it is news: a card with every try left is not.
        card.tries.textContent =
          state.tries !== null && state.tries < 5 ? state.tries + " PIN tries left" : "";
      }
    }

    row.addEventListener("click", function (event) {
      var button = event.target.closest(".cardtray-card");
      if (!button) return;
      toggle(Number(button.dataset.index));
      // Clicking focuses the button, and a focused button keeps Enter (below).
      // Handing focus back means the wallet still answers the keyboard right
      // after a card has been put in, which is exactly when it is wanted.
      button.blur();
    });
    ejectButton.addEventListener("click", function () {
      eject();
      ejectButton.blur();
    });

    // The wallet listens for Enter and Space on the document and calls
    // preventDefault on them, which would stop a tray button reached by Tab from
    // ever seeing its own activation. Keeping those two keys inside the tray is
    // enough; everything else still reaches the wallet, and after a click focus
    // is not in here anyway.
    tray.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") event.stopPropagation();
    });

    var lastSeen = -1;
    setInterval(function () {
      var seq = Atomics.load(hdr, STATE_SEQ);
      if (seq !== lastSeen) {
        lastSeen = seq;
        paint();
      }
    }, REPAINT_MS);

    paint();
    return { insert: insert, eject: eject, inserted: inserted, count: CARD_COUNT };
  }

  // -------------------------------------------------------------- worker half

  function forWorker(sab) {
    var hdr = header(sab);

    return {
      count: CARD_COUNT,
      empty: EMPTY,

      // Which card is in the reader right now, read fresh every time: the tray
      // writes it from the page's own thread and never tells anyone directly.
      inserted: function () { return Atomics.load(hdr, INSERTED); },

      // Parks until the user touches the tray, so a wallet waiting for a card
      // waits at the user's pace rather than spinning. Bounded, because this is
      // the only thread the wallet has and the caller is the one that knows how
      // long it can afford to be gone.
      wait: function (timeoutMs) {
        var seq = Atomics.load(hdr, EVENT_SEQ);
        Atomics.wait(hdr, EVENT_SEQ, seq, timeoutMs);
        return Atomics.load(hdr, INSERTED);
      },

      // What the tray shows about a card. The cards live in Python, so this is
      // the one thing that flows worker -> page.
      publish: function (index, state) {
        Atomics.store(hdr, CARD_STATE + index, state);
        Atomics.add(hdr, STATE_SEQ, 1);
      }
    };
  }

  scope.CardTray = {
    createBuffer: createBuffer,
    runPage: runPage,
    forWorker: forWorker
  };
})(typeof self !== "undefined" ? self : this);
