// Runs the SeedSigner wallet in a Web Worker.
//
// The wallet blocks the CPU waiting for a button press, which would freeze the
// page if it ran on the main thread. In a worker that is fine: the page stays
// responsive, and input is handed over through a SharedArrayBuffer so the
// worker's blocking loop can be woken without any change to SeedSigner itself.

importScripts("pyodide/pyodide.js", "wallet-camera.js", "wallet-cards.js");

let pyodide = null;
let keyBuffer = null; // Int32Array over SharedArrayBuffer: [state, keycode]
let camera = null;    // the page's half of the camera channel, see wallet-camera.js
let cards = null;     // the page's card tray, see wallet-cards.js
let debug = false;    // ?debug=1 on the page; otherwise js_log says nothing

// Which of the two built wallet zips to unpack. The page decides; see
// FIRMWARES in wallet.html for what the names mean and how one is chosen.
let firmware = "smartcard";

const STATE = 0;
const KEYCODE = 1;

function post(type, payload) {
  self.postMessage({ type, ...payload });
}

function hex(buffer) {
  return Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, "0")).join("");
}

self.onmessage = async (event) => {
  const { type } = event.data;

  if (type === "init") {
    keyBuffer = new Int32Array(event.data.sharedBuffer);
    camera = CameraChannel.forWorker(event.data.cameraBuffer);
    // A page with no tray is a page whose reader keeps the card it starts
    // with, which is how this worked before the tray existed.
    cards = event.data.cardBuffer ? CardTray.forWorker(event.data.cardBuffer) : null;
    debug = !!event.data.debug;
    if (event.data.firmware) firmware = event.data.firmware;
    try {
      await boot(event.data.width, event.data.height);
    } catch (error) {
      post("error", { message: String(error && error.message ? error.message : error) });
    }
  }
};

async function boot(width, height) {
  post("status", { message: "loading python…" });
  pyodide = await loadPyodide({ indexURL: "pyodide/" });

  post("status", { message: "loading libraries…" });
  // Pillow and pycryptodome are wanted whichever firmware this is: the renderer
  // draws every screen with Pillow, and pycryptodome is what stands in for
  // pbkdf2_hmac further down, which is the mnemonic to seed path and so is the
  // wallet itself. Dropping that one boots a stock wallet that hangs before the
  // home screen, which is how it was caught.
  //
  // cryptography is the fork's alone. Twelve files in the smartcard tree import
  // it, for pysatochip's card sessions; nothing in stock does, and neither do
  // the stand-in packages here. Loading it regardless charged every stock visit
  // for it, plus the openssl and cffi Pyodide pulls in behind it.
  //
  // numpy is never reachable: decode_qr imports it inside a try that starts with
  // "import cv2", and opencv is not in this list, so np is None either way.
  await pyodide.loadPackage(firmware === "smartcard"
    ? ["Pillow", "pycryptodome", "cryptography"]
    : ["Pillow", "pycryptodome"]);

  post("status", { message: "unpacking wallet…" });
  // One zip per firmware, each built by build/build-wallet-zip.sh from its own
  // section of UPSTREAM and published with its own pair of hashes.
  const zip = await (await fetch(`wallet-${firmware}.zip`)).arrayBuffer();

  // Hash what arrived, before unpacking it, and hand it to the page: the panel
  // shows it beside the sha256 UPSTREAM publishes. It has to be these bytes,
  // the ones this worker is about to unpack and run, because a hash taken from
  // anywhere else -- build-info.json most of all -- would only be one claim
  // repeating another.
  const digest = await crypto.subtle.digest("SHA-256", zip);
  post("zip-sha256", { sha256: hex(digest) });

  await pyodide.unpackArchive(zip, "zip", { extractDir: "/wallet" });

  const driver = await (await fetch("browser_display.py")).text();
  pyodide.FS.writeFile("/wallet/browser_display.py", driver);

  const cameraShim = await (await fetch("browser_camera.py")).text();
  pyodide.FS.writeFile("/wallet/browser_camera.py", cameraShim);

  const qrShim = await (await fetch("browser_qr.py")).text();
  pyodide.FS.writeFile("/wallet/browser_qr.py", qrShim);

  post("status", { message: "starting wallet…" });

  // Frames come back through this callback rather than being polled.
  pyodide.globals.set("js_frame_sink", (bytes) => {
    // Pyodide may hand this over as a proxy or already as a typed array.
    const raw = bytes && typeof bytes.toJs === "function" ? bytes.toJs() : bytes;
    const copy = new Uint8Array(raw);
    if (bytes && typeof bytes.destroy === "function") bytes.destroy();
    self.postMessage({ type: "frame", frame: copy }, [copy.buffer]);
  });

  // Dropped here rather than on the page so the messages are not even built
  // and posted when nobody is reading them.
  pyodide.globals.set("js_log", (msg) => {
    if (debug) self.postMessage({ type: "log", message: String(msg) });
  });

  pyodide.globals.set("js_report_size", (w, h) => {
    self.postMessage({ type: "size", width: w, height: h });
  });

  // What the wallet's settings say the Bitcoin network is. Unlike js_log this
  // is not behind the debug flag: the page shows it to everyone, and it matters
  // most to the visitor who never turns tracing on.
  pyodide.globals.set("js_network", (name, mainnet) => {
    self.postMessage({ type: "network", name: String(name), mainnet: !!mainnet });
  });

  // Blocking read of the next keypress, driven by the page.
  pyodide.globals.set("js_wait_for_key", () => {
    Atomics.wait(keyBuffer, STATE, 0);
    const key = Atomics.load(keyBuffer, KEYCODE);
    Atomics.store(keyBuffer, STATE, 0);
    return key;
  });

  // Same channel, without the parking. The scan screen polls for a press rather
  // than blocking on one, because it has camera frames to pull at the same time.
  pyodide.globals.set("js_peek_key", () => {
    if (Atomics.load(keyBuffer, STATE) === 0) return 0;
    const key = Atomics.load(keyBuffer, KEYCODE);
    Atomics.store(keyBuffer, STATE, 0);
    return key;
  });

  pyodide.globals.set("js_camera", camera);
  pyodide.globals.set("js_cards", cards);

  pyodide.runPython(shims(width, height));
  post("ready", {});

  // Blocks for the lifetime of the worker. This is the whole reason the wallet
  // runs here rather than on the page's thread.
  try {
    post("log", { message: "starting controller…" });
    pyodide.runPython(`
import traceback
from seedsigner.controller import Controller
js_log("controller imported")
try:
    Controller.get_instance().start()
    js_log("controller.start() returned")
except BaseException:
    js_log("controller raised:\\n" + traceback.format_exc()[-1200:])
`);
  } catch (error) {
    post("log", { message: "worker-level failure: " + error });
  }
}

function shims(width, height) {
  return `
import sys, json, importlib, importlib.abc, importlib.util, threading
sys.path.insert(0, "/wallet")

# The device's own settings file, written before the wallet reads it. Settings
# loads settings.json from the working directory when it is not running on
# SeedSigner OS, so both of these are configuration, the way a configured device
# would have them, and nothing under seedsigner/ is touched to get them.
#
#   display_config  the SeedSigner Plus panel, which is the screen drawn here.
#   network         Testnet, where SeedSigner's own default is Mainnet. Nothing
#                   in a browser tab should be pointed at real coins, and the
#                   rest of the page spends its time saying so, so mainnet is
#                   not where a visitor should land without having asked. It is
#                   still in Settings > Advanced > Bitcoin network and still
#                   selectable there, exactly as on hardware: what changes is
#                   where this starts, not what it can reach.
#
# Both keys and both values are SettingsConstants, and the same ones in either
# firmware: SETTING__DISPLAY_CONFIGURATION, SETTING__NETWORK and TESTNET.
import os, json
os.chdir("/wallet")
with open("/wallet/settings.json", "w") as handle:
    json.dump({"display_config": "st7789_320x240", "network": "T"}, handle)

# --- no real threads in the browser -----------------------------------------
class _NoThread:
    """
    Stand-in for threading.Thread.

    Two kinds of thread exist in this codebase. SeedSigner's own BaseThread
    subclasses loop on keep_running to animate something, and running one
    synchronously would never return, so those are dropped. Everything else is
    a one-shot helper (startup preloading, for instance) whose work the caller
    may well be waiting on, so those run inline on start().
    """

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, daemon=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}
        self.name, self.daemon = name or "nothread", daemon
        self._done = False

    # The controller blocks waiting for BackgroundImportThread to set up storage,
    # and its run() is a one-shot rather than a loop, so it has to run even
    # though it is a BaseThread. Without it the wallet hangs forever after the
    # splash.
    #
    # The address verification threads are the other kind of exception. They look
    # like animation loops -- a while over keep_running -- but they are a search
    # that ends: they walk the derivation path looking for one address and stop
    # when they find it. Dropped, nothing ever searched, so Verify Address sat
    # showing an index that never moved and its Skip 10 incremented a counter no
    # thread was reading. Run, they answer at once for an address that really is
    # the wallet's, which is the case worth having work.
    RUN_INLINE_ANYWAY = {
        "BackgroundImportThread",
        "BruteForceAddressVerificationThread",
        "ExpandedBruteForceAddressVerificationThread",
    }

    # How far one of those searches may walk before this gives up on it. Upstream
    # has no bound on the not-found case because on hardware it is a real thread
    # somebody can cancel; here it would be the whole worker, wedged. A wallet
    # that has just exported its own key is being asked about its own first
    # address, so this only has to be deep enough to be honest about a miss.
    INLINE_SEARCH_LIMIT = 100

    def _is_animation_loop(self):
        if type(self).__name__ in self.RUN_INLINE_ANYWAY:
            return False
        return hasattr(self, "keep_running")

    def _bound_search(self):
        """Stop a search thread walking for ever, since nothing else can."""
        counter = getattr(self, "threadsafe_counter", None)
        if counter is None or not hasattr(counter, "increment"):
            return
        increment = counter.increment
        thread = self

        def bounded(step=1):
            increment(step)
            if counter.cur_count >= _NoThread.INLINE_SEARCH_LIMIT:
                js_log(f"inline search {type(thread).__name__} gave up at "
                       f"{counter.cur_count}")
                thread.keep_running = False

        counter.increment = bounded

    def start(self):
        js_log(f"thread start: {type(self).__name__} "
               f"loop={self._is_animation_loop()} target={getattr(self._target, '__name__', None)}")
        if self._is_animation_loop() or self._done:
            return
        self._done = True
        if type(self).__name__ in _NoThread.RUN_INLINE_ANYWAY and hasattr(self, "keep_running"):
            self._bound_search()
        try:
            self.run()
        except Exception as exc:
            js_log(f"inline thread {self.name} failed: {type(exc).__name__}: {exc}")

    def run(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def stop(self): pass
    def join(self, timeout=None): pass
    def is_alive(self): return False

threading.Thread = _NoThread


class _NoTimer:
    """
    Stand-in for threading.Timer.

    Settings.save() debounces its write behind a Timer, and it is the only
    Timer in the wallet. With no timers the write never happens and changing
    any setting, the network among them, raises on the settings screen. There
    is nothing to debounce in here, so the callback runs inline on start().
    """

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval, self._function = interval, function
        self._args, self._kwargs = args or (), kwargs or {}
        self.name, self.daemon = "notimer", None

    def start(self):
        js_log(f"timer inline: {getattr(self._function, '__name__', None)}")
        try:
            self._function(*self._args, **self._kwargs)
        except Exception as exc:
            js_log(f"inline timer failed: {type(exc).__name__}: {exc}")

    def cancel(self): pass
    def join(self, timeout=None): pass
    def is_alive(self): return False

threading.Timer = _NoTimer

# A lock that cannot deadlock, because there is nobody here to deadlock with.
#
# Running a Timer's callback inline on start() runs it inside whatever the
# scheduler was holding when it scheduled it, and Settings.save() schedules its
# write while holding _save_lock, which the write then takes again. On a device
# those are two threads and the second one waits a moment for the first. Here
# they are one thread, and a plain Lock waits for itself forever: the wallet
# accepted the settings change, stored it, and then never drew another frame.
#
# There is one thread in this environment, so the only acquire that can ever
# block is a thread blocking on itself, which is a deadlock rather than
# contention. A reentrant lock turns exactly that case into a pass and leaves
# every other use of a lock as it was.
threading.Lock = threading.RLock

# --- pycryptodomex is pycryptodome under another name ------------------------
class _CryptodomeAlias(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "Cryptodome" and not fullname.startswith("Cryptodome."):
            return None
        real = "Crypto" + fullname[len("Cryptodome"):]
        module = importlib.import_module(real)
        sys.modules[fullname] = module
        return importlib.util.find_spec(real)

sys.meta_path.insert(0, _CryptodomeAlias())

# --- hashlib here has no OpenSSL behind it -----------------------------------
# pbkdf2_hmac is not implemented in Python: it lives in _hashlib, the OpenSSL
# binding, which this build does not have. Every other hash embit wants is pure
# Python and survives, so this is the one hole, and it is directly in the path
# from a mnemonic to seed bytes -- without it loading any seed at all ends in
# InvalidSeedException. pycryptodome is already loaded and its PBKDF2 is the
# real one, so borrow that rather than hand-rolling the derivation.
import hashlib

if not hasattr(hashlib, "pbkdf2_hmac"):
    from Crypto.Hash import SHA256 as _SHA256, SHA512 as _SHA512
    from Crypto.Protocol.KDF import PBKDF2 as _PBKDF2

    _PRF = {"sha256": _SHA256, "sha512": _SHA512}

    def _pbkdf2_hmac(hash_name, password, salt, iterations, dklen=None):
        module = _PRF.get(hash_name)
        if module is None:
            raise ValueError(f"pbkdf2_hmac: no shim for {hash_name}")
        return _PBKDF2(password, salt, dkLen=dklen or module.digest_size,
                       count=iterations, hmac_hash_module=module)

    hashlib.pbkdf2_hmac = _pbkdf2_hmac

# --- nothing here can start a process ----------------------------------------
# Several helpers shell out to a faster native tool and fall back to pure Python
# when the binary is not installed; qr.py does it with qrencode. Emscripten
# raises OSError for that rather than FileNotFoundError, which those fallbacks
# do not catch, so exporting a QR ended in a System Error instead of a QR.
# Reporting the binary as absent is both true here and the case they already
# know how to handle.
#
# call() reports failure by returning non-zero rather than by raising, because
# the two firmwares disagree about which one they can survive. The fork's qr.py
# wraps the qrencode call in try/except FileNotFoundError and also checks the
# return code; stock's has no try/except at all and only checks the code, so a
# raise there escapes and every screen that draws a QR ends in a visible System
# Error. A non-zero return satisfies both, and "the binary ran and failed" is no
# less true here than "the binary is not installed".
import subprocess

def _no_such_binary(*args, **kwargs):
    raise FileNotFoundError("no processes in the browser")

def _failed_call(*args, **kwargs):
    return 1

subprocess.call = _failed_call
for _name in ("run", "check_call", "check_output", "Popen"):
    setattr(subprocess, _name, _no_such_binary)

# --- draw to the page instead of a panel -------------------------------------
import browser_display

_seen = {"n": 0}
_orig_show = browser_display.BrowserDisplay.show_image
def _traced_show(self, image, x_start=0, y_start=0):
    _seen["n"] += 1
    if _seen["n"] <= 3:
        js_log(f"show_image #{_seen['n']}: mode={image.mode} size={image.size} "
               f"driver={self.width}x{self.height}")
    return _orig_show(self, image, x_start, y_start)
browser_display.BrowserDisplay.show_image = _traced_show

browser_display.install(js_frame_sink, ${width}, ${height})

from seedsigner.gui.renderer import Renderer
from seedsigner.hardware.buttons import HardwareButtons, HardwareButtonsConstants

Renderer.configure_instance()
renderer = Renderer.get_instance()

# --- buttons come from the page, not from GPIO -------------------------------
# The wallet blocks here waiting for a press. In a worker that is exactly what
# we want: js_wait_for_key parks on Atomics.wait until the page posts a key.
def _get_instance(cls):
    if cls._instance is None:
        instance = cls.__new__(cls)
        instance.override_ind = False
        instance.cur_input = None
        instance.cur_input_started = None
        instance.last_input_time = 0
        instance.first_repeat_threshold = 225
        instance.next_repeat_threshold = 250
        cls._instance = instance
    return cls._instance

# This fork identifies buttons by name ("KEY_UP"), older ones by GPIO number.
# Resolving through the constants class works for either.
BUTTON_NAMES = [None, "KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT",
                "KEY_PRESS", "KEY1", "KEY2", "KEY3"]
BUTTON_VALUES = [None] + [getattr(HardwareButtonsConstants, n) for n in BUTTON_NAMES[1:]]

def _wait_for(self, keys=[]):
    js_log(f'wait_for keys={keys!r}')
    while True:
        index = js_wait_for_key()
        if index < 1 or index >= len(BUTTON_VALUES):
            continue
        value = BUTTON_VALUES[index]
        js_log(f'key index={index} -> {value!r} accepted={not keys or value in keys}')
        if not keys or value in keys:
            self.last_input_time = 0
            return value

def _update_last_input_time(self):
    self.last_input_time = 0

# The scan screen is the one caller that polls for a press instead of blocking on
# one, because it has camera frames to pull at the same time. Without this it
# could never see the press that backs out of scanning, and the only way out of
# the scan loop would be a successful decode.
#
# A press has to stay claimable long enough for every check in one pass of the
# caller's loop to see it, since the scan loop asks about KEY_RIGHT before
# KEY_LEFT. It must not stay forever, or a key nobody wants sits here hiding the
# press behind it.
_PENDING_KEYS = []  # [value, times offered]
_MAX_OFFERS = 4

def _check_for_low(self, key=None, keys=None):
    index = js_peek_key()
    if 1 <= index < len(BUTTON_VALUES):
        _PENDING_KEYS.append([BUTTON_VALUES[index], 0])

    wanted = list(keys) if keys else ([key] if key is not None else [])
    for entry in _PENDING_KEYS:
        entry[1] += 1
        if not wanted or entry[0] in wanted:
            _PENDING_KEYS.remove(entry)
            self.last_input_time = 0
            return True

    _PENDING_KEYS[:] = [e for e in _PENDING_KEYS if e[1] < _MAX_OFFERS]
    return False

HardwareButtons.get_instance = classmethod(_get_instance)
HardwareButtons.wait_for = _wait_for
HardwareButtons.update_last_input_time = _update_last_input_time
def _poll_button():
    index = js_peek_key()
    if 1 <= index < len(BUTTON_VALUES):
        _PENDING_KEYS.append([BUTTON_VALUES[index], 0])
    return _PENDING_KEYS.pop(0)[0] if _PENDING_KEYS else None

HardwareButtons.check_for_low = _check_for_low
HardwareButtons.has_any_input = lambda self: False
HardwareButtons.trigger_override = lambda self, force_release=False: None

# --- the camera, and the QR decode, both come from the page -------------------
import browser_camera
browser_camera.install(js_camera)

# --- the screens that show a QR draw from a thread this port cannot run ------
import browser_qr
browser_qr.install(_poll_button)

# --- which smartcard is in the reader is the page's to say --------------------
# The pyscard stand-in ships with the wallet, so unlike the camera there is
# nothing to install here beyond handing it the tray. Left alone it keeps a card
# of its own, and the reader is never empty.
#
# Smartcard firmware only. Stock SeedSigner has no card code, so its wallet zip
# carries no smartcard package to import: a zip whose claim is "the pin, its
# pinned dependencies and this repository's stand-ins, and nothing else" should
# not be padded with a package that firmware can never reach. The flag says which
# firmware this is rather than catching ImportError, because a missing module
# here would then be indistinguishable from a broken build.
if js_cards is not None and ${firmware === "smartcard" ? "True" : "False"}:
    from smartcard import simulated_card
    simulated_card.install(js_cards, js_log)

js_report_size(renderer.canvas_width, renderer.canvas_height)

# --- trace the screen lifecycle so a stall is locatable ----------------------
from seedsigner.gui.screens.screen import BaseScreen
_orig_display = BaseScreen.display
_orig_run = BaseScreen._run

def _traced_display(self):
    js_log(f"display() enter: {type(self).__name__}")
    try:
        result = _orig_display(self)
        js_log(f"display() exit: {type(self).__name__} -> {result!r}")
        return result
    except BaseException as exc:
        js_log(f"display() RAISED in {type(self).__name__}: {type(exc).__name__}: {exc}")
        raise

def _traced_run(self):
    js_log(f"_run() enter: {type(self).__name__}")
    return _orig_run(self)

BaseScreen.display = _traced_display
BaseScreen._run = _traced_run

# Views can stall before they ever construct a Screen, so trace one level up.
#
# Destination.run, and not View.run, which is what this patched for a long time
# and traced nothing at all. View.run is abstract -- its body raises "Must
# implement in the child class" -- and all of upstream's views override it, so
# patching the base class rebound an attribute no call ever looked up and a
# whole boot produced zero lines. Destination.run is the funnel the controller
# drives every transition through, and it wraps instantiation as well as the
# run, so a view that hangs in __init__ before it has a Screen still names
# itself here, which was the point of tracing one level up.
#
# The name comes from the Destination rather than from the instance because the
# instance does not exist yet when the enter line is written -- and that is
# exactly the failure this is here to locate.
from seedsigner.views.view import Destination
_orig_dest_run = Destination.run
def _traced_dest_run(self):
    name = self.View_cls.__name__ if self.View_cls is not None else "None"
    js_log(f"View.run enter: {name}")
    try:
        out = _orig_dest_run(self)
        js_log(f"View.run exit: {name}")
        return out
    except BaseException as exc:
        js_log(f"View.run RAISED {name}: {type(exc).__name__}: {exc}")
        raise
Destination.run = _traced_dest_run

# The controller consults these right after the splash. Only the fork has the
# helper: stock has no seedsigner.helpers.seedsigner_os at all, so this is
# tracing that simply has nothing to trace there.
try:
    from seedsigner.helpers import seedsigner_os as _ss_os
except ImportError:
    _ss_os = None

if _ss_os is not None:
    _orig_devbuild = _ss_os.is_seedsigner_os_dev_build
    def _traced_devbuild():
        js_log("is_seedsigner_os_dev_build() called")
        result = _orig_devbuild()
        js_log(f"is_seedsigner_os_dev_build() -> {result}")
        return result
    _ss_os.is_seedsigner_os_dev_build = _traced_devbuild

    import seedsigner.controller as _ctrl
    _ctrl.is_seedsigner_os_dev_build = _traced_devbuild

# --- one upstream bug, put back from outside ---------------------------------
# ShieldSigner B11 -- the tag UPSTREAM pins, and the tag the official
# pi0-smartcard image is built from -- calls _format_word_password() in
# password_generator_views.py without importing it. The name is defined in
# tools_views.py and never brought across, so every word-based password ends in
# a System Error naming line 893 instead of a password. It is nothing to do with
# the dice it was first reported from: EFF short, EFF long and BIP39 all reach
# the same line, whatever the entropy came from. Real B11 hardware has it too.
#
# From outside, and only where the name is missing. The wallet zip stays the
# pinned tree byte for byte -- that is the claim this repository exists to let
# anyone check -- so this is replaced the way every other seam here is, from
# this side of the boundary rather than by editing the tree. Upstream's own
# master fixes it with exactly this import, so the guard turns this into a
# no-op the day UPSTREAM can move to a tag that carries the fix. There is no
# such tag yet: B11 is the newest one published.
#
# tools_views first, and that order is load bearing. The two modules import each
# other: password_generator_views pulls its shared helpers from tools_views at
# the top, and tools_views pulls the password views back in with a star import
# on its last line, which works only because tools_views is the one that gets
# imported first and so is fully defined by the time the star runs. Importing
# password_generator_views first inverts that -- tools_views ends up starring in
# a module that is still executing its own import block -- and the Tools menu
# then dies on "name 'ToolsPasswordGeneratorTypeView' is not defined" before it
# can reach the bug below. Doing it in the order the wallet itself does costs
# nothing and stays out of that.
#
# Broad except on purpose. These are imported lazily by the menu that needs
# them, so a module that fails to import here would take the whole wallet down
# with it, where today it only spoils the one menu.
try:
    from seedsigner.views import tools_views  # imported first, for the order above
    from seedsigner.views import password_generator_views as _pgv
except ImportError:
    _pgv = None   # stock has no password generator at all
except Exception as exc:
    _pgv = None
    js_log(f"password_generator_views did not import: {type(exc).__name__}: {exc}")

if _pgv is not None and not hasattr(_pgv, "_format_word_password"):
    from seedsigner.views.tools_views import _format_word_password as _fwp
    _pgv._format_word_password = _fwp
    js_log("patched in password_generator_views._format_word_password")

# --- which Bitcoin network the wallet is set to ------------------------------
# The page has to show this, and the page must not be the one that knows it: a
# second copy of a setting is a copy that can disagree with the wallet, and it
# would disagree exactly when someone had just changed the setting. So the value
# is read back out of Settings with upstream's own accessors, at the two moments
# it can be new: once here, before the controller starts, and again after every
# write the wallet makes. set_value is that write, for every settings screen in
# both firmwares, so wrapping it observes the change rather than predicting it.
from seedsigner.models.settings import Settings
from seedsigner.models.settings_definition import SettingsConstants

def _report_network():
    try:
        settings = Settings.get_instance()
        value = settings.get_value(SettingsConstants.SETTING__NETWORK)
        name = settings.get_value_display_name(SettingsConstants.SETTING__NETWORK)
        js_network(str(name), value == SettingsConstants.MAINNET)
    except Exception as exc:
        js_log(f"network report failed: {type(exc).__name__}: {exc}")

_orig_set_value = Settings.set_value
def _traced_set_value(self, *args, **kwargs):
    result = _orig_set_value(self, *args, **kwargs)
    _report_network()
    return result
Settings.set_value = _traced_set_value

_report_network()

import time as _time
_orig_sleep = _time.sleep
def _traced_sleep(seconds):
    if seconds >= 0.5:
        js_log(f"sleep({seconds})")
    return _orig_sleep(seconds)
_time.sleep = _traced_sleep
`;
}
