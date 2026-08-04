// The device boots into DOOM. The wallet is what is behind it.
//
// This is the browser half of seedsigner-os/boot-game, where the image boots a
// game and KEY1, KEY2, KEY3 hands off to SeedSigner with an os.execv, so that no
// game code is left in memory while the signing application is handling keys.
// A tab cannot execv, and the closest thing to it is this: DOOM is stopped and
// dropped before the wallet is asked for, and the wallet's 26MB of Pyodide is
// never fetched at all until somebody spells the sequence.
//
// That last part is why the browser version is worth more than the joke. The
// page used to spend about forty seconds booting CPython in WebAssembly before
// it drew anything, on every visit, for everyone. Now the device is playable in
// about a second and only the people who came for a wallet pay for one.
//
// What this module does NOT do is choose a game. On the device a chooser
// appears when more than one game is installed; here exactly one is, so the
// page boots straight into it and there is no menu between DOOM and the wallet.
//
// DOOM itself is not here. It arrives as a wrapper defining window.DoomRun (see
// doom-run.js), and everything below is written to survive that wrapper being
// absent, broken, or slow: the unlock is live from the moment this module takes
// the device, whether or not a single frame has ever been drawn. A broken
// easter egg must never stand between somebody and their device, which is the
// rule the device itself is built on.

(function (scope) {
  "use strict";

  // The three side buttons, left to right, exactly as bootgame/unlock.py has
  // them. None of them steer, so the sequence cannot be spelled by ordinary
  // play, and it reads clearly on camera.
  var SEQUENCE = ["key1", "key2", "key3"];

  // The channels the page and the device shell speak, named. The same list as
  // KEY_NAMES in wallet.html, because it is the same eight buttons: one input
  // path, switched by what is running, rather than a second one for the game.
  var KEY_NAMES = [null, "up", "down", "left", "right", "select", "key1", "key2", "key3"];

  // Handed to the wrapper rather than found by it, so the page stays the one
  // thing that decides what is served from where. Freedoom, so the licensing
  // stays clean; about 10MB gzipped, which is why it is fetched here and not in
  // the offline shell.
  var WAD_URL = "freedoom1.wad";

  // What DOOM draws, and how. RGB565 big endian is what the ST7789 panel takes
  // over SPI on the real device, so the wrapper hands over the same bytes the
  // hardware would have been sent rather than something invented for a browser.
  // The panel the simulator draws. Its worker asks the firmware for
  // st7789_320x240, which is the SeedSigner Plus, and the browser build of DOOM
  // is configured for the same one. Built at the original's 240x240 it sat in
  // the middle of this panel with black bars down both sides, which was not the
  // device being honest about its shape, just DOOM in a box.
  var DOOM_W = 320;
  var DOOM_H = 240;

  // A drawn key reports that it went down and never that it came up: the shell
  // hands out one press per finger and no release. So a click is a pulse of
  // this long, which is enough for DOOM to act on and short enough that a
  // second click is a second press. The keyboard does not need this, and does
  // not use it: a held arrow key is genuinely held, which is the only way a
  // person can walk anywhere.
  var TAP_MS = 120;

  // off: never asked for, or already handed over. Only "holding" routes keys.
  var state = "off";
  var options = null;
  var running = false;   // has the wrapper actually got as far as playing

  // Which buttons are down, so that the browser repeating a held key is not
  // read as a second press. The device shell has the same rule for a held
  // finger, and hardware has it for free: a button that is down is not pressed
  // again until it comes up.
  var held = {};

  // How far into the sequence the presses so far have got.
  var progress = 0;

  // The panel, kept and reused. Only the square in the middle of it ever
  // changes, so the black either side of DOOM is written once and left alone,
  // and no frame allocates anything.
  var panel = null;
  var panelW = 0;
  var panelH = 0;
  var offsetX = 0;
  var offsetY = 0;

  /**
   * Feed one press to the unlock. True on the press that completes it.
   *
   * A transcription of UnlockSequence.feed in bootgame/unlock.py, including the
   * part that is easy to leave out: a wrong press restarts, but that press may
   * itself be a valid opening for the next attempt, which matters the moment a
   * sequence repeats a key. Completing it resets, so the sequence returns true
   * once rather than staying completed.
   */
  function feed(name) {
    if (name === SEQUENCE[progress]) {
      progress += 1;
      if (progress === SEQUENCE.length) {
        progress = 0;
        return true;
      }
      return false;
    }
    progress = name === SEQUENCE[0] ? 1 : 0;
    return false;
  }

  /**
   * One DOOM frame, onto the device's panel.
   *
   * Converted to the same RGB the wallet's own renderer posts and handed to the
   * page's paint(), rather than drawn here: there is one thing painting that
   * canvas and this is not a second one. Centred rather than scaled, because
   * DOOM draws the same 320x240 panel the wallet does, so it lands on it one
   * pixel to one pixel, with no scaling and nothing to centre.
   */
  function onFrame(frame) {
    // A frame already in flight when stop() was called is not a reason to paint
    // DOOM over a wallet. The panel belongs to whatever is holding the device.
    if (state !== "holding") return;
    if (frame.length !== DOOM_W * DOOM_H * 2) {
      console.warn("doom frame is " + frame.length + " bytes, expected "
                   + DOOM_W * DOOM_H * 2);
      return;
    }
    for (var y = 0; y < DOOM_H; y++) {
      var src = y * DOOM_W * 2;
      var dst = ((offsetY + y) * panelW + offsetX) * 3;
      for (var x = 0; x < DOOM_W; x++, src += 2, dst += 3) {
        // Big endian: the high byte carries all five red bits and the top three
        // of green. Each channel is expanded by repeating its own top bits into
        // the bottom of the byte, so full scale stays full scale and black
        // stays black.
        var pixel = (frame[src] << 8) | frame[src + 1];
        var r = (pixel >> 11) & 31;
        var g = (pixel >> 5) & 63;
        var b = pixel & 31;
        panel[dst] = (r << 3) | (r >> 2);
        panel[dst + 1] = (g << 2) | (g >> 4);
        panel[dst + 2] = (b << 3) | (b >> 2);
      }
    }
    options.paint(panel);
  }

  // The wrapper is missing, or it could not start. Say so and give the device
  // to the wallet: this page is a wallet simulator that boots into a game, and
  // a game that will not load must not cost anyone the wallet.
  function unavailable(reason) {
    if (state !== "holding") return;
    state = "off";
    options.onUnavailable(reason);
  }

  // The sequence, spelled. DOOM is stopped before the wallet is asked for, so
  // that the wallet gets a panel nothing else is still drawing on: it is the
  // nearest a tab gets to the device replacing its own process.
  function unlock() {
    state = "off";
    if (running) {
      try {
        scope.DoomRun.stop();
      } catch (error) {
        // Nothing DOOM does on its way out may stand between somebody and the
        // wallet they just asked for.
        console.warn("doom did not stop cleanly: " + error);
      }
    }
    running = false;
    options.onUnlock();
  }

  scope.DoomBoot = {
    /**
     * Take the device and start DOOM on it.
     *
     * options.paint(bytes)   the page's own painter, width * height * 3 of RGB
     * options.width/height   the panel DOOM is drawn into
     * options.status(text)   the line under the device
     * options.onUnlock()     the sequence was spelled: start the wallet
     * options.onUnavailable(reason)   DOOM cannot run: start the wallet anyway
     */
    boot: function (opts) {
      options = opts;
      panelW = opts.width;
      panelH = opts.height;
      panel = new Uint8Array(panelW * panelH * 3);
      offsetX = (panelW - DOOM_W) >> 1;
      offsetY = (panelH - DOOM_H) >> 1;

      // Before anything is loaded, and deliberately: from here the three side
      // buttons open the wallet whether or not DOOM ever draws a pixel. A WAD
      // that never arrives leaves a black screen, and a black screen must still
      // be a SeedSigner.
      state = "holding";

      if (!scope.DoomRun) {
        unavailable("this page was built without it");
        return;
      }
      opts.status("loading DOOM…");
      try {
        scope.DoomRun.start({ wadUrl: WAD_URL, onFrame: onFrame });
      } catch (error) {
        unavailable(String(error && error.message ? error.message : error));
        return;
      }
      Promise.resolve(scope.DoomRun.ready).then(function () {
        // The frames say more than the line does, and the hint under them says
        // the only thing left worth saying.
        running = true;
        opts.status("");
      }, function (error) {
        unavailable(String(error && error.message ? error.message : error));
      });
    },

    /**
     * One key. True if DOOM has the device and the page should do nothing more.
     *
     * down is true or false for a key that is genuinely held and released,
     * which is what the keyboard gives; undefined for a drawn key, which
     * reports no release and is therefore a pulse.
     */
    key: function (channel, down) {
      if (state !== "holding") return false;
      var name = KEY_NAMES[channel];

      if (down === false) {
        held[name] = false;
        if (running) scope.DoomRun.key(name, false);
        return true;
      }
      if (held[name]) return true;
      held[name] = true;

      // DOOM sees the press, and so does the unlock, exactly as DG_GetKey on
      // the device both answers the game and watches for the sequence.
      if (running) scope.DoomRun.key(name, true);
      if (down === undefined) {
        setTimeout(function () {
          held[name] = false;
          if (running) scope.DoomRun.key(name, false);
        }, TAP_MS);
      }
      if (feed(name)) unlock();
      return true;
    },
  };
})(window);
