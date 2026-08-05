// The camera channel, loaded by both the page and the worker.
//
// The wallet's Python runs in the worker and is permanently blocked inside the
// controller's main loop, so the worker can never come back to its event loop to
// service a postMessage. A SharedArrayBuffer is the only thing that can carry
// camera data across, the same reason the buttons already use one.
//
// Both halves live in this file because they have to agree byte-for-byte on the
// layout of that buffer, and a layout written down twice is a layout that drifts.
// The page loads this with a script tag, the worker with importScripts.

(function (scope) {
  "use strict";

  // Int32 header slots.
  var CMD = 0;         // 0 stop, 1 start. Written by the worker.
  var STATE = 1;       // see STATES. Written by the page.
  var FRAME_SEQ = 2;   // bumped once per published frame
  var FRAME_W = 3;
  var FRAME_H = 4;
  var QR_LEN = 5;      // >0 while a decoded payload is waiting to be claimed
  var ERR_LEN = 6;

  var STATES = { IDLE: 0, STARTING: 1, RUNNING: 2, FAILED: 3 };

  // The preview only ever lands on a 320x240 canvas, and every byte of it is
  // copied into a PIL image on the Python side, so it is published small. The
  // decode runs against the full capture instead, where the QR is still sharp.
  var PREVIEW_W = 240;
  var PREVIEW_H = 240;
  var CAPTURE_W = 640;
  var CAPTURE_H = 480;

  var HEADER_BYTES = 64;
  var QR_OFFSET = HEADER_BYTES;
  var QR_MAX = 8192;
  var ERR_OFFSET = QR_OFFSET + QR_MAX;
  var ERR_MAX = 256;
  var FRAME_OFFSET = ERR_OFFSET + ERR_MAX;
  var TOTAL_BYTES = FRAME_OFFSET + PREVIEW_W * PREVIEW_H * 3;

  // ~15fps. The Python decode loop is slower than this, so the page is never the
  // bottleneck and frames it publishes in between are simply overwritten.
  var TICK_MS = 66;

  // How long the camera may deliver nothing before the page says so, and how
  // often it is asked. Comfortably longer than a tick and than any single
  // decode, so an ordinarily slow frame never trips it.
  var STALL_MS = 4000;
  var WATCH_MS = 1000;

  function header(sab) {
    return new Int32Array(sab, 0, HEADER_BYTES / 4);
  }

  function writeString(sab, hdr, offset, max, slot, text) {
    var encoded = new TextEncoder().encode(String(text)).slice(0, max);
    new Uint8Array(sab, offset, max).set(encoded);
    Atomics.store(hdr, slot, encoded.length);
  }

  function readString(sab, hdr, offset, slot) {
    var length = Atomics.load(hdr, slot);
    if (length <= 0) return "";
    return new TextDecoder().decode(new Uint8Array(sab, offset, length).slice());
  }

  // ---------------------------------------------------------------- page half

  // options.source, if given, hands back a MediaStream instead of the webcam.
  // The tutorial passes the phone mock's own canvas: the device's camera is
  // pointed at the phone's screen, which is a picture on this page, so nothing
  // below this line changes and no webcam permission is ever asked for.
  //
  // options.onTrouble, if given, is called with a sentence whenever the camera
  // is delivering nothing, and with "" once it delivers again. It is a callback
  // rather than anything drawn from here because this file is loaded by the
  // worker as well, where there is no document to draw into.
  function runPage(sab, options) {
    var hdr = header(sab);
    var source = (options && options.source) || null;
    var onTrouble = (options && options.onTrouble) || function () {};
    var stream = null;
    var video = null;
    var capture = null;
    var preview = null;
    var decoder = null;
    var lastFrameAt = 0;
    var trouble = "";

    function setState(state) {
      Atomics.store(hdr, STATE, state);
      Atomics.notify(hdr, STATE);
    }

    function fail(error) {
      writeString(sab, hdr, ERR_OFFSET, ERR_MAX, ERR_LEN, explain(error));
      shutdown(STATES.FAILED);
    }

    // getUserMedia's own words are written for developers. "Requested device not
    // found" is accurate and tells a visitor nothing about what to do about it,
    // and it is the first thing most of them will ever see go wrong here, since
    // Scan is the first tile on the device's home screen and starts selected.
    //
    // The name picks the sentence rather than the message: the name is a short
    // list fixed by the spec, the message is browser-specific prose. Anything
    // not on the list keeps its own message, which is better than a wrong guess.
    function explain(error) {
      switch ((error && error.name) || "") {
        case "NotFoundError":
        case "OverconstrainedError":
          return "There is no camera on this device, so scanning will not work. " +
                 "Everything else on the simulator will.";
        case "NotAllowedError":
          return "The camera was refused, so scanning will not work. Everything " +
                 "else will. Allow it in the address bar and press Scan again.";
        case "NotReadableError":
          return "Something else on this machine is holding the camera, so " +
                 "scanning will not work. Close it and press Scan again.";
        case "SecurityError":
          return "A browser only hands a camera to a secure page, and this one " +
                 "is not, so scanning will not work here.";
        default:
          return (error && error.message) || String(error) || "The camera would not start.";
      }
    }

    function shutdown(state) {
      if (stream) {
        stream.getTracks().forEach(function (track) { track.stop(); });
        stream = null;
      }
      if (video) {
        video.srcObject = null;
        video = null;
      }
      setState(state === undefined ? STATES.IDLE : state);
    }

    // A camera that starts and then goes quiet is the one failure the device
    // cannot report for itself. ScanScreen's loop polls its buttons inside
    // `if frame is not None`, so a scan screen that gets no frames is also a
    // scan screen nobody can leave: every press is ignored and the device looks
    // like it has hung. Failing to *start* is already covered, since that
    // raises CameraConnectionError in the shim and the device draws its own
    // error screen; what is left is this, which nothing else notices.
    //
    // On its own timer rather than inside tick(), so that it still fires when
    // the loop itself is what stopped.
    function report(message) {
      if (message === trouble) return;
      trouble = message;
      onTrouble(message);
    }

    function watch() {
      if (Atomics.load(hdr, CMD) === 0) return report("");

      var state = Atomics.load(hdr, STATE);
      // The worker only reads the error string while it is parked inside
      // start(). One that fails after that has nobody else to tell.
      if (state === STATES.FAILED) {
        return report(readString(sab, hdr, ERR_OFFSET, ERR_LEN) || "the camera stopped");
      }
      if (state !== STATES.RUNNING) return;
      if (Date.now() - lastFrameAt < STALL_MS) return;

      report("The camera is not sending any pictures. The device only checks its " +
             "buttons after it reads one, so it will not answer a press until they " +
             "start again: close whatever else is using the camera, or reload.");
    }

    function makeCanvas(width, height) {
      var canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      return { canvas: canvas, ctx: canvas.getContext("2d", { willReadFrequently: true }) };
    }

    // Both decoders are used, for different halves of the job.
    //
    // BarcodeDetector goes in front because deciding "there is no QR here" is
    // what happens on almost every frame, and native does that faster. But it
    // only ever exposes rawValue, a string, and a CompactSeedQR is raw entropy
    // bytes rather than text -- bytes that do not survive being decoded as
    // characters and re-encoded. So jsQR, which returns the codewords
    // themselves, is the only thing allowed to produce a payload.
    //
    // There is deliberately no falling back to rawValue when jsQR comes up
    // empty. A mis-read string can still be a plausible length, and 16, 20, 24,
    // 28 or 32 bytes is all it takes for DecodeQR to accept it as a
    // CompactSeedQR -- which means the wallet would load a seed that was never
    // in front of the camera. A scan that fails and retries is recoverable; a
    // wrong seed presented as right is not. Caught by test_scan_native.py,
    // which used to reach a valid-looking fingerprint from pure garbage.
    //
    // jsQR is served from this origin: the page sends COEP require-corp, so a
    // CDN would be refused.
    function makeDecoder() {
      return loadJsQR().then(withJsQR);
    }

    function loadJsQR() {
      return new Promise(function (resolve, reject) {
        if (scope.jsQR) return resolve(scope.jsQR);
        var tag = document.createElement("script");
        tag.src = "jsQR.js";
        tag.onload = function () { resolve(scope.jsQR); };
        tag.onerror = function () { reject(new Error("jsQR.js did not load")); };
        document.head.appendChild(tag);
      });
    }

    function withJsQR(jsQR) {
      function readBytes(imageData) {
        var found = jsQR(imageData.data, imageData.width, imageData.height);
        return found ? Uint8Array.from(found.binaryData) : null;
      }

      return nativeDetector().then(function (native) {
        if (!native) {
          return {
            name: "jsQR",
            read: function (source, imageData) {
              return Promise.resolve(readBytes(imageData));
            },
          };
        }
        return {
          name: "BarcodeDetector+jsQR",
          read: function (source, imageData) {
            return native.detect(source).then(function (codes) {
              // Native says something is there; jsQR is what reads it. If jsQR
              // disagrees, report nothing and wait for the next frame.
              return codes.length ? readBytes(imageData) : null;
            });
          },
        };
      });
    }

    function nativeDetector() {
      if (!scope.BarcodeDetector) return Promise.resolve(null);
      return scope.BarcodeDetector.getSupportedFormats().then(function (formats) {
        if (formats.indexOf("qr_code") === -1) return null;
        return new scope.BarcodeDetector({ formats: ["qr_code"] });
      }).catch(function () { return null; });
    }

    function open() {
      if (source) return Promise.resolve().then(source);
      if (!scope.navigator || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        // getUserMedia is missing rather than merely refused when the page is not
        // a secure context, which over plain http on a LAN address it is not.
        return Promise.reject(new Error("no camera API here; needs https or localhost"));
      }
      return navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: "environment",
          width: { ideal: CAPTURE_W },
          height: { ideal: CAPTURE_H },
        },
        audio: false,
      });
    }

    function start() {
      setState(STATES.STARTING);
      // Cleared so that watch() above reports this attempt rather than the
      // reason the last one failed.
      Atomics.store(hdr, ERR_LEN, 0);
      return open().then(function (opened) {
        stream = opened;
        video = document.createElement("video");
        video.playsInline = true;
        video.muted = true;
        // srcObject rather than a blob: URL, so the page's own CSP does not have
        // to grow a media-src.
        video.srcObject = stream;
        return video.play();
      }).then(function () {
        return new Promise(function (resolve) {
          if (video.videoWidth) return resolve();
          video.addEventListener("loadedmetadata", function () { resolve(); }, { once: true });
        });
      }).then(function () {
        capture = makeCanvas(CAPTURE_W, CAPTURE_H);
        preview = makeCanvas(PREVIEW_W, PREVIEW_H);
        return makeDecoder();
      }).then(function (ready) {
        decoder = ready;
        // Which of the two decoders is in play is the first thing worth knowing
        // when a scan misbehaves on someone else's browser, so it is behind the
        // same ?debug=1 as everything else rather than always on.
        if (new URLSearchParams(location.search).has("debug")) {
          console.log("[cam] " + video.videoWidth + "x" + video.videoHeight +
                      " decoding with " + decoder.name);
        }
        // The stall clock starts here rather than when the camera was asked
        // for: getUserMedia can sit on a permission prompt for as long as
        // somebody takes to read it, and that is not a stall.
        lastFrameAt = Date.now();
        setState(STATES.RUNNING);
      }).catch(fail);
    }

    function publishFrame() {
      preview.ctx.drawImage(video, 0, 0, PREVIEW_W, PREVIEW_H);
      var rgba = preview.ctx.getImageData(0, 0, PREVIEW_W, PREVIEW_H).data;
      var rgb = new Uint8Array(sab, FRAME_OFFSET, PREVIEW_W * PREVIEW_H * 3);
      for (var src = 0, dst = 0; src < rgba.length; src += 4, dst += 3) {
        rgb[dst] = rgba[src];
        rgb[dst + 1] = rgba[src + 1];
        rgb[dst + 2] = rgba[src + 2];
      }
      Atomics.store(hdr, FRAME_W, PREVIEW_W);
      Atomics.store(hdr, FRAME_H, PREVIEW_H);
      // Bumped last: the sequence number is what tells the worker the bytes above
      // are worth reading. A frame can still tear if the worker reads mid-write,
      // which shows up as a seam in the preview and nowhere else, because the
      // decode never looks at these bytes.
      Atomics.add(hdr, FRAME_SEQ, 1);
      Atomics.notify(hdr, FRAME_SEQ);
      lastFrameAt = Date.now();
      report("");
    }

    function publishPayload(payload) {
      new Uint8Array(sab, QR_OFFSET, QR_MAX).set(payload.slice(0, QR_MAX));
      Atomics.store(hdr, QR_LEN, Math.min(payload.length, QR_MAX));
    }

    function tick() {
      if (Atomics.load(hdr, CMD) === 0) {
        if (Atomics.load(hdr, STATE) !== STATES.IDLE) shutdown();
        return Promise.resolve();
      }

      var state = Atomics.load(hdr, STATE);
      if (state === STATES.IDLE) return start();
      if (state !== STATES.RUNNING) return Promise.resolve();
      if (!video || !video.videoWidth) return Promise.resolve();

      publishFrame();

      // The worker has not claimed the last payload yet. Decoding another would
      // only overwrite it, and it is the same QR anyway.
      if (Atomics.load(hdr, QR_LEN) !== 0) return Promise.resolve();

      capture.ctx.drawImage(video, 0, 0, CAPTURE_W, CAPTURE_H);
      var imageData = capture.ctx.getImageData(0, 0, CAPTURE_W, CAPTURE_H);
      return decoder.read(capture.canvas, imageData).then(function (payload) {
        if (payload && payload.length) publishPayload(payload);
      });
    }

    function loop() {
      tick().catch(fail).then(function () {
        setTimeout(loop, TICK_MS);
      });
    }

    loop();
    setInterval(watch, WATCH_MS);
    return { decoderName: function () { return decoder && decoder.name; } };
  }

  // -------------------------------------------------------------- worker half

  function forWorker(sab) {
    var hdr = header(sab);
    var lastFrame = 0;

    return {
      // Returns "" once the camera is delivering, or the reason it is not.
      start: function (timeoutMs) {
        if (Atomics.load(hdr, STATE) === STATES.FAILED) {
          // Clear a previous failure so the page tries again rather than
          // answering with a stale one.
          Atomics.store(hdr, STATE, STATES.IDLE);
        }
        Atomics.store(hdr, CMD, 1);

        var deadline = Date.now() + timeoutMs;
        for (;;) {
          var state = Atomics.load(hdr, STATE);
          if (state === STATES.RUNNING) return "";
          if (state === STATES.FAILED) {
            return readString(sab, hdr, ERR_OFFSET, ERR_LEN) || "camera failed to start";
          }
          var left = deadline - Date.now();
          if (left <= 0) return "camera did not start in time";
          Atomics.wait(hdr, STATE, state, left);
        }
      },

      stop: function () {
        Atomics.store(hdr, CMD, 0);
        Atomics.store(hdr, QR_LEN, 0);
      },

      // Parks until the page publishes a frame the worker has not seen, so the
      // wallet's scan loop runs at the camera's pace instead of spinning.
      frame: function (timeoutMs) {
        var seq = Atomics.load(hdr, FRAME_SEQ);
        if (seq === lastFrame) {
          Atomics.wait(hdr, FRAME_SEQ, seq, timeoutMs);
          seq = Atomics.load(hdr, FRAME_SEQ);
        }
        if (seq === lastFrame) return null;
        lastFrame = seq;

        var width = Atomics.load(hdr, FRAME_W);
        var height = Atomics.load(hdr, FRAME_H);
        if (width <= 0 || height <= 0) return null;
        return {
          w: width,
          h: height,
          bytes: new Uint8Array(sab, FRAME_OFFSET, width * height * 3).slice(),
        };
      },

      // Claims the pending payload, if any. Clearing the length is what lets the
      // page publish the next one.
      payload: function () {
        var length = Atomics.load(hdr, QR_LEN);
        if (length <= 0) return null;
        var out = new Uint8Array(sab, QR_OFFSET, length).slice();
        Atomics.store(hdr, QR_LEN, 0);
        return out;
      },
    };
  }

  scope.CameraChannel = {
    createBuffer: function () { return new SharedArrayBuffer(TOTAL_BYTES); },
    runPage: runPage,
    forWorker: forWorker,
  };
})(typeof self !== "undefined" ? self : this);
