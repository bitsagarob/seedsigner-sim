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

  function runPage(sab) {
    var hdr = header(sab);
    var stream = null;
    var video = null;
    var capture = null;
    var preview = null;
    var decoder = null;

    function setState(state) {
      Atomics.store(hdr, STATE, state);
      Atomics.notify(hdr, STATE);
    }

    function fail(error) {
      writeString(sab, hdr, ERR_OFFSET, ERR_MAX, ERR_LEN, (error && error.message) || error);
      shutdown(STATES.FAILED);
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

    function start() {
      setState(STATES.STARTING);
      if (!scope.navigator || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        // getUserMedia is missing rather than merely refused when the page is not
        // a secure context, which over plain http on a LAN address it is not.
        fail(new Error("no camera API here; needs https or localhost"));
        return Promise.resolve();
      }

      return navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: "environment",
          width: { ideal: CAPTURE_W },
          height: { ideal: CAPTURE_H },
        },
        audio: false,
      }).then(function (opened) {
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
