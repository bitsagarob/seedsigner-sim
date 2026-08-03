"""
Run the whole suite: one command, from a fresh clone.

    python3 test/run.py                 everything
    python3 test/run.py scan            only the tests whose name contains "scan"

It builds what is missing, generates the QR videos, starts the server, runs the
tests against it, and stops the server whether they passed or not.

Prerequisites, and nothing else:
  - Python 3.9+
  - pip install playwright && playwright install chromium
  - build/fetch-assets.sh and build/build-wallet-zip.sh able to run once, which
    needs network access. Their outputs are not committed: the Pyodide runtime is
    26MB of someone else's release, and wallet.zip is built from a pinned
    upstream SeedSigner commit so that what is tested is provably that commit.

Order is deliberate. The checks that need nothing run first and finish in
seconds, so a broken checkout says so before anything spends two minutes booting
CPython in WebAssembly.
"""

import filecmp
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness

PY = sys.executable

# (name, argv relative to test/, does it need the server)
SUITE = [
    ("leak_scan", ["leak_scan.py"], False),
    ("cards", ["test_cards.py"], False),
    ("tray_layout", ["test_tray_layout.py"], True),
    ("scan_seedqr", ["test_scan.py"], True),
    ("scan_compact", ["test_scan.py"], True),
    ("scan_native", ["test_scan_native.py"], True),
    ("cards_browser", ["test_cards_browser.py"], True),
    ("cards_seed", ["test_cards_seed.py"], True),
]

# The same file scanned twice, once per QR encoding. Both must reach the same
# seed, and the compact one is the encoding that breaks first if any layer
# decides a payload is text.
EXTRA_ENV = {
    "scan_seedqr": {"QR_KIND": "qr"},
    "scan_compact": {"QR_KIND": "qr-compact"},
}


def ensure_assets() -> bool:
    """wallet.zip and the Pyodide runtime, built on demand."""
    wanted = [
        ("wallet.zip", "build/build-wallet-zip.sh"),
        (os.path.join("pyodide", "pyodide.js"), "build/fetch-assets.sh"),
    ]
    for name, script in wanted:
        if harness.find_asset(name):
            continue
        path = os.path.join(harness.REPO, script)
        if not os.path.exists(path):
            print(f"missing {name}, and {script} is not there to build it", file=sys.stderr)
            return False
        print(f"--- {script} (for {name})", flush=True)
        try:
            code = subprocess.call([path], cwd=harness.REPO)
        except OSError as exc:
            # Almost always a lost executable bit or a noexec filesystem, which
            # is worth saying rather than showing a traceback about.
            print(f"cannot run {script}: {exc}", file=sys.stderr)
            return False
        if code != 0:
            print(f"{script} failed", file=sys.stderr)
            return False
        if not harness.find_asset(name):
            print(f"{script} ran but produced no {name}", file=sys.stderr)
            return False
    return True


def start_server():
    roots = [r for r in harness.WEB_ROOTS if os.path.isdir(r)]
    server = subprocess.Popen(
        [PY, os.path.join(HERE, "serve.py"), "--port", str(harness.PORT)] + roots)

    deadline = time.time() + 15
    while time.time() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"server exited immediately: is port {harness.PORT} taken?")
        try:
            with socket.create_connection(("127.0.0.1", harness.PORT), 0.5):
                return server
        except OSError:
            time.sleep(0.25)
    server.kill()
    raise SystemExit(f"server never came up on port {harness.PORT}")


# The three screens that must be the same screen. One seed, encoded three ways
# and read down two different decoder paths, so if the wallet is honest all three
# runs end on the same rendered fingerprint. Comparing the images turns a claim
# somebody had to check by eye into something CI can fail on.
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "baseline", "seed-b2269592.png")

SAME_SEED_SHOTS = ("scan-proof-qr.png", "scan-proof-qr-compact.png",
                   "scan-proof-native-compact.png")


def same_seed() -> int:
    print("\n=== same_seed " + "=" * 54, flush=True)
    paths = [os.path.join(harness.ARTIFACT_DIR, n) for n in SAME_SEED_SHOTS]
    missing = [n for n, p in zip(SAME_SEED_SHOTS, paths) if not os.path.exists(p)]
    if missing:
        print(f"  FAIL no screenshot from {missing}")
        return 1
    for other in paths[1:]:
        if not filecmp.cmp(paths[0], other, shallow=False):
            print(f"  FAIL {os.path.basename(other)} is a different screen from "
                  f"{os.path.basename(paths[0])}")
            return 1

    # Agreeing with each other is not enough. Three runs of a wallet that derived
    # the seed wrongly would agree perfectly and still be wrong, and the mnemonic
    # to seed path here runs on a substituted PBKDF2 (hashlib has no OpenSSL under
    # Pyodide), so "all three match" has to be anchored to a known answer. The
    # baseline is the BIP39 test vector "army van defense ...", whose master
    # fingerprint is b2269592.
    if not os.path.exists(BASELINE):
        print(f"  FAIL no baseline at {BASELINE}")
        return 1
    if not filecmp.cmp(paths[0], BASELINE, shallow=False):
        print("  FAIL the decoded seed does not match the known-good baseline "
              f"({os.path.basename(BASELINE)}); the wallet decoded or derived "
              "something other than the test vector")
        return 1
    print("  ok   all three encodings end on the same screen, and it is the "
          "expected seed (fingerprint b2269592)")
    return 0


def run(name, argv, env):
    print(f"\n=== {name} " + "=" * (60 - len(name)), flush=True)
    started = time.time()
    code = subprocess.call([PY, os.path.join(HERE, argv[0])] + argv[1:], env=env)
    print(f"--- {name}: {'pass' if code == 0 else 'FAIL'} in {time.time() - started:.0f}s",
          flush=True)
    return code


def main(argv) -> int:
    wanted = argv[1:]
    suite = [s for s in SUITE if not wanted or any(w in s[0] for w in wanted)]
    if not suite:
        print(f"nothing matches {wanted}; names are {[s[0] for s in SUITE]}", file=sys.stderr)
        return 2

    needs_browser = any(needs_server for _, _, needs_server in suite)
    if needs_browser and not ensure_assets():
        return 2

    env = dict(os.environ)
    env["SIM_ARTIFACT_DIR"] = harness.ARTIFACT_DIR
    env["SIM_PORT"] = str(harness.PORT)

    if needs_browser:
        print("--- generating the QR videos", flush=True)
        if subprocess.call([PY, os.path.join(HERE, "make_qr_y4m.py")], env=env) != 0:
            return 2

    server = start_server() if needs_browser else None
    results = {}
    try:
        for name, args, _ in suite:
            results[name] = run(name, args, {**env, **EXTRA_ENV.get(name, {})})
    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)
        # The videos are ~115MB and regenerate in seconds; the screenshots are
        # the part worth keeping. SIM_KEEP_VIDEOS=1 to leave them behind.
        if not os.environ.get("SIM_KEEP_VIDEOS"):
            for name in ("qr.y4m", "qr-compact.y4m", "qr-blank.y4m"):
                path = os.path.join(harness.ARTIFACT_DIR, name)
                if os.path.exists(path):
                    os.remove(path)

    # Only meaningful when every scan test ran and passed: comparing a fresh
    # screenshot against a stale one would prove nothing.
    scans = ("scan_seedqr", "scan_compact", "scan_native")
    if all(results.get(name) == 0 for name in scans):
        results["same_seed"] = same_seed()

    print("\n" + "=" * 68)
    for name, code in results.items():
        print(f"  {'pass' if code == 0 else 'FAIL'}  {name}")
    print(f"artifacts in {harness.ARTIFACT_DIR}")
    return 0 if all(code == 0 for code in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
