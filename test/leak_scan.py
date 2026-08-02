"""
Fail if a tracked file leaks the author's infrastructure.

This repo is published from a working machine that also runs other things. The
risk is not a stolen key -- it is the boring stuff: a hardcoded home directory
naming the account, a private address naming the subnet, an internal hostname
naming a box. None of it is dangerous on its own, and all of it is free
reconnaissance for anyone who wants to go looking.

A human can spot that once. A human cannot spot it on every commit forever, so
it is checked here instead, and it fails the build rather than warning.

What is deliberately NOT checked: public URLs. github.com/<the project> is meant
to be in this repo, and a scanner that bans the word "github" would be turned off
within a week. Everything below matches a shape that only ever appears by
accident -- a private address, somebody's home directory, a name that resolves
nowhere outside one LAN.

Run it directly (python3 test/leak_scan.py) or let run.py do it first.
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# --- what is allowed ---------------------------------------------------------
# Every entry here is a deliberate exception, and each one says why. Adding to
# this list is how you disagree with the scanner; editing the rules is not.

ALLOWED_ADDRESSES = {
    # The test server binds and the tests connect here. There is no way to run a
    # cross-origin-isolated page over a secure context without it.
    "127.0.0.1",
    "::1",
    # Bind-all, which appears in server usage text.
    "0.0.0.0",
    # The base addresses of the private ranges themselves. These name nobody's
    # network -- they are the RFC's own notation, and they have to be writable
    # here or this file could not describe what it is looking for.
    "10.0.0.0",
    "172.16.0.0",
    "192.168.0.0",
    "100.64.0.0",
    "169.254.0.0",
    "255.255.255.255",
}

ALLOWED_HOSTNAMES = {
    # Loopback by name, same reason as above.
    "localhost",
}

# Home directories that name nobody. /home/runner is GitHub's own runner account
# and turns up in CI logs and paths; the rest are placeholders people write in
# documentation on purpose.
ALLOWED_HOME_USERS = {"runner", "user", "username", "you", "youruser", "example"}

# Nothing under these is a tracked source file worth scanning: generated output,
# vendored dependencies, and the git directory itself.
#
# "pyodide" is the downloaded WebAssembly runtime. It is gitignored, hash-checked
# by build/fetch-assets.sh, and never published from here, so git never reports
# it -- this entry only matters to the directory-walk fallback below, which
# otherwise flags the home directory Emscripten hardcodes for its own virtual
# filesystem. That is a path inside a sandbox, not a path on anybody's machine.
SKIP_DIRECTORIES = {".git", "node_modules", "artifacts", "__pycache__", ".venv",
                    "pyodide"}

# Binary-ish files carry no infrastructure and produce noise if grepped.
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                 ".ttf", ".otf", ".zip", ".wasm", ".whl", ".y4m", ".pdf")

# Names that must not be committed but must not be written down here either --
# spelling them out in a public file would leak exactly what it is meant to stop.
# Set LEAK_SCAN_EXTRA to a "|"-separated regex in CI, from a repository secret.
EXTRA_PATTERN = os.environ.get("LEAK_SCAN_EXTRA", "").strip()


# --- rules -------------------------------------------------------------------

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Private and unroutable ranges, as (first octet, test) so no rule here has to
# be written as an address literal that would then trip the rule.
PRIVATE_RANGES = (
    ("RFC1918 10/8", lambda o: o[0] == 10),
    ("RFC1918 172.16/12", lambda o: o[0] == 172 and 16 <= o[1] <= 31),
    ("RFC1918 192.168/16", lambda o: o[0] == 192 and o[1] == 168),
    ("CGNAT / tailnet 100.64/10", lambda o: o[0] == 100 and 64 <= o[1] <= 127),
    ("link-local 169.254/16", lambda o: o[0] == 169 and o[1] == 254),
)

# An absolute path into somebody's account. The user name is the leak.
HOME_PATH = re.compile(r"/(?:home|Users)/([A-Za-z_][A-Za-z0-9_.-]*)")

# Hostnames that only resolve on a private network.
PRIVATE_TLD = re.compile(
    r"\b[a-z0-9][a-z0-9-]*\.(?:local|internal|intranet|lan|localdomain|corp)\b"
    r"|\b[a-z0-9][a-z0-9-]*\.home\.arpa\b"
    r"|\b[a-z0-9][a-z0-9-]*\.ts\.net\b", re.IGNORECASE)

# A URL whose host has no dot in it is a name from one machine's /etc/hosts or
# one LAN's DNS -- it cannot mean anything to a reader outside it.
BARE_HOST_URL = re.compile(r"\b(?:https?|ssh|ftp|rsync)://([A-Za-z0-9][A-Za-z0-9-]*)(?=[:/\s\"'>]|$)")


def private_address(text):
    """The range a literal falls in, or None if it is public or allowed."""
    if text in ALLOWED_ADDRESSES:
        return None
    octets = [int(part) for part in text.split(".")]
    if any(o > 255 for o in octets):
        return None  # a version number, not an address
    for label, matches in PRIVATE_RANGES:
        if matches(octets):
            return label
    return None


def findings(line):
    """Every leak in one line, as (rule, matched text)."""
    for found in IPV4.finditer(line):
        label = private_address(found.group(0))
        if label:
            yield f"private address ({label})", found.group(0)

    for found in HOME_PATH.finditer(line):
        if found.group(1) not in ALLOWED_HOME_USERS:
            yield "absolute home directory", found.group(0)

    for found in PRIVATE_TLD.finditer(line):
        yield "private hostname", found.group(0)

    for found in BARE_HOST_URL.finditer(line):
        if found.group(1).lower() not in ALLOWED_HOSTNAMES:
            yield "URL to a single-label host", found.group(0)

    if EXTRA_PATTERN:
        for found in re.finditer(EXTRA_PATTERN, line, re.IGNORECASE):
            yield "site-specific denylist", found.group(0)


def tracked_files(root):
    """What git is publishing. Falls back to a walk outside a checkout, so this
    still works on an unpacked tarball."""
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                             capture_output=True, check=True).stdout
        names = [n for n in out.decode().split("\0") if n]
        if names:
            return names
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    names = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRECTORIES]
        for name in filenames:
            names.append(os.path.relpath(os.path.join(dirpath, name), root))
    return names


def scannable(root, name):
    if any(part in SKIP_DIRECTORIES for part in name.split(os.sep)):
        return False
    if name.lower().endswith(SKIP_SUFFIXES):
        return False
    path = os.path.join(root, name)
    if not os.path.isfile(path) or os.path.getsize(path) > 4 * 1024 * 1024:
        return False
    with open(path, "rb") as handle:
        return b"\0" not in handle.read(8192)


def main(argv) -> int:
    root = os.path.abspath(argv[1]) if len(argv) > 1 else REPO
    names = sorted(n for n in tracked_files(root) if scannable(root, n))

    leaks = []
    for name in names:
        with open(os.path.join(root, name), encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, 1):
                for rule, text in findings(line):
                    leaks.append((name, number, rule, text.strip()))

    print(f"leak scan: {len(names)} files under {root}")
    if EXTRA_PATTERN:
        print("           plus a site-specific denylist from LEAK_SCAN_EXTRA")

    if not leaks:
        print("clean")
        return 0

    print(f"\n{len(leaks)} leak(s):")
    for name, number, rule, text in leaks:
        print(f"  {name}:{number}: {rule}: {text}")
    print("\nIf one of these is deliberate, add it to the allowlist at the top of "
          "test/leak_scan.py with a comment saying why.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
