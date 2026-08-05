// The Simulator wallet: the half of a wallet a signing device deliberately is not.
//
// A SeedSigner holds keys and signs with them. The addresses, the balance and
// the transaction being built live somewhere else, and until this panel existed
// the page had no somewhere else: a visitor could load a seed and then stand at
// a dead end. So this is the missing half, and it is beside the device rather
// than on it. The device gains no balance, no wallet view and no new screen,
// because gaining one would be as wrong as making its screen a button.
//
// Everything between the two crosses as a QR code, both ways, exactly as the
// multisig tutorial already proves it can: the panel reads the device's screen
// with the page's own jsQR, and when the device wants its camera the panel hands
// over its own canvas as the picture. No webcam, no framing, no focus, and the
// code is in view the instant it is shown -- that much realism is dropped on
// purpose. What is not dropped is any step: the device's own scan and review
// screens happen for real, in front of you, because those are the thing worth
// learning.
//
// The chain half is Bitsaga Signet, through signet-coordinator.js and the same
// origin it already uses. Nothing here derives a key or serialises a
// transaction itself; that work belongs to the coordinator, and where the
// coordinator cannot yet do it the panel says so and stops rather than pretending.

(function (scope) {
  "use strict";

  var C = scope.SignetCoordinator;

  // What this panel needs from the coordinator beyond what the multisig
  // tutorial already uses. Checked by name rather than assumed, because the
  // single sig half is newer than this file and a missing function should read
  // as a sentence in the panel rather than as a stack trace in the console.
  var NEEDS = ["parseAccount", "singleSigWallet", "deriveAddressSingle",
               "buildPsbtSingle", "finaliseSingle"];

  // A wallet looks twenty addresses ahead on each branch and stops, which is
  // what every wallet means by a gap limit. Forty addresses is one /scan call
  // with room to spare under its cap of sixty, so a refresh is one request.
  var GAP = 20;

  // Bitsaga Signet is not busy and nothing here is bidding for space. Two
  // sat/vB is above the relay minimum and small enough that the fee never
  // becomes the interesting number on screen.
  var FEE_RATE = 2;

  // Below this an output is dust and the network will not relay it.
  var DUST = 546;

  // How often the balance is asked for. The scan endpoint counts requests per
  // address per hour and a panel left open all afternoon should not be what
  // exhausts that, so waiting for a block is done by asking for the proof of
  // one transaction instead, which is what the tutorial already does.
  var REFRESH_MS = 20000;
  var PROOF_MS = 3000;

  // How long a code stays up before the next one. Same as the tutorial, which
  // is what the device's decoder was watched reading.
  var FRAME_MS = 550;

  var NOT_REAL = "These are not real bitcoin. They exist only on that test "
               + "network, cannot be sold or sent to anyone, and are worth nothing.";

  // The one sentence that has to be true whatever else the panel is showing.
  var NOT_A_WALLET = "Not a real wallet. Bitsaga Signet test coins only, no "
                   + "keys, and it forgets everything when you close the tab.";

  // The device path to the account key, spelled out because the whole point of
  // the landing state is that nobody has to guess it. It ends where the device's
  // own default ends: the animated ur:crypto-account it offers first is read
  // here now, so there is no extra keypress to ask for and nowhere the path
  // steers a visitor away from what the device wanted to do. Static is still
  // read, it just no longer has to be named.
  var SEED_PATH = "Tools → New seed";
  var EXPORT_PATH = "Seeds → your seed → Export Xpub → Single sig "
                  + "→ Native Segwit";

  var WAITING = "Waiting for Bitsaga Signet to put it in a block, about thirty seconds.";

  // What the one control says, in both of its states. It is a disclosure
  // button, so the label is the action and aria-expanded carries the state.
  var OPEN_LABEL = "Open wallet";
  var SHUT_LABEL = "Close wallet";

  // A wallet: the body, and the pocket a card slides into on its right edge.
  // Drawn here rather than fetched, in the stroke idiom the page's other icon
  // already uses, and deliberately not a flat rectangle with a stripe -- that
  // is a bank card, and there are three of those in the tray below this.
  var WALLET_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
    '<rect x="3" y="5.5" width="18" height="13" rx="2.6"></rect>' +
    '<path d="M21 10.4h-3.3a2 2 0 0 0 0 4H21"></path>' +
    '</svg>';

  // ---------------------------------------------------------------- the panel

  // The page's own idiom, and nothing new: the tutorial panel's fill, border and
  // radius, the card tray's buttons, orange only where something is on or has
  // come out right. What is different is the shape of the thing, which is
  // BlueWallet's rather than Sparrow's: one big number, a short list, two
  // controls, and air around all of it.
  var CSS = [
    // The control that opens it, in the flow under the device with the rest of
    // the page's controls. It used to be a strip fixed to the right edge of the
    // viewport, which left the page at rest exactly as it had been -- and which
    // nobody pressed, because a vertical label pinned to the side of a screen
    // does not read as the way into the only thing here that can hold a
    // balance. Orange, because it is the one action on this page worth taking;
    // the firmware row under it is grey, and is a preference rather than an act.
    // The control, and beside it what the wallet is doing. They were one
    // object, which made the button's own name change under a screen reader
    // every time the balance moved, and read as a status chip somebody had put
    // a border round. Wrapping, because a phone cannot hold both on one line.
    ".wal-openrow{display:flex;flex-wrap:wrap;align-items:center;",
    "justify-content:center;gap:.4rem .7rem}",
    ".wal-open{display:inline-flex;align-items:center;gap:.5rem;font:inherit;",
    "font-size:.9rem;color:#f7931a;background:#16181c;border:1px solid #f7931a;",
    "border-radius:8px;padding:.55rem 1.1rem;cursor:pointer}",
    ".wal-open:hover{background:#1c2026}",
    ".wal-open:focus-visible{outline:2px solid #f7931a;outline-offset:2px}",
    ".wal-open b{font-weight:600}",
    // The page's own icon idiom, taken from the fullscreen control: strokes
    // that inherit the label's colour, so there is no second rule to keep in
    // step with it.
    ".wal-open svg{width:1.1em;height:1.1em;flex:none;fill:none;",
    "stroke:currentColor;stroke-width:1.6;stroke-linecap:round;",
    "stroke-linejoin:round}",
    ".wal-status{margin:0;display:flex;align-items:center;gap:.4rem;",
    "color:#7c848f;font-size:.85rem}",
    ".wal-status .wal-dot{width:.45rem;height:.45rem;",
    "flex:none;border-radius:50%;background:#3a4048}",
    ".wal-status[data-on=yes] .wal-dot{background:#f7931a}",
    // It stays where it is when the drawer opens, and closes it again. The
    // strip was hidden while open, which cost nothing because it floated over
    // the page; a button in the flow that vanished would take the row under it
    // up the page every time the drawer moved. The panel's own Close stays,
    // being the one that sits next to what it closes.

    // The panel itself, in the column the tutorial panel would have had.
    ".wal{width:min(46rem,100%);box-sizing:border-box;background:#12151a;",
    "border:1px solid #2a2f36;border-radius:10px;padding:1.1rem 1.25rem 1.35rem;",
    "color:#b6bec8;text-align:left}",
    ".wal:focus{outline:none}",
    ".wal-head{display:flex;align-items:baseline;justify-content:space-between;gap:.9rem}",
    ".wal h2{font-size:1rem;font-weight:600;color:#d7dbe0;margin:0}",
    ".wal-note{margin:.35rem 0 0;font-size:.82rem;color:#7c848f}",

    ".wal button{font:inherit;font-size:.88rem;color:#8b939e;background:#1d2026;",
    "border:1px solid #2a2e35;border-radius:5px;padding:.25rem .7rem;cursor:pointer}",
    ".wal button:hover:not(:disabled){color:#d7dbe0;border-color:#3a3f47}",
    ".wal button:disabled{opacity:.4;cursor:default}",
    ".wal button:focus-visible{outline:2px solid #f7931a;outline-offset:2px}",
    ".wal .primary{color:#f7931a;border-color:#f7931a;background:#16181c;",
    "padding:.5rem 1.1rem;font-size:.95rem}",
    ".wal .primary:hover:not(:disabled){background:#1c2026;color:#f7931a}",
    ".wal-actions{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin:1.1rem 0 0}",
    // The i, and what it holds. Out of the flow so opening it does not shove
    // the buttons under it around.
    ".wal-info{position:relative;font-size:.82rem}",
    ".wal-info>summary{list-style:none;cursor:pointer;width:1.35rem;height:1.35rem;",
    "border-radius:50%;border:1px solid #3a4048;color:#9aa3ae;display:grid;",
    "place-items:center;font:italic 600 .85rem/1 serif}",
    ".wal-info>summary::-webkit-details-marker{display:none}",
    ".wal-info>summary:hover{color:#f7931a;border-color:#f7931a}",
    ".wal-info[open]>summary{color:#f7931a;border-color:#f7931a}",
    ".wal-info>div{position:absolute;z-index:12;left:0;top:1.8rem;width:min(24rem,70vw);",
    "background:#0d1014;border:1px solid #2a2f36;border-radius:8px;padding:.6rem .75rem;",
    "color:#b6bec8;line-height:1.55;box-shadow:0 .8rem 1.6rem rgba(0,0,0,.55)}",

    // The balance. The one number worth a size of its own.
    // The address check the device asks for right after an export.
    ".wal-verify{margin:1.2rem 0 0;padding:.8rem .9rem;border:1px solid #f7931a;",
    "border-radius:8px;background:#16181c}",
    ".wal-verify-head{margin:0;font-weight:600;color:#f7931a}",
    ".wal-verify-say{margin:.45rem 0 0;font-size:.85rem;color:#9aa3ae}",
    ".wal-verify .wal-actions{margin:.7rem 0 0}",
    ".wal-balance{margin:1.4rem 0 0;font-size:2rem;line-height:1.15;font-weight:600;",
    "color:#d7dbe0;letter-spacing:-.01em}",
    ".wal-balance span{font-size:1rem;font-weight:400;color:#7c848f;margin-left:.35rem}",
    ".wal-pending{margin:.3rem 0 0;font-size:.85rem;color:#f7931a}",
    ".wal-pending:empty{display:none}",

    ".wal-say{margin:1rem 0 0}",
    ".wal-say:empty{display:none}",
    ".wal-path{display:block;margin:.35rem 0 0;padding:.55rem .75rem;",
    "border-left:2px solid #f7931a;background:#16181c;color:#d7dbe0;",
    "overflow-wrap:anywhere}",
    // The numbers are the point, so they are the one thing in the list that is
    // not grey: this is a sequence, and it used to be three paragraphs that had
    // to be read to find that out.
    // How to connect, as a numbered list. Not .wal-steps, which is the send's
    // own progress list further down and owns that name; and not a flex column,
    // which was the first attempt and why the numbers did not appear at all --
    // flex items are not list items, and ::marker only exists on a list item.
    ".wal-howto{margin:.9rem 0 0;padding:0 0 0 1.5rem;list-style:decimal}",
    ".wal-howto li{margin:0 0 .85rem}",
    ".wal-howto li:last-child{margin-bottom:0}",
    ".wal-howto li::marker{color:#f7931a;font-weight:600}",
    ".wal-bad{margin:1rem 0 0;border:1px solid #7f1d1d;background:#1b0f10;color:#ef4444;",
    "border-radius:6px;padding:.5rem .7rem;overflow-wrap:anywhere}",
    ".wal-bad:empty{display:none}",

    // The transaction list: what came in, what went out, and which of them the
    // chain has actually accepted. Nothing else; there is no UTXO view here and
    // no address book, because a wallet this small has no use for either.
    ".wal-list{list-style:none;padding:0;margin:1.2rem 0 0;",
    "border-top:1px solid #2a2f36}",
    ".wal-list li{display:flex;justify-content:space-between;gap:.9rem;",
    "padding:.6rem 0;border-bottom:1px solid #2a2f36;font-size:.9rem}",
    ".wal-list .what{color:#d7dbe0}",
    ".wal-list .when{display:block;font-size:.8rem;color:#7c848f;margin-top:.15rem}",
    ".wal-list .amount{white-space:nowrap;color:#d7dbe0}",
    ".wal-list li[data-state=pending] .amount,",
    ".wal-list li[data-state=pending] .when{color:#f7931a}",

    // The steps of a send, which stay on screen because they are the lesson.
    ".wal-steps{list-style:none;padding:0;margin:1rem 0 0;font-size:.88rem}",
    ".wal-steps li{padding:.25rem 0 .25rem .9rem;border-left:2px solid #2a2f36;color:#7c848f}",
    ".wal-steps li[data-state=now]{border-color:#f7931a;color:#d7dbe0}",
    ".wal-steps li[data-state=done]{border-color:#3a4048;color:#8b939e}",

    ".wal-field{margin:1rem 0 0}",
    ".wal-field label{display:block;font-size:.82rem;color:#7c848f;margin-bottom:.25rem}",
    ".wal-field input{width:100%;box-sizing:border-box;font:inherit;font-size:.9rem;",
    "color:#d7dbe0;background:#0b0c0e;border:1px solid #2a2e35;border-radius:5px;",
    "padding:.45rem .6rem}",
    ".wal-field input:focus{outline:2px solid #f7931a;outline-offset:1px}",
    ".wal-field button{margin-top:.45rem}",
    ".wal-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85rem;",
    "overflow-wrap:anywhere;color:#d7dbe0}",

    // The code the device is pointed at. White, square and big enough that the
    // decoder reading it is doing the same job it would do through a lens.
    ".wal-qr{display:block;width:min(15rem,100%);margin:1rem auto 0;background:#fff;",
    "border-radius:6px}",
    ".wal-qr[hidden]{display:none}",

    // Collapsed and open are two elements, not one that changes shape: a strip
    // pinned to the viewport and a panel in the page's own column cannot be
    // transitioned into each other, and pretending otherwise is where drawers
    // start fighting the layout. The panel slides, the strip does not move.
    "@media (prefers-reduced-motion: no-preference){",
    ".wal{transition:transform .22s ease,opacity .22s ease}",
    "}",
    ".wal[data-anim=in]{transform:translateX(2rem);opacity:0}",
    ".wal[data-anim=out]{transform:translateX(100%);opacity:0}",

    // Below the split the panel comes up rather than in. The button needs
    // nothing of its own here: it is one line in a centred column either way.
    "@media (max-width:61.99rem){",
    ".wal[data-anim=in]{transform:translateY(2rem)}",
    ".wal[data-anim=out]{transform:translateY(100%)}",
    "}",

    "@media (max-width:30rem){",
    ".wal{padding:.9rem .85rem 1.1rem}",
    ".wal-balance{font-size:1.7rem}",
    "}",
  ].join("");

  // ------------------------------------------------------------ an address in
  //
  // Everything else in this panel works in addresses the coordinator derived,
  // and knows the script that goes with each because it derived that too. A
  // typed address is the one thing that arrives as characters, and a
  // transaction cannot be built out of characters, so this reads one back into
  // the script it pays to. The checksum is the reason to do it here rather than
  // trust the field: a mistyped address that still decodes would send coins
  // nowhere, and bech32 exists so that it does not decode.
  //
  // Segwit v0 on this chain and nothing else, which is every address this
  // wallet or its faucet can produce. Anything else is refused with a reason
  // rather than guessed at.
  var BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";

  function bech32Polymod(values) {
    var GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
    var check = 1;
    for (var i = 0; i < values.length; i++) {
      var top = check >>> 25;
      check = ((check & 0x1ffffff) << 5) ^ values[i];
      for (var b = 0; b < 5; b++) if ((top >> b) & 1) check ^= GEN[b];
    }
    return check >>> 0;
  }

  function addressScript(text) {
    var address = String(text).trim();
    if (address !== address.toLowerCase() && address !== address.toUpperCase()) {
      throw new Error("That address mixes upper and lower case, which no address does.");
    }
    address = address.toLowerCase();
    if (address.indexOf("tb1") !== 0) {
      throw new Error("Addresses on Bitsaga Signet begin with tb1.");
    }
    var body = address.slice(3);
    var values = [];
    for (var i = 0; i < body.length; i++) {
      var value = BECH32.indexOf(body[i]);
      if (value === -1) throw new Error("That address has a character no address can have.");
      values.push(value);
    }
    // The human readable part expanded the way BIP173 expands it, then the
    // data, then the six characters of checksum that have to come to 1.
    var hrp = [116 >> 5, 98 >> 5, 0, 116 & 31, 98 & 31];
    if (values.length < 7 || bech32Polymod(hrp.concat(values)) !== 1) {
      throw new Error("That address does not check out. One character of it is wrong.");
    }
    if (values[0] !== 0) throw new Error("This wallet can only pay segwit v0 addresses.");
    var bits = 0, acc = 0, program = [];
    values.slice(1, values.length - 6).forEach(function (value) {
      acc = (acc << 5) | value;
      bits += 5;
      while (bits >= 8) {
        bits -= 8;
        program.push((acc >> bits) & 0xff);
      }
    });
    if (program.length !== 20 && program.length !== 32) {
      throw new Error("That is not the right length for a segwit address.");
    }
    return new Uint8Array([0x00, program.length].concat(program));
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function sats(value) {
    return Number(value).toLocaleString("en-GB") + " sats";
  }

  // Counted if wallet-track.js is on the page, ignored if it is not, exactly as
  // the tutorial counts itself.
  function track(action, name) {
    if (scope.Track) scope.Track.event("wallet", action, name);
  }

  // ------------------------------------------------------------- the chain

  // The one call this panel makes that the tutorial does not: what the chain
  // holds against a list of addresses. Same origin as /status and /claim,
  // through the coordinator's own base, so the page's content security policy
  // does not grow a host. Twenty seconds for the same reason the coordinator
  // waits twenty: a request that never answers leaves a balance that never
  // arrives and nothing on screen saying why.
  function scanChain(addresses) {
    if (!C || !C.api) {
      return Promise.reject(new Error("signet-coordinator.js is not on this page, "
                                      + "so there is nothing to ask the chain with."));
    }
    var giveUp = new AbortController();
    var timer = setTimeout(function () { giveUp.abort(); }, 20000);
    return fetch(C.api + "/scan", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ addresses: addresses }),
      signal: giveUp.signal,
    }).then(function (response) {
      clearTimeout(timer);
      return response.json().catch(function () {
        throw new Error("Bitsaga Signet answered with something that is not JSON");
      }).then(function (body) {
        if (!response.ok) throw new Error(body.error || ("Bitsaga Signet said " + response.status));
        if (!body.addresses) throw new Error("Bitsaga Signet did not say what these addresses hold");
        return body.addresses;
      });
    }, function () {
      clearTimeout(timer);
      throw new Error(giveUp.signal.aborted
        ? "Bitsaga Signet did not answer in time"
        : "Bitsaga Signet is not reachable from this browser");
    });
  }

  // ------------------------------------------------------------- the machine

  function Wallet(options) {
    this.screen = options.screen;
    this.lines = [];              // the device's own narration, for the send flow
    this.open = false;
    this.stage = "idle";          // idle | connecting | ready
    this.view = "balance";        // balance | receive | send
    this.step = null;             // which step of a send is in hand
    this.error = "";
    this.progress = "";
    this.wallet = null;           // what singleSigWallet gave back
    this.records = [];            // every derived address, both branches
    this.chain = {};              // address -> what the chain holds against it
    this.sent = [];               // spends this tab made, so the list can name them
    this.presenting = null;       // the frames the device's camera is being shown
    this.reader = 0;              // bumped to call off whatever is being read
    this.build(options.container);
  }

  Wallet.prototype.build = function (container) {
    var self = this;
    var style = element("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    this.opener = element("button", "wal-open");
    this.opener.type = "button";
    this.opener.id = "wallet-button";
    this.opener.setAttribute("aria-expanded", "false");
    this.opener.setAttribute("aria-controls", "wallet");
    this.opener.insertAdjacentHTML("afterbegin", WALLET_ICON);
    // The label says what pressing it does, not what the thing behind it is
    // called. "Simulator wallet" beside a dot and a state reads as a status
    // chip, and a status chip is exactly what nobody pressed.
    this.openerLabel = element("b", null, OPEN_LABEL);
    this.opener.appendChild(this.openerLabel);
    this.opener.addEventListener("click", function () { self.toggle(); });

    // What the wallet is doing, beside the button rather than inside it. It is
    // not part of the action and it changes on its own, which is what a status
    // is: role=status so a reader is told when it changes, and the dot is here
    // with the words it belongs to rather than in front of the label.
    this.openerStatus = element("p", "wal-status");
    this.openerStatus.setAttribute("role", "status");
    this.openerStatus.dataset.on = "no";
    this.openerStatus.appendChild(element("span", "wal-dot"));
    this.openerState = element("span", "wal-state", "Not connected");
    this.openerStatus.appendChild(this.openerState);

    var row = element("div", "wal-openrow");
    row.appendChild(this.opener);
    row.appendChild(this.openerStatus);
    // Into the footer's own slot when the page offers one, which puts it under
    // the device beside the firmware row and the card tray. Falling back to the
    // body keeps this module usable on a page that has made no room for it.
    (document.getElementById("wallet-open-slot") || document.body).appendChild(row);

    this.root = element("section", "wal");
    this.root.id = "wallet";
    this.root.hidden = true;
    this.root.tabIndex = -1;
    this.root.setAttribute("aria-labelledby", "wal-title");

    var head = element("div", "wal-head");
    var title = element("h2", null, "Simulator wallet");
    title.id = "wal-title";
    head.appendChild(title);
    this.closeButton = element("button", null, "Close");
    this.closeButton.type = "button";
    this.closeButton.setAttribute("aria-label", "Close the simulator wallet");
    this.closeButton.addEventListener("click", function () { self.toggle(false); });
    head.appendChild(this.closeButton);

    this.body = element("div", "wal-body");

    this.root.appendChild(head);
    this.root.appendChild(element("p", "wal-note", NOT_A_WALLET));
    this.root.appendChild(this.body);
    container.appendChild(this.root);

    // One canvas for both directions of the QR trade: the address a payment
    // should go to, and the transaction the device is asked to sign. Sized like
    // the tutorial's phone, because the device's camera path captures 640 by 480
    // and a code drawn smaller than that is a code being scaled up to be read.
    this.canvas = element("canvas", "wal-qr");
    this.canvas.width = 640;
    this.canvas.height = 480;
    this.canvas.hidden = true;
    this.painter = this.canvas.getContext("2d", { willReadFrequently: true });

  };

  // ------------------------------------------------------------ open and shut

  Wallet.prototype.toggle = function (want) {
    var self = this;
    var open = want === undefined ? !this.open : want;
    if (open === this.open) return;
    this.open = open;
    this.opener.setAttribute("aria-expanded", String(open));
    this.openerLabel.textContent = open ? SHUT_LABEL : OPEN_LABEL;
    track(open ? "open" : "close", this.stage);

    var still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (open) {
      document.body.classList.add("wallet-open", "paired");
      this.root.hidden = false;
      this.root.dataset.anim = "in";
      // Two frames: the browser has to have laid the panel out in its offset
      // state before the state is taken away, or there is nothing to move from.
      requestAnimationFrame(function () {
        requestAnimationFrame(function () { delete self.root.dataset.anim; });
      });
      this.root.focus();
      this.began();
    } else {
      this.root.dataset.anim = "out";
      var hide = function () {
        if (self.open) return;                 // opened again mid-slide
        self.root.hidden = true;
        delete self.root.dataset.anim;
        document.body.classList.remove("wallet-open", "paired");
        // After the strip is back on the page and not before: focus cannot land
        // on something that is still display:none, and a keyboard left on the
        // body has nowhere obvious to go next.
        self.opener.focus();
      };
      if (still) hide(); else setTimeout(hide, 220);
      this.stopReading();
      this.stopPresenting();
      // A send is a conversation with the device, and shutting the drawer ends
      // it: the code is no longer being held up and nothing is watching the
      // screen for an answer. Said, rather than left as a step that has quietly
      // stopped moving. Nothing was signed or sent, so nothing is lost.
      if (this.step && this.step !== "done") {
        this.step = null;
        this.view = "balance";
        this.sending = null;
        this.error = "That send stopped when the wallet was closed. Nothing was "
                   + "signed or sent. Start it again when you are ready.";
      }
    }
  };

  /** What opening the drawer starts, which depends on how far it already got. */
  Wallet.prototype.began = function () {
    var self = this;
    this.render();
    if (this.stage === "idle") this.watchForAccount();
    if (this.stage === "ready") this.refreshSoon();
    // The first question this page ever asks Bitsaga Signet, and it is not
    // asked until a visitor has opened the wallet: a page that reached out to
    // the network before anybody wanted anything would make the sentence in its
    // own about panel untrue. Failing is silent, because nothing has been asked
    // for yet; what fails while something is being asked for is said out loud.
    if (!this.status && C && C.network) {
      C.network.status().then(function (status) { self.status = status; }).catch(function () {});
    }
  };

  // --------------------------------------------------------- reading the device

  /** Whatever QR is on the device's screen, read with the page's own jsQR. */
  Wallet.prototype.readDevice = function () {
    if (!scope.jsQR || !this.screen) return null;
    var context = this.screen.getContext("2d");
    var image = context.getImageData(0, 0, 240, 240);
    var found = scope.jsQR(image.data, image.width, image.height);
    return found && found.data ? found.data : null;
  };

  /**
   * Watch the device's screen until a predicate is happy, or give up.
   *
   * One reader at a time and never two: a generation is taken at the start and
   * checked at every tick, so anything left running when the drawer shuts or
   * the flow moves on simply stops rather than writing into a state it no
   * longer belongs to.
   */
  /**
   * A small i holding one paragraph, for the things worth having available and
   * not worth reading twice. Same circle as the header's and the network
   * label's, so a visitor learns it once.
   */
  Wallet.prototype.info = function (text) {
    var box = element("details", "wal-info");
    var mark = element("summary", null, "i");
    mark.setAttribute("aria-label", "More about this");
    mark.title = "More about this";
    box.appendChild(mark);
    box.appendChild(element("div", null, text));
    return box;
  };

  Wallet.prototype.watch = function (test, timeout, what) {
    var self = this;
    var mine = ++this.reader;
    var deadline = Date.now() + timeout;
    return new Promise(function (resolve, reject) {
      (function tick() {
        if (mine !== self.reader) return;
        var value;
        try {
          value = test();
        } catch (error) {
          return reject(error);
        }
        if (value) return resolve(value);
        if (Date.now() > deadline) return reject(new Error("Nothing arrived while waiting for " + what));
        // The rate the tutorial reads the same screen at. An animated code the
        // device is cycling has to be looked at faster than it changes, and a
        // fountain code that is missed costs a whole turn of the cycle.
        setTimeout(tick, 150);
      })();
    });
  };

  Wallet.prototype.stopReading = function () {
    this.reader++;
  };

  /**
   * Wait for the device to show a whole ur:crypto-psbt, saying how far it is.
   *
   * The count is codes actually read out of the number this transfer turned out
   * to have, which the fountain decoder knows the moment the first part lands.
   * Nothing here is a guess or a timer pretending to be one.
   */
  /**
   * Give a code to a collector, and shrug at the ones that arrive broken.
   *
   * jsQR is reading a canvas the device is repainting, so now and then it
   * returns a frame caught mid-redraw: half a code, a byteword that is not one,
   * a checksum over bytes that were never all on screen at once. The decoder is
   * right to refuse those. What was wrong was where the refusal went: watch()
   * turns any throw into a rejection and the account watcher swallows it, so one
   * bad frame stopped the panel looking for good ones, silently and for ever.
   *
   * A static export survived that because it is one clean read. An animated one
   * is dozens, so it only had to be unlucky once, which is exactly how it
   * looked: static connects, animated never does.
   */
  function feed(collector, text) {
    try {
      collector.receive(text);
      return true;
    } catch (error) {
      return false;
    }
  }

  Wallet.prototype.readPsbt = function (timeout) {
    var self = this;
    var collector = null;
    return this.watch(function () {
      var text = self.readDevice();
      if (!text || text.toLowerCase().indexOf("ur:crypto-psbt/") !== 0) return false;
      collector = collector || scope.URDecode.collector();
      if (!feed(collector, text)) return false;
      if (collector.parts()) {
        self.say("Code " + collector.have() + " of " + collector.parts() + ".");
      }
      if (!collector.done()) return false;
      return collector;
    }, timeout || 300000, "the signature on the device's screen");
  };

  // --------------------------------------------------- showing the device a QR

  Wallet.prototype.paint = function (matrix) {
    var modules = matrix.length;
    var scale = Math.floor(Math.min(640, 480) * 3 / 4 / modules);
    var size = modules * scale;
    var left = Math.floor((640 - size) / 2), top = Math.floor((480 - size) / 2);
    this.painter.fillStyle = "#ffffff";
    this.painter.fillRect(0, 0, 640, 480);
    this.painter.fillStyle = "#000000";
    for (var r = 0; r < modules; r++) {
      for (var c = 0; c < modules; c++) {
        if (matrix[r][c]) this.painter.fillRect(left + c * scale, top + r * scale, scale, scale);
      }
    }
    this.canvas.hidden = false;
  };

  /**
   * Hold a code up for the device's camera.
   *
   * The camera is one thing and can have one owner, so claiming it is a
   * statement rather than a race: while this is set, the page hands the device
   * this canvas instead of a webcam, and clearing it hands the webcam back. A
   * single frame flickers by two pixels on a timer for the same reason the
   * tutorial's does, so the capture stream always has something new to publish
   * and the device never sits looking at a frame it has already given up on.
   */
  /**
   * Hold the first receive address up, because the device has just asked for it.
   *
   * Exporting an xpub leaves a SeedSigner on Verify Address, telling you to show
   * it a receive address from the wallet you just exported. That is not a
   * formality: it is the check that the key this panel imported is the key the
   * device holds, and skipping it teaches the habit of trusting whatever a
   * coordinator claims your addresses are. So the panel puts the answer up
   * ready, and says as little as it can while doing it.
   *
   * Ready, not beamed. An earlier turn of this held the code at the camera the
   * moment it connected, which took the camera nobody had offered it and pushed
   * the device into its address check unasked. Showing it is one press.
   */
  Wallet.prototype.offerVerify = function () {
    var self = this;
    // Branch 0, index 0: the first receive address, which is the one a device
    // checking an import expects to be shown.
    var first = null;
    (this.records || []).forEach(function (record) {
      if (!first && record.branch === 0 && record.index === 0) first = record;
    });
    if (!first) return;
    this.verify = first;
    this.render();

  };

  Wallet.prototype.doneVerifying = function () {
    if (!this.verify) return;
    this.verify = null;
    this.stopPresenting();
    if (this.canvas) this.canvas.hidden = true;
    this.render();
  };

  Wallet.prototype.present = function (frames) {
    var self = this;
    this.stopPresenting();
    var at = 0;
    var beat = 0;
    this.presenting = setInterval(function () {
      if (frames.length > 1 || at === 0) {
        self.paint(scope.QREncode.matrix(frames[at % frames.length]));
        at++;
      }
      beat ^= 1;
      self.painter.fillStyle = beat ? "#fefefe" : "#ffffff";
      self.painter.fillRect(0, 0, 2, 2);
    }, frames.length > 1 ? FRAME_MS : 60);
    // The first code up straight away, rather than after the first interval.
    this.paint(scope.QREncode.matrix(frames[0]));
    at = 1;
  };

  Wallet.prototype.stopPresenting = function () {
    if (this.presenting) clearInterval(this.presenting);
    this.presenting = null;
  };

  /** The stream the page hands the device when the wallet is holding a code up. */
  Wallet.prototype.stream = function () {
    if (!this.presenting || !this.canvas.captureStream) return null;
    return this.canvas.captureStream(25);
  };

  // ------------------------------------------------------- the device's own log

  // The same narration the tests and the tutorial read. It is what lets this
  // panel say "the device is reviewing it" truthfully rather than guessing from
  // a timer: the screens named below are screens the device really put up.
  Wallet.prototype.log = function (message) {
    this.lines.push(message);
    if (this.lines.length > 400) this.lines.splice(0, 200);
  };

  Wallet.prototype.currentScreen = function () {
    for (var i = this.lines.length - 1; i >= 0; i--) {
      var found = /display\(\) enter: (\w+)/.exec(this.lines[i]);
      if (found) return found[1];
    }
    return null;
  };

  // ------------------------------------------------------------- connecting

  // What the device puts in a static single sig xpub QR: the account key and
  // where it came from, which is everything a watching wallet needs and nothing
  // that can spend.
  var ACCOUNT_LINE = /^\[[0-9a-fA-F]{8}(?:\/\d+['h]?)+\][A-Za-z0-9]+$/;

  // The animated forms of the same thing. A crypto-account is what Export Xpub
  // animates by default, one output descriptor around the account key; a
  // crypto-hdkey is that key on its own. parseAccount takes the CBOR of either,
  // so all this has to decide is whether the UR on screen is one of the two.
  var ACCOUNT_URS = ["crypto-account", "crypto-hdkey"];

  /**
   * Sit and watch the device's screen for an account key, however it is written.
   *
   * No button to press: the panel is already looking, so the moment the export
   * lands on the device's screen the wallet appears. The static form is one QR
   * and one glance; the animated form is several, and they are counted off as
   * they are read because a transfer that takes seven frames looks like a
   * transfer that has stalled unless it says otherwise. That count is the
   * fountain decoder's own, never a timer dressed up as one: it is codes this
   * loop actually decoded out of the number the first part declared.
   */
  Wallet.prototype.watchForAccount = function () {
    var self = this;
    var collector = null;
    this.error = "";
    this.watch(function () {
      var text = self.readDevice();
      if (!text) return false;
      if (ACCOUNT_LINE.test(text.trim())) return text.trim();
      var head = /^ur:([a-z0-9-]+)\//i.exec(text);
      if (!head || ACCOUNT_URS.indexOf(head[1].toLowerCase()) === -1) return false;
      collector = collector || scope.URDecode.collector();
      if (!feed(collector, text)) return false;
      if (collector.parts()) {
        self.say("Code " + collector.have() + " of " + collector.parts() + ".");
      }
      if (!collector.done()) return false;
      return collector.payload();
    }, 86400000, "the account key on the device's screen")
      .then(function (exported) { return self.connect(exported); })
      .catch(function () { /* the drawer was shut, or the day ran out */ });
  };

  /** Connect to whatever the export turned out to be: the line, or the CBOR. */
  Wallet.prototype.connect = function (exported) {
    var self = this;
    var absent = NEEDS.filter(function (name) {
      return !C || typeof C[name] !== "function";
    });
    if (absent.length) {
      this.stage = "idle";
      this.error = "This page's coordinator cannot build a single signature "
                 + "wallet yet: it is missing " + absent.join(", ") + ". The "
                 + "simulator is unaffected.";
      this.render();
      return Promise.resolve();
    }

    this.stage = "connecting";
    this.error = "";
    this.say("Reading the account key off the device's screen.");
    track("connect", "started");

    return Promise.resolve()
      .then(function () { return C.parseAccount(exported); })
      .then(function (account) {
        self.account = account;
        return C.singleSigWallet(account);
      })
      .then(function (wallet) {
        self.wallet = wallet;
        return self.derive();
      })
      .then(function () { return self.refresh(); })
      .then(function () {
        self.stage = "ready";
        self.say("");
        track("connect", "connected");
        if (scope.Track) scope.Track.milestone("wallet-connected");
        self.render();
        self.refreshSoon();
        self.offerVerify();
      })
      .catch(function (error) {
        self.stage = "idle";
        self.error = error.message + " Nothing about the simulator itself has changed.";
        self.say("");
        self.render();
        self.watchForAccount();
      });
  };

  /**
   * Twenty addresses on each branch, which is what a gap limit means.
   *
   * One at a time and in order, because each is a public derivation with real
   * curve arithmetic behind it and forty of them take a moment worth narrating.
   */
  Wallet.prototype.derive = function () {
    var self = this;
    var wanted = [];
    for (var branch = 0; branch < 2; branch++) {
      for (var index = 0; index < GAP; index++) wanted.push([branch, index]);
    }
    this.records = [];
    return wanted.reduce(function (chain, at) {
      return chain.then(function () {
        return C.deriveAddressSingle(self.wallet, at[0], at[1]);
      }).then(function (record) {
        record.branch = at[0];
        record.index = at[1];
        self.records.push(record);
        self.say("Working out your addresses, " + self.records.length
                 + " of " + wanted.length + ".");
      });
    }, Promise.resolve());
  };

  // ----------------------------------------------------------- what it holds

  Wallet.prototype.refresh = function () {
    var self = this;
    if (!this.records.length) return Promise.resolve();
    if (this.stage === "connecting") {
      this.say("Asking Bitsaga Signet what those addresses hold.");
    }
    return scanChain(this.records.map(function (record) { return record.address; }))
      .then(function (held) {
        self.chain = held;
        // The balance view only. A refresh that landed while somebody was
        // halfway through typing an amount would rebuild the form under them
        // and put the default back, which is a wallet arguing with its user.
        if (self.stage === "ready" && self.view === "balance" && !self.step) self.render();
      });
  };

  /** The idle refresh, which only runs while somebody is looking at the panel. */
  Wallet.prototype.refreshSoon = function () {
    var self = this;
    if (this.refresher) clearTimeout(this.refresher);
    this.refresher = setTimeout(function () {
      if (!self.open || self.stage !== "ready") return;
      self.refresh().catch(function () { /* the next one can try again */ })
        .then(function () { self.refreshSoon(); });
    }, REFRESH_MS);
  };

  Wallet.prototype.held = function (address) {
    return this.chain[address] || { confirmed: 0, unconfirmed: 0, utxos: [], history: [] };
  };

  Wallet.prototype.balance = function () {
    var self = this;
    var confirmed = 0, pending = 0;
    this.records.forEach(function (record) {
      var held = self.held(record.address);
      confirmed += held.confirmed || 0;
      pending += held.unconfirmed || 0;
    });
    return { confirmed: confirmed, pending: pending, total: confirmed + pending };
  };

  /** Every unspent output, with the address record that can sign for it. */
  Wallet.prototype.coins = function () {
    var self = this;
    var out = [];
    this.records.forEach(function (record) {
      (self.held(record.address).utxos || []).forEach(function (utxo) {
        out.push({
          txid: utxo.txid, vout: utxo.vout, value: utxo.value,
          height: utxo.height || 0, record: record,
        });
      });
    });
    return out;
  };

  /** The first address on a branch the chain has never seen. */
  Wallet.prototype.fresh = function (branch) {
    var self = this;
    var onBranch = this.records.filter(function (r) { return r.branch === branch; });
    var unused = onBranch.filter(function (record) {
      return !(self.held(record.address).history || []).length;
    });
    // All twenty used is the end of the gap limit, not a reason to fail: the
    // last one is reused and the panel says nothing, because a demonstration
    // wallet that has been round twenty addresses has made its point already.
    return unused[0] || onBranch[onBranch.length - 1];
  };

  /**
   * The list, rebuilt from what the chain says plus what this tab did.
   *
   * A scan endpoint answers about addresses, not about transactions, so this
   * knows exactly two things about any transaction: whether it is in a block,
   * and what it paid us. A spend's own amount is only known for spends made
   * here, which are the only spends this wallet can have made.
   */
  Wallet.prototype.history = function () {
    var self = this;
    var rows = {};
    this.records.forEach(function (record) {
      var held = self.held(record.address);
      (held.history || []).forEach(function (entry) {
        var row = rows[entry.txid] || (rows[entry.txid] = {
          txid: entry.txid, height: entry.height || 0, received: 0,
        });
        if (entry.height) row.height = entry.height;
      });
      (held.utxos || []).forEach(function (utxo) {
        var row = rows[utxo.txid] || (rows[utxo.txid] = {
          txid: utxo.txid, height: utxo.height || 0, received: 0,
        });
        row.received += utxo.value;
      });
    });
    this.sent.forEach(function (spend) {
      var row = rows[spend.txid] || (rows[spend.txid] = { txid: spend.txid, height: 0, received: 0 });
      row.spend = spend;
    });
    return Object.keys(rows).map(function (txid) { return rows[txid]; })
      .sort(function (a, b) {
        if (!a.height !== !b.height) return a.height ? 1 : -1;   // pending first
        return b.height - a.height;
      });
  };

  // -------------------------------------------------------------- the faucet

  Wallet.prototype.claim = function () {
    var self = this;
    var record = this.fresh(0);
    this.error = "";
    this.say("Asking Bitsaga Signet's faucet to pay " + record.address + ".");
    this.render();
    return C.network.claim(record.address).then(function (paid) {
      track("faucet", "claimed");
      if (scope.Track) scope.Track.milestone("wallet-funded");
      self.say(WAITING);
      self.render();
      // Straight away, so the payment appears as unconfirmed the moment the
      // faucet has made it rather than half a minute later when it is mined.
      return self.refresh().then(function () {
        return self.waitForBlock(paid.txid);
      });
    }).then(function () {
      self.say("");
      return self.refresh();
    }).catch(function (error) {
      self.say("");
      self.error = error.message;
      self.render();
    });
  };

  /**
   * Wait until a transaction is in a block.
   *
   * The proof endpoint answers 404 until then, so asking for the proof is the
   * confirmation check, and it is one transaction rather than forty addresses:
   * polling the scan endpoint this often would be asking the chain the wrong
   * question forty times over.
   */
  Wallet.prototype.waitForBlock = function (txid) {
    var self = this;
    var deadline = Date.now() + 300000;
    return new Promise(function (resolve, reject) {
      (function again() {
        if (!self.open) return resolve();          // nobody is watching any more
        C.network.proof(txid).then(function () {
          resolve();
        }, function (error) {
          if (error.status !== 404) return reject(error);
          if (Date.now() > deadline) {
            return reject(new Error("Bitsaga Signet has not put this in a block. "
                                    + "It may still turn up."));
          }
          setTimeout(again, PROOF_MS);
        });
      })();
    });
  };

  // ---------------------------------------------------------------- spending

  /**
   * The whole round trip, in the order the steps appear on screen.
   *
   * Every one of them is a real thing happening: a PSBT built here, read by the
   * device's own unmodified decoder off a code it really photographed, reviewed
   * on its own screens, signed by the seed it is holding, handed back as a code
   * this panel really read, finished here and given to the network.
   */
  Wallet.prototype.spend = function (amount, destination) {
    var self = this;
    var coins = this.coins();
    this.error = "";
    this.sending = { amount: amount, destination: destination };
    track("send", "composed");

    this.at("build");
    return Promise.resolve().then(function () {
      if (!coins.length) throw new Error("There is nothing in this wallet to spend.");
      // Confirmed first, largest first: the smallest number of inputs that can
      // cover it, and no unconfirmed coin unless there is no other way.
      coins.sort(function (a, b) {
        if (!a.height !== !b.height) return a.height ? -1 : 1;
        return b.value - a.value;
      });
      var chosen = [], total = 0;
      for (var i = 0; i < coins.length && total < amount + 1000; i++) {
        chosen.push(coins[i]);
        total += coins[i].value;
      }
      if (total < amount + 1000) throw new Error("There is not enough in this wallet to send that.");
      // Which output, and everything the derivation knew about the address it
      // pays to: the coordinator signs over the value and rebuilds the script
      // from the key, and it should not have to go looking for either.
      self.sending.inputs = chosen.map(function (coin) {
        return { txid: coin.txid, vout: coin.vout, value: coin.value, source: coin.record };
      });
      self.sending.change = self.fresh(1);
      return C.buildPsbtSingle({
        inputs: self.sending.inputs,
        outputs: [{ value: amount, script: addressScript(destination) }],
        change: self.sending.change,
        feeRate: FEE_RATE,
      });
    }).then(function (psbt) {
      self.sending.psbt = psbt;
      var frames = scope.WalletTutorial && scope.WalletTutorial.specterFrames;
      if (!frames) throw new Error("wallet-tutorial.js is not on this page, so there "
                                   + "is nothing here to split the transaction into codes.");
      self.sending.frames = frames(psbt, 280);
      self.at("show");
      self.present(self.sending.frames);
      track("send", "psbt-shown");
      // The device has to be told to look, and the panel has to have the code
      // up before it is told: the page hands over the camera when the camera is
      // opened, so a device already scanning is a device already pointed at a
      // webcam.
      return self.watch(function () {
        return self.currentScreen() === "ScanScreen";
      }, 300000, "the device to open Scan");
    }).then(function () {
      return self.watch(function () {
        var screen = self.currentScreen();
        return screen && screen !== "ScanScreen";
      }, 300000, "the device to take the transaction");
    }).then(function () {
      self.stopPresenting();
      self.canvas.hidden = true;
      self.at("review");
      return self.readPsbt(600000);
    }).then(function (collector) {
      self.at("finish");
      track("send", "signature");
      var signed = C.toBase64(collector.psbt());
      self.sending.signed = signed;
      return C.finaliseSingle(signed, self.sending.inputs);
    }).then(function (raw) {
      var hex = raw && raw.hex ? raw.hex : raw;
      return C.network.broadcast(hex);
    }).then(function (sent) {
      track("send", "broadcast");
      self.sending.txid = sent.txid;
      self.sent.push({ txid: sent.txid, amount: amount, destination: destination });
      self.at("confirm");
      self.say(WAITING);
      return self.refresh().then(function () { return self.waitForBlock(sent.txid); });
    }).then(function () {
      track("send", "confirmed");
      if (scope.Track) scope.Track.milestone("wallet-spent");
      self.at("done");
      self.say("");
      return self.refresh();
    }).catch(function (error) {
      self.stopPresenting();
      self.canvas.hidden = true;
      self.error = error.message;
      self.say("");
      self.render();
    });
  };

  // ------------------------------------------------------------- the rendering

  // The steps of a send, which are what a visitor is meant to come away with,
  // so they are on screen the whole time rather than replaced one by one.
  var STEPS = [
    ["build", "Build the transaction here, in this tab"],
    ["show", "Show it to your signer as a QR code"],
    ["review", "The device reads it and shows what it would sign"],
    ["finish", "Read the signature back off the device's screen"],
    ["confirm", "Finish it here, and hand it to Bitsaga Signet"],
    ["done", "In a block"],
  ];

  Wallet.prototype.at = function (step) {
    this.step = step;
    this.render();
  };

  /** A line about what is happening now, which is never a guess. */
  Wallet.prototype.say = function (text) {
    this.progress = text;
    if (this.sayText) this.sayText.textContent = text;
    this.openerState.textContent = this.openerLine();
  };

  Wallet.prototype.openerLine = function () {
    if (this.stage === "connecting") return "Connecting";
    if (this.stage !== "ready") return "Not connected";
    var balance = this.balance();
    return balance.total ? sats(balance.total) : "0 sats";
  };

  Wallet.prototype.render = function () {
    if (!this.open) return;
    var self = this;
    this.body.textContent = "";
    this.openerStatus.dataset.on = this.stage === "ready" ? "yes" : "no";
    this.openerState.textContent = this.openerLine();

    if (this.stage === "idle") this.renderIdle();
    else if (this.stage === "connecting") this.renderConnecting();
    else if (this.view === "receive") this.renderReceive();
    else if (this.view === "send") this.renderSend();
    else this.renderBalance();

    if (this.error) {
      var bad = element("p", "wal-bad", this.error);
      this.body.appendChild(bad);
      // A send that went wrong offers its own way back, and one way back is
      // enough: two buttons under one sentence is a choice nobody asked for.
      if (this.step) return;
      var retry = element("button", null, "Try again");
      retry.type = "button";
      retry.addEventListener("click", function () {
        self.error = "";
        self.render();
        if (self.stage === "idle") self.watchForAccount();
      });
      var row = element("div", "wal-actions");
      row.appendChild(retry);
      this.body.appendChild(row);
    }
  };

  // The one line that changes without the panel being rebuilt around it, so it
  // is the one line that has to announce itself.
  Wallet.prototype.sayInto = function (parent) {
    this.sayText = element("p", "wal-say", this.progress);
    this.sayText.setAttribute("aria-live", "polite");
    parent.appendChild(this.sayText);
  };

  // The landing state used to open with two paragraphs about what a signing
  // device is and what this panel is the other half of, before it got round to
  // the one thing a visitor has to do. Anybody who has opened this has already
  // decided to try it; what they need is the path and the reassurance that
  // there is nothing to press afterwards. The rest of the argument is on the
  // page around it and behind the i, where somebody who wants it can find it.
  // Numbered, because it is a sequence and was written as prose; and starting
  // with the seed, because a device that has just booted has none and every
  // visit begins there. The last step is the one that used to be a sentence on
  // its own -- "leave the QR up, this panel reads it" reads as a riddle with
  // nothing before it, and as an instruction once it is step three of three.
  Wallet.prototype.renderIdle = function () {
    var steps = element("ol", "wal-howto");
    steps.appendChild(step("Make a seed on the device.", SEED_PATH));
    steps.appendChild(step("Export its public key.", EXPORT_PATH));
    steps.appendChild(step("Leave that QR on the device's screen. This panel "
                           + "reads it off the screen by itself.", null));
    this.body.appendChild(steps);
    this.sayInto(this.body);
  };

  function step(text, path) {
    var item = element("li");
    item.appendChild(document.createTextNode(text));
    if (path) item.appendChild(element("span", "wal-path", path));
    return item;
  }

  Wallet.prototype.renderConnecting = function () {
    this.body.appendChild(element("p", "wal-say", "Connecting to the device."));
    this.sayInto(this.body);
  };

  Wallet.prototype.button = function (label, primary, handler) {
    var button = element("button", primary ? "primary" : null, label);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
  };

  Wallet.prototype.renderBalance = function () {
    var self = this;
    var balance = this.balance();

    // The device is on Verify Address and wants to be shown one. Four words and
    // the address itself: anybody who has just been told what to do by the
    // device does not need to be told again in a paragraph.
    if (this.verify) {
      var check = element("div", "wal-verify");
      check.appendChild(element("p", "wal-verify-head", "Your first address"));
      check.appendChild(element("p", "wal-mono", this.verify.address));
      check.appendChild(element("p", "wal-verify-say", "Scan it on the device to check it matches."));
      var row = element("div", "wal-actions");
      row.appendChild(this.button("Show it to the device", true, function () {
        self.present([self.verify.address]);
        self.say("On the device, go to Scan.");
        self.render();
      }));
      row.appendChild(this.button("Done", true, function () { self.doneVerifying(); }));
      check.appendChild(row);
      this.body.appendChild(check);
    }

    var big = element("p", "wal-balance", sats(balance.total));
    this.body.appendChild(big);
    if (balance.pending) {
      this.body.appendChild(element("p", "wal-pending",
        sats(balance.pending) + " of it is not in a block yet."));
    }
    this.sayInto(this.body);

    var rows = this.history();
    if (!rows.length) {
      // What the faucet pays, in the number it actually pays, when it has said
      // so. The page keeps no copy of that figure to fall back on: a wallet
      // promising an amount nobody agreed to is worse than a wallet saying
      // "some".
      var pays = this.status && this.status.payout_sat
        ? "The faucet pays " + sats(this.status.payout_sat) + " at a time, on "
          + "Bitsaga Signet, which is the only place they exist. "
        : "The faucet pays test coins on Bitsaga Signet, which is the only "
          + "place they exist. ";
      if (this.status && this.status.faucet_ready === false) {
        this.body.appendChild(element("p", "wal-pending",
          "The faucet is empty at the moment. Rob has been told; try again shortly."));
      }
      // Behind the i beside the button rather than above it: what the faucet
      // pays and what the coins are worth is worth having, and it is not worth
      // reading every time somebody wants more of them.
      var actions = element("div", "wal-actions");
      actions.appendChild(this.button("Get test bitcoin", true, function () { self.claim(); }));
      actions.appendChild(this.info(pays + NOT_REAL));
      this.body.appendChild(actions);
      return;
    }

    var list = element("ul", "wal-list");
    rows.forEach(function (row) {
      var item = document.createElement("li");
      item.dataset.state = row.height ? "confirmed" : "pending";
      var what = element("div");
      what.appendChild(element("span", "what", row.spend ? "Sent" : "Received"));
      what.appendChild(element("span", "when", row.height
        ? "In block " + row.height
        : WAITING));
      item.appendChild(what);
      item.appendChild(element("span", "amount", row.spend
        ? "-" + sats(row.spend.amount)
        : "+" + sats(row.received)));
      list.appendChild(item);
    });
    this.body.appendChild(list);

    var row = element("div", "wal-actions");
    row.appendChild(this.button("Receive", false, function () {
      self.view = "receive";
      self.render();
    }));
    row.appendChild(this.button("Send", true, function () {
      self.view = "send";
      self.step = null;
      self.sending = null;
      self.to = "";
      self.render();
    }));
    row.appendChild(this.button("Get test bitcoin", false, function () { self.claim(); }));
    this.body.appendChild(row);
  };

  Wallet.prototype.renderReceive = function () {
    var self = this;
    var record = this.fresh(0);
    this.body.appendChild(element("p", "wal-say",
      "Your next unused address. It belongs to the seed in the device, and the "
      + "device can prove that to you: it is address " + record.index
      + " of the account you exported."));
    this.body.appendChild(element("p", "wal-mono", record.address));
    this.body.appendChild(this.canvas);
    this.canvas.hidden = false;
    this.paint(scope.QREncode.matrix(record.address));
    var row = element("div", "wal-actions");
    row.appendChild(this.button("Back", false, function () {
      self.canvas.hidden = true;
      self.view = "balance";
      self.render();
    }));
    row.appendChild(this.button("Get test bitcoin", true, function () {
      self.canvas.hidden = true;
      self.view = "balance";
      self.claim();
    }));
    this.body.appendChild(row);
  };

  Wallet.prototype.renderSend = function () {
    var self = this;
    if (this.step) return this.renderSending();

    var balance = this.balance();
    var form = element("div");
    var amountField = element("div", "wal-field");
    var amountLabel = element("label", null, "Amount, in sats");
    amountLabel.htmlFor = "wal-amount";
    var amount = element("input");
    amount.id = "wal-amount";
    amount.type = "number";
    amount.min = String(DUST);
    amount.value = String(Math.max(DUST, Math.min(100000, Math.floor(balance.total / 2))));
    amountField.appendChild(amountLabel);
    amountField.appendChild(amount);

    var toField = element("div", "wal-field");
    var toLabel = element("label", null, "To");
    toLabel.htmlFor = "wal-to";
    var to = element("input");
    to.id = "wal-to";
    to.type = "text";
    to.spellcheck = false;
    // Empty, and filled only by somebody deciding to fill it. An address that
    // is already in the box is an address nobody reads, and a wallet that has
    // quietly chosen who to pay has made the one decision this screen exists to
    // put in front of you.
    to.value = this.to || "";
    to.addEventListener("input", function () { self.to = to.value; });
    var fill = this.button("Use one of my addresses", false, function () {
      self.to = self.payTo();
      to.value = self.to;
    });
    toField.appendChild(toLabel);
    toField.appendChild(to);
    toField.appendChild(fill);

    form.appendChild(element("p", "wal-say",
      "There is nobody else on this test network to pay, so paying yourself is "
      + "the honest demonstration, and it is a real transaction either way: "
      + "signed, relayed and mined like any other. The button under the field "
      + "fills it with one of your own receive addresses. " + NOT_REAL));
    form.appendChild(amountField);
    form.appendChild(toField);
    this.body.appendChild(form);
    this.sayInto(this.body);

    var row = element("div", "wal-actions");
    row.appendChild(this.button("Back", false, function () {
      self.view = "balance";
      self.render();
    }));
    // "Create transaction" rather than "Build it": what the button does is the
    // whole point of the screen, and a wallet nobody has used before should not
    // ask them to guess what "it" refers to.
    row.appendChild(this.button("Create transaction", true, function () {
      var value = Math.floor(Number(amount.value));
      if (!(value >= DUST)) {
        self.error = "That is less than the network will relay, which is " + DUST + " sats.";
        return self.render();
      }
      // The field starts empty, so nothing in it is the ordinary way to arrive
      // here rather than a mistake, and it deserves the sentence that says what
      // to do rather than the one about what an address begins with.
      if (!to.value.trim()) {
        self.error = "There is nowhere to send it yet. Type an address, or use "
                   + "one of your own.";
        return self.render();
      }
      // Read here rather than three steps later: an address that will not
      // decode is a typing mistake, and a typing mistake belongs beside the
      // field it was made in.
      try {
        addressScript(to.value);
      } catch (error) {
        self.error = error.message;
        return self.render();
      }
      self.spend(value, to.value.trim());
    }));
    this.body.appendChild(row);
  };

  /**
   * The address the button under the To field puts in it.
   *
   * One of this wallet's own and nothing else, because that is what the button
   * says it is. The faucet's would be the neat alternative, the API does not
   * publish one, and a button labelled "my addresses" that quietly filled in
   * somebody else's would be worse than no button.
   */
  Wallet.prototype.payTo = function () {
    // Branch 0, not branch 1. Branch 1 is where the change goes, so paying it
    // put the payment and the change on the same script: the transaction was
    // real and correct, but the device had two identical outputs to show and
    // the one thing marking which was change was a derivation record nobody
    // could see. Paying a receive address keeps the two visibly different,
    // which is the whole thing this screen is meant to teach.
    return this.fresh(0).address;
  };

  Wallet.prototype.renderSending = function () {
    var self = this;
    var reached = false;
    var list = element("ul", "wal-steps");
    STEPS.forEach(function (step) {
      var item = element("li", null, step[1]);
      if (step[0] === self.step) { item.dataset.state = "now"; reached = true; }
      else item.dataset.state = reached ? "next" : "done";
      list.appendChild(item);
    });

    if (this.step === "show") {
      this.body.appendChild(element("p", "wal-say", "Show this to your signer."));
      this.body.appendChild(element("p", "wal-path", "On the device, go to Scan"));
      this.body.appendChild(this.canvas);
      this.body.appendChild(element("p", "wal-note",
        (this.sending.frames.length === 1
          ? "One code. "
          : this.sending.frames.length + " codes, cycling. ")
        + "The device's camera is pointed at this canvas, so nothing has to be "
        + "held anywhere and no webcam is opened."));
    } else if (this.step === "review") {
      this.body.appendChild(element("p", "wal-say",
        "The device has it. Work through its own review screens and approve it "
        + "there: what it is showing you is the transaction this panel built."));
    } else if (this.step === "finish") {
      this.body.appendChild(element("p", "wal-say",
        "Reading the signature off the device's screen."));
    } else if (this.step === "done") {
      this.body.appendChild(element("p", "wal-say",
        "Sent, mined and confirmed on Bitsaga Signet. " + NOT_REAL));
    }

    this.body.appendChild(list);
    this.sayInto(this.body);

    if (this.sending && this.sending.txid) {
      this.body.appendChild(element("p", "wal-mono", this.sending.txid));
    }
    if (this.step === "done" || this.error) {
      var row = element("div", "wal-actions");
      row.appendChild(this.button("Back to the wallet", true, function () {
        self.view = "balance";
        self.step = null;
        self.error = "";
        self.canvas.hidden = true;
        self.render();
      }));
      this.body.appendChild(row);
    }
  };

  // ------------------------------------------------------- what the page uses

  scope.WalletCoordinator = {
    mount: function (options) {
      var wallet = new Wallet(options);
      scope.WalletCoordinator.current = wallet;
      // The decoder the wallet's own camera path uses, fetched now rather than
      // when the drawer opens, so the first thing a visitor does is not waiting
      // on a script.
      if (!scope.jsQR) {
        var tag = document.createElement("script");
        tag.src = "jsQR.js";
        document.head.appendChild(tag);
      }
      return wallet;
    },

    /**
     * The camera, when the wallet has claimed it.
     *
     * Null means it has not, and the page opens the webcam as it always did.
     * One owner at a time, decided at the moment the device opens its camera
     * rather than by whoever wrote to a variable last.
     */
    cameraStream: function () {
      var wallet = scope.WalletCoordinator.current;
      return wallet ? wallet.stream() : null;
    },
  };
})(typeof self !== "undefined" ? self : this);
