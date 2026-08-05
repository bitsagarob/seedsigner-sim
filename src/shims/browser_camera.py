"""
A SeedSigner camera whose frames and QR payloads both come from the browser.

SeedSigner reads QR codes with pyzbar, a binding to the zbar C library, and there
is no zbar in WASM. Porting it is not the answer, because the browser already has
a QR decoder of its own -- BarcodeDetector where it exists, jsQR everywhere else --
and it already owns the camera through getUserMedia. So the fake sits at the two
places where SeedSigner reaches for hardware, the video stream and the decode, and
everything above them runs unmodified: ScanScreen, DecodeQR's parsing of SeedQR,
CompactSeedQR, PSBT and UR payloads, and every view that consumes them.

The page, not this module, holds the camera. The worker thread is permanently
blocked inside the controller's main loop and can never service a postMessage, so
a SharedArrayBuffer is the only channel that works here -- the same reason the
buttons already use one. The page writes frames and decoded payloads into it; the
calls below read them out synchronously, which is what SeedSigner's blocking scan
loop expects.

Frames and payloads are deliberately not tied to each other. extract_qr_data()
ignores the image it is handed and reports whatever the page decoded most
recently, because the decode ran in JavaScript against the page's own copy of the
frame. The image that arrives here matters only for the preview.
"""

from PIL import Image

from seedsigner.hardware.camera import Camera, CameraConnectionError
from seedsigner.models.decode_qr import DecodeQR

# The bridge into the page, supplied by install(). Four calls: start(), stop(),
# frame(timeout_ms) and payload().
_js = None

# How long a frame read parks before giving up and letting the scan loop go round
# again. Long enough not to spin, short enough that stopping the camera is not
# noticeably delayed.
_FRAME_WAIT_MS = 2000

# Long enough to cover a getUserMedia permission prompt. The worker is parked for
# this whole time, which is harmless: the page's own thread is what draws the
# prompt and it stays free.
_START_WAIT_MS = 30000

_last_frame = None


def _to_bytes(js_array):
    """Copy a JS Uint8Array into Python bytes."""
    to_py = getattr(js_array, "to_py", None)
    return bytes(to_py()) if to_py is not None else bytes(js_array)


class _BrowserVideoStream:
    """
    Stands in for pivideostream.VideoStream.

    Nothing is read through it -- frames come over the SharedArrayBuffer -- but
    ScanScreen watches `camera._video_stream` to decide whether the camera is
    still live, so the attribute has to hold something and then become None.
    """

    def stop(self):
        pass


def _start_video_stream_mode(self, resolution=(512, 384), framerate=12, format="bgr"):
    """
    Ask the page for the camera and wait until it is actually delivering frames.

    `resolution`, `framerate` and `format` are the caller's preferences for a
    sensor this process does not own; getUserMedia negotiates its own, and the
    page downscales to a fixed preview size before publishing. Failing to open
    the camera raises CameraConnectionError, which is the same error the picamera
    backend raises and which SeedSigner already routes to CameraConnectionErrorView.
    """
    global _last_frame

    if self._video_stream is not None:
        self.stop_video_stream_mode()

    _last_frame = None
    error = _js.start(_START_WAIT_MS)
    if error:
        raise CameraConnectionError(str(error))

    self._video_stream = _BrowserVideoStream()


def _read_video_stream(self, as_image=False, preview=False, greyscale=True):
    """
    Return the most recent camera frame, parking until one arrives.

    Blocking here is what paces SeedSigner's scan loop; without it the loop would
    spin against a still image as fast as Python can run.

    `greyscale` is ignored. It exists on the real camera to save CPU on a Pi Zero
    by never building the colour frame, and the colour conversion has already
    happened in the browser by the time a frame reaches this process.
    """
    global _last_frame

    if self._video_stream is None:
        raise Exception("Must call start_video_stream_mode first.")

    # The preview pump asks for the frame the decode pass just read, so hand back
    # the cached one rather than parking for a second frame.
    if preview and _last_frame is not None:
        return _last_frame

    frame = _js.frame(_FRAME_WAIT_MS)
    if frame is None:
        return None

    image = Image.frombytes("RGB", (frame.w, frame.h), _to_bytes(frame.bytes))

    # The real camera rotates by 90 degrees to undo how the sensor is mounted in
    # the case, plus whatever the user set. A getUserMedia stream arrives upright.
    _last_frame = image
    return image


def _stop_video_stream_mode(self):
    global _last_frame

    if self._video_stream is not None:
        self._video_stream.stop()
        self._video_stream = None
    _last_frame = None
    _js.stop()


class _BrowserSingleFrame:
    """
    Stands in for the PiCamera object single-frame mode holds.

    Nothing is read through it, the same as the video stream above: the frame
    comes over the SharedArrayBuffer. But Camera uses `_picamera` to know
    whether the mode is open, and capture_frame's own guard reads it, so it has
    to hold something and then become None.
    """

    def close(self):
        pass


def _start_single_frame_mode(self, resolution=(720, 480)):
    """
    Open the camera for a still, rather than for a stream.

    This is the other half of the hardware camera and it was missing, which is
    why "new seed from a photo" died on `No module named 'picamera'`: stock
    reaches for the single-frame API there and for the video stream nowhere
    near it, so patching only the stream left the whole flow on the real
    picamera import. The fork does not use this API at all, which is why it
    went unnoticed for as long as the fork was the firmware people booted.

    `resolution` is the caller's preference for a sensor this process does not
    own, exactly as in the stream case: the page negotiates its own with
    getUserMedia and publishes at a fixed size.
    """
    if self._video_stream is not None:
        self.stop_video_stream_mode()
    if self._picamera is not None:
        self.stop_single_frame_mode()

    error = _js.start(_START_WAIT_MS)
    if error:
        raise CameraConnectionError(str(error))

    self._picamera = _BrowserSingleFrame()


def _capture_frame(self):
    """
    One frame, as a PIL image, from the stream the page is already publishing.

    The real one sets the exposure and white balance manually, takes a JPEG and
    rotates it by 90 degrees plus the user's setting. None of that applies: the
    browser owns the exposure, there is no JPEG in the middle, and a
    getUserMedia stream arrives upright, which is the same reason
    read_video_stream does not rotate either.

    It is the published preview frame, so it is the preview's size rather than
    the resolution asked for. What this image is used for is entropy -- it is
    hashed, and the sensor noise in it is the point -- and a quarter-megapixel
    of real camera output has far more of that than a seed needs. It is also
    what the visitor is shown, at a size the screen would have scaled it to
    anyway.
    """
    if self._picamera is None:
        raise Exception("Must call start_single_frame_mode first.")

    frame = _js.frame(_FRAME_WAIT_MS)
    if frame is None:
        raise CameraConnectionError("the camera did not deliver a frame")

    return Image.frombytes("RGB", (frame.w, frame.h), _to_bytes(frame.bytes))


def _stop_single_frame_mode(self):
    if self._picamera is not None:
        self._picamera.close()
        self._picamera = None
    _js.stop()


def _extract_qr_data(image, is_binary: bool = False):
    """
    Report the payload the page has decoded, or None.

    The image is ignored: this is the pyzbar call, and the decode it stands in for
    has already happened in JavaScript. Payloads are returned as bytes exactly as
    pyzbar returns them, so CompactSeedQR's raw entropy survives the trip
    unmangled and DecodeQR's own type detection is what decides what it is.

    Each payload is handed over once. The page will not publish another until this
    one has been claimed, so a QR held in front of the camera does not flood the
    decoder with repeats of itself.
    """
    payload = _js.payload()
    return None if payload is None else _to_bytes(payload)


def _pump_preview(screen):
    """
    Draw one frame of ScanScreen's live preview.

    The preview is normally a thread, and this port has no threads: the worker is
    single-threaded, so the shim that stands in for threading.Thread drops
    anything loop-shaped, and ScanScreen's LivePreviewThread is loop-shaped. That
    would leave the scan screen frozen on whatever was drawn before it.

    Rather than reimplement the preview -- it draws the progress bar for animated
    QRs, the frame-accepted indicator and the translated instructions -- let
    SeedSigner's own loop body run exactly one pass: keep_running answers True
    once and then False, so run() draws a single frame and returns.
    """
    threads = getattr(screen, "threads", None)
    if not threads:
        return

    preview = threads[0]
    passes = [True, False]
    type(preview).keep_running = property(lambda self: passes.pop(0) if passes else False)
    try:
        preview.run()
    finally:
        del type(preview).keep_running


def install(js_camera):
    """
    Point SeedSigner's camera and QR decode at the page.

    `js_camera` is the worker's bridge object. Patching the methods rather than
    the class keeps Camera's own singleton and its settings handling, which read
    camera rotation and device index and work fine as they are.
    """
    global _js
    _js = js_camera

    Camera.start_video_stream_mode = _start_video_stream_mode
    Camera.read_video_stream = _read_video_stream
    Camera.stop_video_stream_mode = _stop_video_stream_mode

    # The still half. Stock's image-entropy flow and its QR brightness setting
    # are the callers; the fork has neither, so this was missing for as long as
    # the fork was what booted.
    Camera.start_single_frame_mode = _start_single_frame_mode
    Camera.capture_frame = _capture_frame
    Camera.stop_single_frame_mode = _stop_single_frame_mode

    DecodeQR.extract_qr_data = staticmethod(_extract_qr_data)

    # Without this ScanView bails to "QR Scanner Unavailable" before it ever opens
    # the camera, because it probes for zbar and OpenCV and this build has neither.
    DecodeQR.is_qr_scanner_available = staticmethod(lambda: True)

    _install_preview_pump()


def _install_preview_pump():
    from seedsigner.gui.screens.scan_screens import ScanScreen

    original_run = ScanScreen._run

    def _run(self):
        # The decode loop is the only thing still running once scanning starts, so
        # it is the only place left to drive the preview from. Camera reads are
        # the loop's heartbeat: one frame read, one frame drawn.
        camera = self.camera
        original_read = camera.read_video_stream

        # The guard is what makes this safe on both firmwares. The fork's
        # LivePreviewThread asks for its frame with preview=True, so the flag
        # alone is enough to tell "the decode loop wants a frame" from "the
        # preview does". Stock's read_video_stream is (self, as_image=False)
        # with no preview keyword at all, so on stock the preview's own read
        # looks exactly like the decode loop's and would pump the preview from
        # inside the preview, for as deep as the recursion limit allows.
        drawing = {"active": False}

        def read_and_draw(*args, **kwargs):
            frame = original_read(*args, **kwargs)
            if frame is not None and not kwargs.get("preview") and not drawing["active"]:
                drawing["active"] = True
                try:
                    _pump_preview(self)
                finally:
                    drawing["active"] = False
            return frame

        camera.read_video_stream = read_and_draw
        try:
            return original_run(self)
        finally:
            del camera.read_video_stream

    ScanScreen._run = _run
