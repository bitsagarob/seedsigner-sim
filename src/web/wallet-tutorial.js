// The multisig tutorial: a whole 2 of 3, inside the simulator.
//
// Three seeds onto three SeedKeeper cards, the three public keys back off them,
// a 2 of 3 wallet built from those keys, coins from Bitsaga Signet's faucet, and
// a spend signed by two of the three cards and confirmed on that chain. Nobody
// leaves the page and nobody installs anything.
//
// Two modes, one machine. A step is a list of actions, and an action is three
// things: a sentence saying what has to happen, the keys or clicks that make it
// happen, and how we know it did. Self driving performs the middle one; hands on
// leaves it to the visitor. Both wait for the same evidence, which is why the
// panel keeps pace either way and why there is only one description of the flow.
//
// The coordinator is on the phone: signet-coordinator.js. This file is the
// theatre around it, and the driving of the device.
//
// The QR exchange is not faked past the optics. A code the phone holds up is
// drawn from real modules by qr-encode.js onto the phone's own screen, and the
// device's camera is pointed at that screen, so the wallet's unmodified decoder
// reads real pixels. A code the device shows is read back off the device's
// canvas the same way. Nobody's webcam is involved, and the panel says so.

(function (scope) {
  "use strict";

  var C = scope.SignetCoordinator;

  // Three published BIP39 test vectors, one per card. Three separate seeds
  // rather than three paths under one, because a quorum whose keys all came
  // from the same seed is not a quorum. Nothing about them is secret and
  // nothing should ever hold value.
  var SEEDS = [
    {
      card: "A",
      words: "abandon abandon abandon abandon abandon abandon abandon abandon "
           + "abandon abandon abandon about",
      seedqr: "000000000000000000000000000000000000000000000003",
      fingerprint: "73c5da0a",
    },
    {
      card: "B",
      words: "legal winner thank year wave sausage worth useful legal winner "
           + "thank yellow",
      seedqr: "101920151790203919831533203119191019201517902040",
      fingerprint: "b8688df1",
    },
    {
      card: "C",
      words: "letter advice cage absurd amount doctor acoustic avoid letter "
           + "advice cage above",
      seedqr: "102800320257000800640514001601281028003202570004",
      fingerprint: "28645006",
    },
  ];

  // 1,000 sat over roughly 150 virtual bytes. Bitsaga Signet is not busy and
  // nothing here is bidding for space; this is simply above the relay minimum.
  var FEE = 1000n;

  var NOT_REAL = "These are not real bitcoin. They exist only on our test "
               + "network, cannot be sold or sent to anyone, and are worth nothing.";

  // How fast self driving goes.
  //
  // The wallet answers a keypress in about a fifth of a second, so left to
  // itself this whole ceremony went past in two minutes: thirteen steps, a
  // hundred and fifty odd actions, and nothing on screen long enough to read.
  // Waiting on the wallet is not pacing, so the pacing is here: before an
  // action is performed, the run waits for roughly as long as the sentence
  // describing it takes to take in, and a step's own paragraph gets longer
  // because it is the part actually worth reading.
  //
  // These are a starting point rather than a finding. The controls in the panel
  // are the real answer for anyone this does not suit: Pause stops it between
  // actions and Step takes exactly one.
  var READ_FLOOR = 500;      // a beat, even for a three word instruction
  var READ_PER_WORD = 100;   // about 600 words a minute: a glance, not a study
  var READ_MAX = 3000;       // no single action holds the run longer than this
  var STEP_MAX = 8000;       // except the paragraph that opens a step
  // Between the keypresses of one action. Fast enough not to be a wait, slow
  // enough that a menu is seen moving one line at a time rather than jumping.
  var PRESS_GAP = 340;

  // ---------------------------------------------------------------- the panel

  var CSS = [
    ".tut{width:min(46rem,100%);box-sizing:border-box;position:relative;",
    "background:#12151a;border:1px solid #2a2f36;border-radius:10px;",
    "padding:1.1rem 1.25rem 1.25rem;color:#b6bec8;text-align:left;overflow:hidden}",
    ".tut h2{font-size:1rem;font-weight:600;color:#d7dbe0;margin:0}",

    // The one moving thing: a hairline that grows across the top of the panel
    // as the step's own actions complete. Self driving only, because in hands
    // on there is nothing to be ahead of.
    ".tut-bar{position:absolute;inset:0 auto auto 0;height:2px;width:100%;background:transparent}",
    ".tut-bar i{display:block;height:100%;width:0;background:#f7931a;",
    "transition:width .45s ease}",

    ".tut-head{display:flex;flex-wrap:wrap;align-items:baseline;",
    "justify-content:space-between;gap:.5rem .9rem}",
    ".tut-controls{display:flex;flex-wrap:wrap;gap:.4rem}",
    ".tut button{font:inherit;font-size:.88rem;color:#8b939e;background:#1d2026;",
    "border:1px solid #2a2e35;border-radius:5px;padding:.25rem .7rem;cursor:pointer}",
    ".tut button:hover:not(:disabled){color:#d7dbe0;border-color:#3a3f47}",
    ".tut button:disabled{opacity:.4;cursor:default}",
    ".tut button.on{color:#f7931a;border-color:#f7931a;background:#16181c}",
    ".tut button:focus-visible{outline:2px solid #f7931a;outline-offset:2px}",

    ".tut-step{margin:.9rem 0 0;color:#d7dbe0;font-weight:600}",
    ".tut-say{margin:.35rem 0 0}",
    ".tut-do{margin:.6rem 0 0;padding:.5rem .7rem;border-left:2px solid #f7931a;",
    "background:#16181c;color:#d7dbe0}",
    ".tut-do:empty{display:none}",
    ".tut-verdict{margin:.8rem 0 0;border:1px solid #2a2f36;border-radius:6px;",
    "padding:.5rem .7rem;overflow-wrap:anywhere}",
    ".tut-verdict:empty{display:none}",
    ".tut-verdict[data-state=good]{color:#f7931a;border-color:#f7931a;background:#16181c}",
    ".tut-verdict[data-state=bad]{color:#ef4444;border-color:#7f1d1d;background:#1b0f10}",

    // The phone. A coordinator is a separate thing in a separate hand, so it
    // looks like one, and the QR that crosses between them is drawn at the size
    // the camera actually reads.
    ".tut-swap{display:flex;flex-wrap:wrap;gap:1rem;margin:1rem 0 0;align-items:flex-start}",
    ".tut-phone{width:9.5rem;flex:none;background:#0b0c0e;border:1px solid #2f343c;",
    "border-radius:14px;padding:.55rem .45rem;box-shadow:0 6px 16px rgba(0,0,0,.5)}",
    ".tut-phone-top{height:.28rem;width:2.6rem;margin:0 auto .45rem;border-radius:999px;",
    "background:#2f343c}",
    ".tut-phone-screen{position:relative;background:#16181c;border-radius:7px;",
    "overflow:hidden;min-height:8.8rem}",
    ".tut-phone-canvas{display:block;width:100%;background:#fff}",
    ".tut-phone-canvas[hidden]{display:none}",
    ".tut-phone-face{padding:.5rem .55rem;font-size:.74rem;line-height:1.45;color:#8b939e}",
    ".tut-phone-face b{display:block;color:#d7dbe0;font-weight:600;font-size:.8rem}",
    ".tut-phone-face span{display:block;overflow-wrap:anywhere;margin-top:.3rem}",
    ".tut-phone-name{margin:.4rem 0 0;text-align:center;font-size:.7rem;color:#7c848f;",
    "letter-spacing:.06em;text-transform:uppercase}",

    ".tut-flow{flex:1 1 12rem;min-width:0}",
    ".tut-arrow{color:#f7931a;font-weight:600;font-size:.82rem;letter-spacing:.04em}",
    ".tut-arrow:empty{display:none}",
    ".tut-caption{margin:.25rem 0 0;font-size:.88rem}",
    ".tut-caption:empty{display:none}",
    ".tut-honest{margin:.8rem 0 0;font-size:.82rem;color:#7c848f}",

    ".tut-fold{margin:1rem 0 0}",
    ".tut-fold>summary{display:inline-block;list-style:none;cursor:pointer;font:inherit;",
    "font-size:.88rem;color:#8b939e;background:#1d2026;border:1px solid #2a2e35;",
    "border-radius:5px;padding:.2rem .6rem}",
    ".tut-fold>summary::-webkit-details-marker{display:none}",
    ".tut-fold>summary:hover{color:#d7dbe0;border-color:#3a3f47}",
    ".tut-fold[open]>summary{color:#f7931a;border-color:#f7931a;background:#16181c}",
    ".tut-fold>summary:focus-visible{outline:2px solid #f7931a;outline-offset:2px}",
    ".tut-fold dl{display:grid;grid-template-columns:max-content 1fr;gap:.3rem .9rem;",
    "margin:.7rem 0 0;font-size:.85rem}",
    ".tut-fold dt{color:#7c848f}",
    ".tut-fold dd{margin:0;overflow-wrap:anywhere;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}",
    ".tut-fold dd.plain{font-family:inherit}",

    ".tut-start{font:inherit;color:#f7931a;background:#16181c;border:1px solid #f7931a;",
    "border-radius:6px;padding:.5rem 1.1rem;cursor:pointer}",
    ".tut-start:hover{background:#1c2026}",
    ".tut-start:focus-visible{outline:2px solid #f7931a;outline-offset:2px}",

    "@media (max-width:30rem){",
    ".tut{padding:.9rem .8rem 1rem}",
    ".tut-fold dl{grid-template-columns:1fr;gap:0}",
    ".tut-fold dd{margin:0 0 .45rem}",
    ".tut-phone{width:100%}",
    "}",
  ].join("");

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------- the machine

  function Tutorial(options) {
    this.sendKey = options.sendKey;
    this.keymap = options.keymap;
    this.tray = options.tray;
    this.screen = options.screen;
    this.lines = [];
    this.cursor = 0;
    this.mode = "idle";
    this.paused = true;
    this.stepOnce = false;
    this.generation = 0;
    this.details = [];
    this.build(options.container);
  }

  Tutorial.prototype.build = function (container) {
    var style = element("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var root = element("section", "tut");
    root.id = "tutorial";

    this.bar = element("div", "tut-bar");
    this.barFill = element("i");
    this.bar.appendChild(this.barFill);

    var head = element("div", "tut-head");
    head.appendChild(element("h2", null, "A 2 of 3 on Bitsaga Signet"));
    this.controls = element("div", "tut-controls");
    head.appendChild(this.controls);

    this.playButton = this.control("Play", this.togglePlay.bind(this));
    this.stepButton = this.control("Step", this.stepOn.bind(this));
    this.handsButton = this.control("I will drive", this.toggleHands.bind(this));
    this.againButton = this.control("Start again", this.restart.bind(this));

    this.stepText = element("p", "tut-step");
    this.sayText = element("p", "tut-say");
    this.doText = element("p", "tut-do");
    this.verdict = element("p", "tut-verdict");

    var swap = element("div", "tut-swap");
    var phone = element("div", "tut-phone");
    phone.appendChild(element("div", "tut-phone-top"));
    var phoneScreen = element("div", "tut-phone-screen");
    this.canvas = element("canvas", "tut-phone-canvas");
    this.canvas.width = 640;
    this.canvas.height = 480;
    this.canvas.hidden = true;
    this.face = element("div", "tut-phone-face");
    phoneScreen.appendChild(this.canvas);
    phoneScreen.appendChild(this.face);
    phone.appendChild(phoneScreen);
    phone.appendChild(element("p", "tut-phone-name", "the coordinator"));

    var flow = element("div", "tut-flow");
    this.arrow = element("p", "tut-arrow");
    this.caption = element("p", "tut-caption");
    flow.appendChild(this.arrow);
    flow.appendChild(this.caption);
    flow.appendChild(element("p", "tut-honest",
      "The scanning is done for you. Nothing here uses your webcam: the "
      + "device's camera is pointed at the phone's screen and the phone's at "
      + "the device's, and both screens are on this page."));
    swap.appendChild(phone);
    swap.appendChild(flow);

    this.fold = element("details", "tut-fold");
    this.fold.appendChild(element("summary", null, "Show the details"));
    this.detailList = element("dl");
    this.fold.appendChild(this.detailList);

    root.appendChild(this.bar);
    root.appendChild(head);
    root.appendChild(this.stepText);
    root.appendChild(this.sayText);
    root.appendChild(this.doText);
    root.appendChild(this.verdict);
    root.appendChild(swap);
    root.appendChild(this.fold);
    container.appendChild(root);

    // What is behind every number below, said once, where somebody looking for
    // it will find it and nobody else has to read it.
    this.detail("network", "Bitsaga Signet, our own Bitcoin test network. "
      + "Testnet address prefixes, a block every thirty seconds, and a faucet. "
      + NOT_REAL, true);
    this.detail("mainnet", "Mainnet works here exactly as on hardware, and that "
      + "is the danger: on Mainnet this page exports the correct mainnet account "
      + "keys and produces real, valid mainnet signatures, so any seed you type "
      + "into it should be treated as public. Nothing in this tutorial needs it.",
      true);
    SEEDS.forEach(function (seed) {
      this.detail("Card " + seed.card + " test seed", seed.words, true);
      // The bytes actually held up to the camera: a SeedQR is the twelve words
      // as their four digit positions in the BIP39 wordlist, nothing more.
      this.detail("Card " + seed.card + " SeedQR payload", seed.seedqr);
    }, this);

    this.painter = this.canvas.getContext("2d", { willReadFrequently: true });
    this.clearPhone();
    this.showFace("Bitsaga Signet coordinator",
                  "Nothing built yet. Press Play, or take the buttons yourself.");
    this.introduce();
    // A frame the capture stream can always find something new in, so the
    // device's camera never sits on a stale picture.
    var self = this;
    setInterval(function () {
      self.heartbeat = (self.heartbeat || 0) ^ 1;
      self.painter.fillStyle = self.heartbeat ? "#fefefe" : "#ffffff";
      self.painter.fillRect(0, 0, 2, 2);
    }, 60);
  };

  Tutorial.prototype.control = function (label, handler) {
    var button = element("button", null, label);
    button.type = "button";
    button.addEventListener("click", function () {
      handler();
      button.blur();
    });
    this.controls.appendChild(button);
    return button;
  };

  Tutorial.prototype.introduce = function () {
    this.stepText.textContent = "Ready";
    this.sayText.textContent =
      "Three test seeds go onto three cards, the three public keys come back "
      + "off them, and those keys make one wallet that needs any two of the "
      + "three to spend. Then coins from the Bitsaga Signet faucet, and a spend "
      + "signed twice. " + NOT_REAL;
    this.doText.textContent =
      "Holding all three keys on one device is fine for a demo and wrong for "
      + "real funds. The point of multisig is keys in different places and "
      + "different hands, so that losing one, or someone else finding one, is "
      + "survivable.";
  };

  // ------------------------------------------------------------ the log oracle

  Tutorial.prototype.log = function (message) {
    this.lines.push(message);
  };

  Tutorial.prototype.currentScreen = function () {
    for (var i = this.lines.length - 1; i >= 0; i--) {
      var found = /display\(\) enter: (\w+)/.exec(this.lines[i]);
      if (found) return found[1];
    }
    return null;
  };

  /** Wait for a line matching, from the cursor onwards, and move the cursor. */
  Tutorial.prototype.until = function (pattern, timeout) {
    var self = this;
    var matcher = new RegExp(pattern);
    return this.poll(timeout, function () {
      for (var i = self.cursor; i < self.lines.length; i++) {
        if (matcher.test(self.lines[i])) {
          self.cursor = i + 1;
          return true;
        }
      }
      return false;
    }, pattern);
  };

  /** Poll a predicate until it is true, or give up and say what we wanted. */
  Tutorial.prototype.poll = function (timeout, test, what) {
    var self = this;
    var generation = this.generation;
    var deadline = Date.now() + (this.mode === "hands" ? Math.max(timeout, 900000) : timeout);
    return new Promise(function (resolve, reject) {
      (function tick() {
        if (generation !== self.generation) return;         // restarted underneath us
        var value;
        try {
          value = test();
        } catch (error) {
          return reject(error);
        }
        if (value) return resolve(value);
        if (Date.now() > deadline) {
          return reject(new Error("nothing happened while waiting for " + what));
        }
        setTimeout(tick, 150);
      })();
    });
  };

  Tutorial.prototype.sleep = function (ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  };

  /**
   * Leave time to read what has just gone up, before anything else moves.
   *
   * Self driving only: in hands on the visitor sets the pace by pressing the
   * buttons, and waiting on top of that would only be in the way.
   */
  Tutorial.prototype.pace = function (text, ceiling) {
    if (this.mode !== "self" || !text) return Promise.resolve();
    var words = String(text).trim().split(/\s+/).length;
    return this.sleep(Math.min(ceiling || READ_MAX, READ_FLOOR + words * READ_PER_WORD));
  };

  // ------------------------------------------------------------ the device

  Tutorial.prototype.press = function (names, gap) {
    var self = this;
    return names.reduce(function (chain, name) {
      return chain.then(function () {
        self.sendKey(self.keymap[name]);
        return self.sleep(gap || PRESS_GAP);
      });
    }, Promise.resolve());
  };

  // ------------------------------------------------------------ the phone

  Tutorial.prototype.clearPhone = function () {
    this.painter.fillStyle = "#ffffff";
    this.painter.fillRect(0, 0, 640, 480);
  };

  Tutorial.prototype.showFace = function (title, text) {
    this.face.textContent = "";
    this.face.appendChild(element("b", null, title));
    this.face.appendChild(element("span", null, text));
    this.face.hidden = false;
    this.canvas.hidden = true;
    this.clearPhone();
  };

  Tutorial.prototype.paintMatrix = function (matrix) {
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
    this.face.hidden = true;
    this.canvas.hidden = false;
  };

  /** What the phone's own camera would see: the device's screen. */
  Tutorial.prototype.mirrorDevice = function () {
    this.painter.fillStyle = "#0b0c0e";
    this.painter.fillRect(0, 0, 640, 480);
    // The device draws a QR into the left 240 by 240 of its 320 by 240 screen.
    this.painter.drawImage(this.screen, 0, 0, 240, 240, 80, 0, 480, 480);
    this.painter.strokeStyle = "#f7931a";
    this.painter.lineWidth = 6;
    this.painter.strokeRect(60, 20, 520, 440);
    this.face.hidden = true;
    this.canvas.hidden = false;
  };

  Tutorial.prototype.transfer = function (direction, caption) {
    this.arrow.textContent = direction === "in"
      ? "Phone  →  device" : "Device  →  phone";
    this.caption.textContent = caption;
  };

  Tutorial.prototype.endTransfer = function () {
    this.arrow.textContent = "";
    this.caption.textContent = "";
    this.canvas.hidden = true;
    this.face.hidden = false;
    this.clearPhone();
  };

  /** Read whatever QR is on the device's screen, with the page's own jsQR. */
  Tutorial.prototype.readDevice = function () {
    if (!scope.jsQR) return null;
    var context = this.screen.getContext("2d");
    var image = context.getImageData(0, 0, 240, 240);
    var found = scope.jsQR(image.data, image.width, image.height);
    return found && found.data ? found.data : null;
  };

  // ------------------------------------------------------- details, on demand

  Tutorial.prototype.detail = function (label, value, plain) {
    this.detailList.appendChild(element("dt", null, label));
    var dd = element("dd", plain ? "plain" : null, value);
    this.detailList.appendChild(dd);
  };

  // Shown while it is driving, paused or not: pausing does not undo the actions
  // that have already happened, so a line that emptied itself when the run
  // stopped would be saying something untrue. It stays where the last piece of
  // evidence left it, which is also what it does during the wait before an
  // action, because nothing has happened yet.
  Tutorial.prototype.setProgress = function (fraction) {
    this.barFill.style.width = this.mode === "self"
      ? Math.round(fraction * 100) + "%" : "0";
  };

  /**
   * How far through the action in hand, where the action itself knows.
   *
   * Only ever called with something real: the codes of an animated QR that have
   * actually been read, or how far the chain is towards its next block. An
   * action with nothing measurable inside it simply does not call this, and the
   * line waits at the last thing that really happened.
   */
  Tutorial.prototype.subProgress = function (fraction) {
    this.setProgress(this.fraction + fraction / (this.stepSize || 1));
  };

  // ------------------------------------------------------------ the controls

  Tutorial.prototype.togglePlay = function () {
    if (this.mode === "idle") return this.start("self");
    this.paused = !this.paused;
    this.stepOnce = false;               // Play means keep going, not one more
    this.reflect();
  };

  /**
   * One action, then stop again.
   *
   * The whole of the next thing the panel describes happens -- the keys are
   * pressed and the evidence for them is waited for -- and then the run pauses
   * itself. Nothing is ever left half pressed, because the pausing is done
   * between actions and only there, which is the same place the Play button
   * takes effect.
   */
  Tutorial.prototype.stepOn = function () {
    this.stepOnce = true;
    if (this.mode === "idle") return this.start("self");
    this.paused = false;
    this.reflect();
  };

  /**
   * Take over, or hand back, at the action in hand.
   *
   * The action starts again in the new mode rather than the run carrying on
   * from where it was. Handing back in the middle of an action the visitor has
   * not finished would otherwise leave nobody to press its buttons, and the
   * panel would sit there waiting for something nobody was going to do. Doing
   * it again is safe: an action's evidence is a line the log has not shown yet,
   * so if it has already happened, the new attempt sees it immediately.
   */
  Tutorial.prototype.toggleHands = function () {
    if (this.mode === "idle") return this.start("hands");
    var step = this.at, action = this.atAction;
    this.mode = this.mode === "hands" ? "self" : "hands";
    this.paused = false;
    this.stepOnce = false;
    this.generation++;               // let the wait in flight go
    this.reflect();
    this.run(step, action);
  };

  Tutorial.prototype.restart = function () {
    location.reload();
  };

  Tutorial.prototype.reflect = function () {
    this.playButton.textContent = this.paused ? "Play" : "Pause";
    this.playButton.classList.toggle("on", this.mode === "self" && !this.paused);
    // Stepping through is what hands on already is, so it is not offered twice.
    this.stepButton.disabled = this.mode === "hands";
    this.handsButton.textContent = this.mode === "hands" ? "Let it drive" : "I will drive";
    this.handsButton.classList.toggle("on", this.mode === "hands");
    this.setProgress(this.fraction || 0);
  };

  Tutorial.prototype.start = function (mode) {
    if (this.mode !== "idle") return;
    this.mode = mode;
    this.paused = false;
    this.reflect();
    this.run(0);
  };

  /** Between actions, and only there, so a pause never lands mid keypress. */
  Tutorial.prototype.gate = function () {
    var self = this;
    if (!this.paused) return Promise.resolve();
    return this.poll(86400000, function () { return !self.paused; }, "the Play button");
  };

  // ------------------------------------------------------------ the runner

  // Resumes at an action rather than at a step, so a retry after the faucet
  // answered does not ask the faucet again.
  Tutorial.prototype.run = function (fromStep, fromAction) {
    var self = this;
    var steps = this.steps || (this.steps = buildSteps(this));
    var context = { tutorial: this, state: this.state || (this.state = {}) };

    function runStep(index, first) {
      if (index >= steps.length) return Promise.resolve();
      var step = steps[index];
      self.at = index;
      self.stepSize = step.actions.length;
      self.stepText.textContent = step.title;
      self.sayText.textContent = step.text;
      if (!first) {
        self.verdict.textContent = "";
        self.verdict.removeAttribute("data-state");
        // The step's paragraph is being read; the last step's last instruction
        // is not the thing to leave standing under it.
        self.doText.textContent = "";
      }
      self.fraction = first / step.actions.length;
      self.setProgress(self.fraction);

      // A step opens with a title and a paragraph saying what is about to
      // happen, which is the part worth reading whole, so it is read before the
      // first action rather than underneath one already running.
      var opening = first
        ? Promise.resolve()
        : self.pace(step.title + ". " + step.text, STEP_MAX);

      return step.actions.slice(first).reduce(function (chain, action, offset) {
        var at = first + offset;
        return chain.then(function () {
          self.atAction = at;
          return self.gate();
        }).then(function () {
          self.doText.textContent = action.instruct || "";
          if (self.mode !== "self") return null;
          // The instruction is up; leave time to read it before the device
          // moves. An action with nothing to say gets no wait, because what
          // it is waiting for is the thing to watch.
          return self.pace(action.instruct).then(function () {
            if (action.perform) return action.perform(context);
          });
        }).then(function () {
          return action.until(context);
        }).then(function () {
          self.fraction = (at + 1) / step.actions.length;
          self.setProgress(self.fraction);
          if (self.stepOnce) {           // one action was all that was asked for
            self.stepOnce = false;
            self.paused = true;
            self.reflect();
          }
        });
      }, opening).then(function () {
        return runStep(index + 1, 0);
      });
    }

    return runStep(fromStep, fromAction || 0).then(function () {
      self.doText.textContent = "";
      self.endTransfer();
      self.setProgress(0);
    }).catch(function (error) {
      self.fail(error);
    });
  };

  Tutorial.prototype.fail = function (error) {
    var self = this;
    this.setProgress(0);
    this.endTransfer();
    this.verdict.dataset.state = "bad";
    this.verdict.textContent = error.message;
    this.doText.textContent = "This step did not get where it was going. Try it "
      + "again, or take the buttons yourself.";
    if (!this.retryButton) {
      this.retryButton = this.control("Try again", function () {
        var step = self.at, action = self.atAction;
        self.verdict.textContent = "";
        self.verdict.removeAttribute("data-state");
        self.retryButton.remove();
        self.retryButton = null;
        self.generation++;
        self.cursor = self.lines.length;
        self.run(step, action);
      });
    }
  };

  // ------------------------------------------------------- building the steps

  function step(title, text, actions) {
    return { title: title, text: text, actions: actions };
  }

  /** An action: what has to happen, what does it, and how we know it did. */
  function act(instruct, perform, until) {
    return { instruct: instruct, perform: perform, until: until };
  }

  function buildSteps(tutorial) {
    var t = tutorial;

    function keys(names, gap) {
      return function () { return t.press(names, gap); };
    }

    function screenIs(name, timeout) {
      return function () { return t.until("display\\(\\) enter: " + name + "\\b", timeout || 120000); };
    }

    function logged(pattern, timeout) {
      return function () { return t.until(pattern, timeout || 180000); };
    }

    function settle(ms) {
      return function () { return t.sleep(ms); };
    }

    function inserted(index) {
      return function () {
        return t.poll(60000, function () { return t.tray.inserted() === index; },
                      index < 0 ? "the card to come out" : "the card to go in");
      };
    }

    // The PIN, typed at speed. Four presses of the first key on the keyboard,
    // then the third side button to save: the shortest PIN the card accepts,
    // and the same one every time it is asked for. The ceremony is the real
    // one, screen for screen; only the typing is quick.
    function pin() {
      return keys(["Enter", "Enter", "Enter", "Enter", "3"], 90);
    }

    /**
     * Climb back to the home screen.
     *
     * Left goes to the back arrow at the top of a list screen and select takes
     * it, which is how many screens deep this is does not have to be known. Up
     * would be shorter and is wrong: on the home screen itself it lands on the
     * power button, and selecting that reboots the device and takes the cards
     * with it.
     */
    function homeAgain() {
      return act(
        "Press left and then select, as many times as it takes, until the "
        + "device is back on its home screen.",
        function () {
          var tries = 0;
          function climb() {
            if (t.currentScreen() === "MainMenuScreen" || tries++ > 8) return Promise.resolve();
            return t.press(["ArrowLeft"]).then(function () {
              if (t.currentScreen() === "MainMenuScreen") return;
              return t.press(["Enter"]).then(function () { return t.sleep(400); }).then(climb);
            });
          }
          return climb();
        },
        function () {
          return t.poll(120000, function () {
            return t.currentScreen() === "MainMenuScreen";
          }, "the home screen");
        });
    }

    /**
     * Keep confirming until a screen arrives, because what is in between
     * depends on settings and on the transaction rather than on this flow.
     *
     * Only ever press on a screen that has been up for two looks in a row. A
     * key sent during a transition is buffered and taken by whatever arrives
     * next, and one stray press past the signing screen dismisses the signed QR
     * before anything can read it.
     */
    function advance(target, tries, instruct) {
      return act(
        instruct || ("Keep pressing the select button until the device reaches "
                     + target + "."),
        function () {
          var attempts = 0;
          var previous = null;
          function again() {
            if (t.currentScreen() === target) return Promise.resolve();
            if (attempts++ > (tries || 6) * 2) return Promise.resolve();
            return t.sleep(900).then(function () {
              var screen = t.currentScreen();
              if (screen === target) return;
              if (screen !== previous) {
                previous = screen;                 // still settling; look again
                return again();
              }
              previous = null;
              return t.press(["Enter"]).then(again);
            });
          }
          return again();
        },
        function () {
          return t.poll(120000, function () { return t.currentScreen() === target; }, target);
        });
    }

    /**
     * Make the device forget the seed it is holding, and take the card out.
     *
     * Every flow that loads a seed off a card ends with this, so that the next
     * one starts from a device holding nothing. It is the honest state for a
     * signer between jobs, and it is also what keeps the menus predictable: the
     * Seeds screen offers "Load a seed" straight away when there is no seed
     * loaded, and a list of seeds when there is.
     */
    function forgetTheSeed(card) {
      return [
        act("Make the device forget the seed: press right to Seeds, then select.",
            keys(["ArrowRight", "Enter"]), screenIs("ButtonListScreen")),
        act("Select the loaded seed.", keys(["Enter"]), screenIs("SeedOptionsScreen")),
        act("Down five times to Discard, then select.",
            keys(["ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
            screenIs("WarningScreen")),
        act("Confirm: down once, then select.",
            keys(["ArrowDown", "Enter"]), screenIs("MainMenuScreen")),
        act("Take " + card + " out of the reader.",
            function () { t.tray.eject(); }, inserted(-1)),
      ];
    }

    /** The phone holds a QR up to the device's camera. */
    function handUp(caption, payload, until) {
      return act(null, null, function (context) {
        t.transfer("in", caption);
        t.paintMatrix(scope.QREncode.matrix(
          typeof payload === "function" ? payload(context) : payload));
        return until(context).then(function (value) {
          t.endTransfer();
          return value;
        });
      });
    }

    /** The same, for something too big for one code: the frames cycle. */
    function handUpFrames(caption, frames, until) {
      return act(null, null, function (context) {
        t.transfer("in", caption);
        var list = typeof frames === "function" ? frames(context) : frames;
        var at = 0;
        var done = false;
        (function cycle() {
          if (done) return;
          t.paintMatrix(scope.QREncode.matrix(list[at % list.length]));
          at++;
          setTimeout(cycle, 550);
        })();
        return until(context).then(function (value) {
          done = true;
          t.endTransfer();
          return value;
        }, function (error) {
          done = true;
          t.endTransfer();
          throw error;
        });
      });
    }

    /** The phone reads whatever the device is showing. */
    function readOff(caption, handle, timeout) {
      return act(null, null, function (context) {
        t.transfer("out", caption);
        var collector = null;
        return t.poll(timeout || 180000, function () {
          t.mirrorDevice();
          var text = t.readDevice();
          if (!text) return false;
          if (text.toLowerCase().indexOf("ur:") === 0) {
            collector = collector || scope.URDecode.collector();
            collector.receive(text);
            // Real sub-step progress: codes actually read, out of the number
            // this transfer turned out to have.
            if (collector.parts()) {
              t.caption.textContent = caption + " Code " + collector.have()
                                    + " of " + collector.parts() + ".";
              t.subProgress(collector.have() / collector.parts());
            }
            if (!collector.done()) return false;
            return handle(context, collector) || true;
          }
          return handle(context, text) || true;
        }, "the QR on the device's screen").then(function (value) {
          t.endTransfer();
          return value;
        });
      });
    }

    // -------------------------------------------------- a seed onto a card

    function seedOntoCard(i) {
      var seed = SEEDS[i];
      var card = "Card " + seed.card;
      return step(
        "Put a test seed on " + card,
        "The seed is scanned in, written to the card, and then forgotten by the "
        + "device, so from here on the only copy is on the card. The card is "
        + "blank, so it asks for a PIN and then takes one twice: that is the "
        + "card's own ceremony, and it is real here.",
        [
          act("Click " + card + " in the tray to put it in the reader.",
              function () { t.tray.insert(i); }, inserted(i)),
          act("Press the select button to open Scan.",
              keys(["Enter"]), screenIs("ScanScreen")),
          handUp("The twelve word test seed for " + card + ", as a SeedQR.",
                 seed.seedqr, screenIs("SeedFinalizeScreen", 240000)),
          act("The device shows the seed's fingerprint. Select for Done.",
              keys(["Enter"]), screenIs("SeedOptionsScreen")),
          act("Go down three times to Backup seed, then select.",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("ButtonListScreen")),
          act("Go down once to To SeedKeeper, then select.",
              keys(["ArrowDown", "Enter"]), screenIs("SeedAddPassphraseScreen")),
          act("The card is asked for its PIN. Press select four times, then the "
              + "third side button to save.",
              pin(), screenIs("WarningScreen")),
          act("The card has no PIN yet. Select to give it one.",
              keys(["Enter"]), screenIs("SeedAddPassphraseScreen")),
          act("Choose the PIN: select four times, then the third side button.",
              pin(), screenIs("SeedAddPassphraseScreen")),
          act("Type it once more to confirm it.",
              pin(), screenIs("LargeIconStatusScreen")),
          act("The card is set up. Select to carry on.",
              keys(["Enter"]), screenIs("SeedAddPassphraseScreen")),
          act("Accept the label the device offers, which is the seed's own "
              + "fingerprint: the third side button.",
              keys(["3"]),
              logged("\\[card\\] Card " + seed.card + " stored secret", 240000)),
          act(null, null, screenIs("LargeIconStatusScreen")),
          act("The seed is on the card. Select to finish.",
              keys(["Enter"]), screenIs("SeedOptionsScreen")),
          act("Now make the device forget it: down five times to Discard, then select.",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("WarningScreen")),
          act("Confirm: down once, then select.",
              keys(["ArrowDown", "Enter"]), screenIs("MainMenuScreen")),
          act("Take " + card + " back out of the reader.",
              function () { t.tray.eject(); }, inserted(-1)),
        ]);
    }

    // -------------------------------------------------- the key off a card

    function keyOffCard(i) {
      var seed = SEEDS[i];
      var card = "Card " + seed.card;
      return step(
        "Read " + card + "'s public key",
        "The card gives the seed back, the device works out the account's "
        + "public key, and shows it as a QR for the coordinator to photograph. "
        + "A public key is not a spending key: it can make addresses and check "
        + "them, and it can do nothing else.",
        [
          act("Click " + card + " to put it in the reader.",
              function () { t.tray.insert(i); }, inserted(i)),
          act("Press right to Seeds, then select.",
              keys(["ArrowRight", "Enter"]), screenIs("ButtonListScreen")),
          act("Down three times to From SeedKeeper, then select.",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("SeedAddPassphraseScreen")),
          act("Type the card's PIN: select four times, then the third side button.",
              pin(), screenIs("ButtonListScreen", 240000)),
          act("Select the one secret the card is carrying.",
              keys(["Enter"]),
              logged("\\[card\\] Card " + seed.card + " exporting secret", 240000)),
          act(null, null, screenIs("SeedFinalizeScreen", 240000)),
          act("Select for Done.", keys(["Enter"]), screenIs("SeedOptionsScreen")),
          act("Down once to Export Xpub, then select.",
              keys(["ArrowDown", "Enter"]), screenIs("ButtonListScreen")),
          act("Down once to Multisig, then select.",
              keys(["ArrowDown", "Enter"]), screenIs("ButtonListScreen")),
          act("Choose Native Segwit, the first in the list.",
              keys(["Enter"]), screenIs("ButtonListScreen")),
          act("Down once to Static, so the key comes as one code, then select.",
              keys(["ArrowDown", "Enter"]), settle(1500)),
          // A privacy warning and a details page may sit between here and the
          // QR, depending on settings, so this is driven by where it has
          // arrived rather than by a fixed number of presses.
          advance("QRDisplayScreen", 5),
          readOff(card + "'s account public key, photographed off the device's screen.",
                  function (context, text) {
                    context.state.keys = context.state.keys || [];
                    context.state.keys[i] = text;
                    t.detail(card + " account key", text);
                    return true;
                  }),
          act("Any button leaves the QR.", keys(["Enter"]), screenIs("MainMenuScreen")),
        ].concat(forgetTheSeed(card)));
    }

    // -------------------------------------------------- signing, twice

    function signWith(i) {
      var seed = SEEDS[i];
      var card = "Card " + seed.card;
      return step(
        "Sign with " + card,
        "The coordinator hands the unfinished transaction to the device, the "
        + "device shows what it would be signing, and the card's seed signs it. "
        + "The signature comes back as a QR. One signature is not enough to "
        + "move anything, which is the point of a 2 of 3.",
        [
          act("Click " + card + " to put it in the reader.",
              function () { t.tray.insert(i); }, inserted(i)),
          act("Press right to Seeds, then select.",
              keys(["ArrowRight", "Enter"]), screenIs("ButtonListScreen")),
          act("Down three times to From SeedKeeper, then select.",
              keys(["ArrowDown", "ArrowDown", "ArrowDown", "Enter"]),
              screenIs("SeedAddPassphraseScreen")),
          act("Type the card's PIN.", pin(), screenIs("ButtonListScreen", 240000)),
          act("Select the secret on the card.", keys(["Enter"]),
              logged("\\[card\\] Card " + seed.card + " exporting secret", 240000)),
          act(null, null, screenIs("SeedFinalizeScreen", 240000)),
          act("Select for Done.", keys(["Enter"]), screenIs("SeedOptionsScreen")),
          homeAgain(),
          act("Press select to open Scan.", keys(["Enter"]), screenIs("ScanScreen")),
          handUpFrames("The transaction to be signed, split across several codes "
                       + "because one would be too dense to read.",
                       function (context) { return context.state.frames; },
                       function () {
                         return t.poll(300000, function () {
                           var screen = t.currentScreen();
                           return screen && screen !== "ScanScreen";
                         }, "the device to take the transaction");
                       }),
          advance("PSBTFinalizeScreen", 10,
                  "Work through the review screens, pressing select, until the "
                  + "device offers to sign."),
          act("Approve it.", keys(["Enter"]), screenIs("QRDisplayScreen", 240000)),
          readOff("The signature from " + card + ", photographed off the device's screen.",
                  function (context, collector) {
                    var psbt = C.toBase64(collector.psbt());
                    context.state.signed = context.state.signed || [];
                    context.state.signed.push(psbt);
                    t.detail(card + " signed PSBT", psbt);
                    return true;
                  }, 300000),
          act("Any button leaves the QR.", keys(["Enter"]),
              function () {
                return t.poll(60000, function () {
                  return t.currentScreen() === "MainMenuScreen";
                }, "the home screen");
              }),
        ].concat(forgetTheSeed(card)));
    }

    // -------------------------------------------------- the coordinator's own

    /** Work only the coordinator can do, so it happens in both modes. */
    function coordinator(work) {
      return act(null, null, function (context) {
        return Promise.resolve(work(context)).then(function () {
          // Deriving an address takes a fifth of a second and puts a sentence
          // up worth reading -- an address, an amount, a transaction id -- so
          // the step waits for a reader rather than for itself.
          return t.pace(t.verdict.textContent || t.stepText.textContent, STEP_MAX);
        });
      });
    }

    var steps = [];
    for (var s = 0; s < 3; s++) steps.push(seedOntoCard(s));
    for (var k = 0; k < 3; k++) steps.push(keyOffCard(k));

    steps.push(step(
      "Build the 2 of 3",
      "Three public keys make one wallet. Any two of the three can spend from "
      + "it; any one of them alone can do nothing. The description of that "
      + "wallet is called a descriptor, and it is not a secret.",
      [
        coordinator(function (context) {
          t.showFace("Building the wallet", "Sorting three public keys into one "
                     + "2 of 3 and working out an address to receive at.");
          // A fresh address, the way a wallet hands out a fresh one every time
          // rather than reusing the first. It also keeps two visitors doing this
          // at once out of each other's way: the three test seeds are public and
          // the same for everybody, so the wallet is the same wallet, and only
          // the address makes the coins theirs to watch.
          context.state.index = Math.floor(Math.random() * 10000);
          return C.buildWallet(context.state.keys).then(function (wallet) {
            context.state.wallet = wallet;
            t.detail("descriptor", wallet.descriptor);
            return C.deriveAddress(wallet, 0, context.state.index);
          }).then(function (receive) {
            context.state.receive = receive;
            t.detail("receive address", receive.address + "  (receive number "
                     + context.state.index + ")");
            t.detail("witness script", C.hex(receive.witnessScript));
            t.verdict.dataset.state = "good";
            t.verdict.textContent = "The wallet is built. Its address for this "
              + "run is " + receive.address;
            t.showFace("2 of 3 wallet", receive.address);
          });
        }),
      ]));

    steps.push(step(
      "Tell the device about the wallet",
      "The device has only ever seen one key at a time. Giving it the whole "
      + "descriptor is what lets it recognise its own key in a transaction "
      + "later, and check that an address really belongs to this wallet.",
      [
        act("Press select to open Scan.", keys(["Enter"]), screenIs("ScanScreen")),
        handUp("The 2 of 3 descriptor, which names all three keys and says two "
               + "of them are needed.",
               function (context) {
                 return context.state.wallet.descriptor;
               },
               screenIs("MultisigWalletDescriptorScreen", 240000)),
        act("The device shows the wallet. Select to accept it.",
            keys(["Enter"]),
            function () {
              return t.poll(120000, function () {
                var screen = t.currentScreen();
                return screen && screen !== "MultisigWalletDescriptorScreen";
              }, "the device to accept the wallet");
            }),
        homeAgain(),
      ]));

    steps.push(step(
      "Ask Bitsaga Signet's faucet for coins",
      "Bitsaga Signet is our own Bitcoin test network. It makes a block every "
      + "thirty seconds, so a confirmation happens while you watch. " + NOT_REAL,
      [
        coordinator(function (context) {
          t.showFace("Asking the faucet", "Bitsaga Signet, " + context.state.receive.address);
          return C.network.claim(context.state.receive.address).then(function (paid) {
            context.state.funding = paid.txid;
            t.detail("faucet transaction", paid.txid);
            t.verdict.dataset.state = "good";
            t.verdict.textContent = "The faucet sent "
              + (paid.amount_sat / 1e8).toFixed(8) + " to the wallet. " + NOT_REAL;
            t.showFace("Coins on the way", paid.txid);
          });
        }),
        coordinator(function (context) {
          return waitForBlock(t, context.state.funding, "the faucet's payment",
                              "Waiting for Bitsaga Signet to put it in a block.");
        }),
      ]));

    steps.push(step(
      "Build the spend",
      "A signing device cannot know what a wallet owns or what a fee should be, "
      + "so the coordinator works that out and hands over an unfinished "
      + "transaction for the device to sign. This one pays a second address of "
      + "the same wallet, because there is nobody on this network to pay. "
      + NOT_REAL,
      [
        coordinator(function (context) {
          var state = context.state;
          t.showFace("Building the spend", "One input, one output, and the "
                     + "script that needs two signatures.");
          return C.network.proof(state.funding).then(function (proof) {
            var outputs = C.transactionOutputs(proof.tx);
            var script = C.hex(state.receive.scriptPubkey);
            var ours = outputs.filter(function (out) { return out.script === script; })[0];
            if (!ours) throw new Error("the faucet's transaction does not pay this wallet");
            state.input = { txid: state.funding, vout: ours.index, value: ours.value };
            return C.deriveAddress(state.wallet, 1, state.index);
          }).then(function (change) {
            state.change = change;
            state.amount = state.input.value - FEE;
            state.psbt = C.toBase64(C.buildPsbt(state.input, state.receive,
                                                change.scriptPubkey, state.amount));
            state.frames = specterFrames(state.psbt);
            t.detail("spending", state.input.txid + ":" + state.input.vout);
            t.detail("paying", change.address);
            t.detail("unsigned PSBT", state.psbt);
            t.verdict.dataset.state = "good";
            t.verdict.textContent = "Ready to sign: "
              + (Number(state.amount) / 1e8).toFixed(8) + " to " + change.address
              + ", with " + FEE + " sat of fee. " + NOT_REAL;
            t.showFace("Unsigned transaction", state.frames.length + " codes to hold up");
          });
        }),
      ]));

    steps.push(signWith(0));
    steps.push(signWith(1));

    steps.push(step(
      "Put the two signatures together and send it",
      "Neither signature alone moves anything. Together they satisfy the 2 of 3, "
      + "and the coordinator can finish the transaction and hand it to the "
      + "network.",
      [
        coordinator(function (context) {
          var state = context.state;
          t.showFace("Finishing the transaction", "Two signatures into one witness.");
          var signatures = {};
          state.signed.forEach(function (psbt) {
            Object.assign(signatures, C.partialSignatures(psbt));
          });
          t.detail("signatures collected", String(Object.keys(signatures).length), true);
          return C.finalise(state.input, state.receive, state.change.scriptPubkey,
                            state.amount, signatures).then(function (final) {
            state.spend = final;
            t.detail("signed transaction", final.hex);
            t.detail("transaction id", final.txid);
            return C.network.broadcast(final.hex);
          }).then(function (sent) {
            if (sent.txid && sent.txid !== state.spend.txid) {
              throw new Error("the network gave the transaction a different id");
            }
            t.verdict.dataset.state = "good";
            t.verdict.textContent = "Sent to Bitsaga Signet: " + state.spend.txid;
            t.showFace("Broadcast", state.spend.txid);
          });
        }),
        coordinator(function (context) {
          return waitForBlock(t, context.state.spend.txid, "the spend",
                              "Waiting for the spend to be mined.");
        }),
      ]));

    steps.push(step(
      "Done",
      "Three seeds on three cards, a wallet that none of them can spend from "
      + "alone, and a transaction that two of them signed and the network "
      + "accepted. " + NOT_REAL,
      [
        coordinator(function (context) {
          t.verdict.dataset.state = "good";
          t.verdict.textContent = "Confirmed on Bitsaga Signet: "
            + context.state.spend.txid;
          t.showFace("Confirmed", context.state.spend.txid);
          t.doText.textContent =
            "Everything above happened in this tab. The one thing to carry away: "
            + "all three keys were on one device here, which is fine for a demo "
            + "and wrong for real funds, where the keys belong in different "
            + "places and different hands.";
          return Promise.resolve();
        }),
      ]));

    return steps;
  }

  /**
   * Wait for a transaction to be in a block.
   *
   * The proof endpoint answers 404 until then, so asking for the proof is the
   * confirmation check. The progress line follows the chain rather than a
   * guess: Bitsaga Signet makes a block roughly every thirty seconds, and the
   * status endpoint says how old the last one is, so the line grows towards the
   * next block and starts again if that block did not carry the transaction.
   */
  function waitForBlock(t, txid, what, say) {
    t.showFace("Waiting for a block", txid);
    var deadline = Date.now() + 300000;
    return new Promise(function (resolve, reject) {
      (function again() {
        C.network.proof(txid).then(function (proof) {
          t.detail(what + " confirmed in block", String(proof.height), true);
          t.verdict.dataset.state = "good";
          t.verdict.textContent = "Confirmed on Bitsaga Signet in block "
            + proof.height + ". " + NOT_REAL;
          t.showFace("Confirmed", "Block " + proof.height);
          resolve();
        }, function (error) {
          if (error.status !== 404) return reject(error);
          if (Date.now() > deadline) {
            return reject(new Error("Bitsaga Signet has not confirmed this "
                                    + "transaction. " + say));
          }
          C.network.status().then(function (status) {
            t.subProgress(Math.min(1, (status.last_block_age_seconds || 0) /
                                      (status.block_seconds || 30)));
          }).catch(function () {}).then(function () {
            setTimeout(again, 3000);
          });
        });
      })();
    });
  }

  /**
   * A PSBT split into the frames SeedSigner reassembles by plain concatenation.
   * Small frames rather than one dense code, which is what every coordinator
   * does and what a 640 by 480 camera can actually read.
   */
  function specterFrames(payload, size) {
    var chunk = size || 280;
    var parts = [];
    for (var at = 0; at < payload.length; at += chunk) {
      parts.push(payload.substr(at, chunk));
    }
    return parts.map(function (part, i) {
      return "p" + (i + 1) + "of" + parts.length + " " + part;
    });
  }

  // ------------------------------------------------------------ what the page uses

  scope.WalletTutorial = {
    /** The one button on the resting page. */
    offer: function (container) {
      var style = element("style");
      style.textContent = CSS;
      document.head.appendChild(style);
      var button = element("button", "tut-start", "Start multisig walkthrough");
      button.type = "button";
      button.id = "start-tutorial";
      button.addEventListener("click", function () {
        var params = new URLSearchParams(location.search);
        params.set("tutorial", "1");
        location.search = params.toString();
      });
      container.appendChild(button);
    },

    mount: function (options) {
      var tutorial = new Tutorial(options);
      scope.WalletTutorial.current = tutorial;
      // The same decoder the wallet's own camera path uses, because the phone
      // reading the device's screen is the same job in the other direction.
      if (!scope.jsQR) {
        var tag = document.createElement("script");
        tag.src = "jsQR.js";
        document.head.appendChild(tag);
      }
      return tutorial;
    },

    /**
     * The camera the device gets while the tutorial is running: the phone's own
     * screen, as a stream. Real pixels, decoded by the page's real decoder;
     * only the lens is missing, and no webcam is ever opened.
     */
    cameraSource: function () {
      var tutorial = scope.WalletTutorial.current;
      if (!tutorial) throw new Error("the tutorial is not mounted");
      return tutorial.canvas.captureStream(25);
    },

    seeds: SEEDS,
    specterFrames: specterFrames,
  };
})(typeof self !== "undefined" ? self : this);
