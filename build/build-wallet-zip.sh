#!/usr/bin/env bash
#
# Build a wallet zip: the Python tree the simulator unpacks into the Pyodide
# filesystem at runtime.
#
# There are two firmwares and therefore two zips, and the firmware is the one
# required argument:
#
#   ./build/build-wallet-zip.sh smartcard    ->  wallet-smartcard.zip
#   ./build/build-wallet-zip.sh stock        ->  wallet-stock.zip
#
# No default, deliberately. They are equally first-class, they pin different
# repositories, and a script that guessed would eventually hand somebody the
# other one's hash to compare against.
#
# The point of this script is that you do not have to trust the zip that is
# being served to you. Run it, and compare its sha256 to the one you downloaded.
# If they match, the served zip is exactly the pinned upstream SeedSigner tree
# plus the pinned pure-Python dependencies plus this repository's own stand-in
# packages for the hardware it cannot have, and nothing else.
#
#   ./build/build-wallet-zip.sh smartcard
#   sha256sum some-downloaded-wallet-smartcard.zip
#
# For that comparison to mean anything the build has to be reproducible, so:
#
#   * every input is content-addressed -- upstream and the git-pinned
#     dependencies by commit sha, the PyPI dependencies by artifact sha256;
#   * the zip is written by hand rather than by the zip(1) command, with fixed
#     timestamps (SOURCE_DATE_EPOCH), fixed permissions, fixed entry order, and
#     no __pycache__ or .pyc anywhere;
#   * nothing about the build host leaks in: no paths, no user, no umask, no
#     timezone, no locale.
#
# Two hashes are printed. The first is the sha256 of the zip file, which is what
# you compare against a download. The second is the sha256 of a manifest of
# (sha256, path) over the zip's *contents*, which is independent of how well
# zlib happened to compress. If the zip hashes differ but the manifest hashes
# match, the two builds contain identical files and you are looking at a
# compressor difference, not a supply-chain difference.
#
# Requires: bash, git, curl, python3, and sha256sum (or shasum).

set -euo pipefail

# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

OUT_DIR="${REPO_ROOT}/build/out"
CACHE_DIR="${WALLET_BUILD_CACHE:-${XDG_CACHE_HOME:-${HOME}/.cache}/seedsigner-sim-build}"
KEEP_STAGING="no"

usage() {
    cat <<'USAGE'
Usage: build-wallet-zip.sh FIRMWARE [options]

  FIRMWARE         Which wallet to build, and which section of UPSTREAM to read
                   its pin from. One of:
                     smartcard   the 3rdIteration fork, with SeedKeeper and
                                 Satochip support   ->  wallet-smartcard.zip
                     stock       SeedSigner as its own project publishes it
                                                    ->  wallet-stock.zip

  --out DIR        Write the zip here (default: <repo>/build/out)
  --cache DIR      Cache downloaded PyPI artifacts here
                   (default: $XDG_CACHE_HOME/seedsigner-sim-build)
  --no-cache       Download everything fresh, cache nothing
  --keep-staging   Leave the assembled tree in the output directory, for
                   diffing against an unpacked wallet zip
  -h, --help       This message

Environment:
  SOURCE_DATE_EPOCH   Timestamp stamped into every zip entry. Defaults to the
                      commit date of the pinned upstream commit, so two people
                      who run this with no environment set get the same bytes.
USAGE
}

FIRMWARE=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)          OUT_DIR="$2"; shift 2 ;;
        --cache)        CACHE_DIR="$2"; shift 2 ;;
        --no-cache)     CACHE_DIR=""; shift ;;
        --keep-staging) KEEP_STAGING="yes"; shift ;;
        -h|--help)      usage; exit 0 ;;
        -*)             echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            [ -z "${FIRMWARE}" ] || { echo "only one firmware at a time: already have ${FIRMWARE}, then got $1" >&2; exit 2; }
            FIRMWARE="$1"; shift ;;
    esac
done

case "${FIRMWARE}" in
    smartcard|stock) ;;
    "")  echo "which firmware? one of: smartcard stock" >&2; usage >&2; exit 2 ;;
    *)   echo "no such firmware: ${FIRMWARE} (one of: smartcard stock)" >&2; exit 2 ;;
esac

die() {
    echo "build-wallet-zip: $*" >&2
    exit 1
}

step() {
    echo "==> $*"
}

for tool in git curl python3; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool not found: ${tool}"
done

# sha256sum on GNU systems, shasum -a 256 on macOS.
if command -v sha256sum >/dev/null 2>&1; then
    sha256_of() { sha256sum -- "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
    sha256_of() { shasum -a 256 -- "$1" | cut -d' ' -f1; }
else
    die "no sha256 tool found (looked for sha256sum and shasum)"
fi

# ---------------------------------------------------------------------------
# The dependency pins
# ---------------------------------------------------------------------------
#
# Upstream pins its Python dependencies in requirements.txt, some as PyPI
# versions and some as git or GitHub-archive URLs. Those pins are reproduced
# below, one row per dependency, in a form this script can act on.
#
# We do not shell out to pip. pip resolves, it does not just fetch: given the
# same requirements file on two machines it can pick different artifacts, it
# runs setup.py out of sdists (arbitrary code, arbitrary output), and it writes
# .dist-info directories whose contents depend on the pip and setuptools
# versions installed. None of that survives a byte-for-byte comparison. What we
# want is the narrow thing pip would be used for here: fetch one exact artifact,
# check it is the artifact we meant, unpack the pure-Python module out of it.
# That is what the table below describes, and it is auditable by reading it --
# every byte that enters the build is named and hashed here.
#
# Columns, pipe-separated:
#
#   kind      pypi (fetch an artifact by URL, verify sha256)
#             git  (clone and check out a commit, verified by git itself)
#   module    what lands at the top level of the zip: a package directory or a
#             single .py file
#   dist      distribution name, used to name its licence file
#   release   version, or the commit for git pins
#   url       artifact URL, or clone URL
#   integrity sha256 of the artifact, or the full commit sha
#   subpath   directory inside the unpacked source that contains `module`
#
# One table per firmware, because the two firmwares pin different sets: the
# smartcard fork needs the whole card stack, stock needs three libraries. They
# are written out separately rather than expressed as a base plus a delta. Two
# lists a reader can check line by line against two requirements.txt files beat
# a mechanism that computes the same lists and has to be understood first.
#
# EXPECTED_TOP_LEVEL beside each is everything that must be at the top level of
# the finished zip, and nothing else. Checked before the zip is written, so a
# dependency that silently failed to unpack stops the build instead of shipping
# a wallet that cannot import.

case "${FIRMWARE}" in
smartcard)

# Deliberately NOT in this table, and why:
#
#   Pillow, pycryptodomex, cryptography, cffi, pycparser
#       Compiled extensions. Pyodide builds and ships its own; the worker asks
#       for them with loadPackage() at boot (see src/web/wallet-worker.js).
#       Putting pure-Python stand-ins in the zip would shadow the real ones.
#       pycryptodomex is not separately available -- the worker aliases the
#       Cryptodome namespace onto Pyodide's pycryptodome.
#   pyscard
#       A C extension binding PC/SC. This is one of the four hardware seams:
#       src/smartcard/ in this repo deliberately shadows it with a fake card.
#   pyzbar
#       Binds libzbar. This fork's decode_qr.py imports it inside a try/except
#       and sets it to None, so nothing has to stand in for it here; QR decoding
#       happens in JavaScript (jsQR) instead. Stock imports it unguarded, which
#       is why the stock table below does have to ship a stand-in.
#   smbus2, periphery
#       I2C and GPIO. There is no /dev/i2c in a browser. battery_hat.py guards
#       both imports, so leaving them out is what makes the simulator correctly
#       report "no battery HAT" rather than fail later trying to talk to one.
#   colorama
#       Upstream marks it Windows-only.
#
# certifi IS included even though nothing in a browser opens a TLS socket,
# because pysatochip imports it at module scope and would fail to import
# without it.
#
# Two entries are not in upstream's requirements.txt at all but are imported by
# the wallet, so they are pinned here and flagged in THIRD-PARTY.md:
#
#   base58     not imported directly; bip38.py uses embit's own base58
#              submodule. Shipped because upstream's environment provides it
#   mnemonic   imported by seedsigner/views/seed_views.py and smartcard_views.py
#
# ecdsa needs six, which upstream pins but the earlier hand-assembled tree was
# missing; it is pinned here at upstream's version.
#
# pysatochip is the one row whose pin is deliberately NOT the one in upstream's
# requirements.txt, because the device does not use that file. requirements.txt
# asks PyPI for pysatochip==0.17.0; the SeedSigner OS image builds
# 3rdIteration/pysatochip from GitHub at the tag 0.6a through buildroot, and
# then deletes requirements.txt from the rootfs. Both are in seedsigner-os at
# the tag whose name matches this firmware's, SeSi-0.8.7+ShSi-B11:
#
#   opt/external-packages/python-pysatochip/python-pysatochip.mk
#       PYTHON_PYSATOCHIP_VERSION = 0.6a
#       PYTHON_PYSATOCHIP_SITE = $(call github,3rdIteration,pysatochip,...)
#   opt/pi0-smartcard/configs/pi0-smartcard_defconfig
#       BR2_PACKAGE_PYTHON_PYSATOCHIP=y
#   opt/build.sh
#       rm -rf ${rootfs_overlay}/opt/requirements.txt
#
# The two are not the same code. That GitHub tag calls itself pysatochip 0.17.4
# in its own version.py, four revisions past the newest release on PyPI, and one
# of those revisions is "correct handling of Password, Descriptor and Data
# secret types in seedkeeper export": its SEEDKEEPER_DIC_TYPE has
# 0xC1: 'Descriptor' and PyPI 0.17.0 has no entry for that type at all. Shipping
# the PyPI one gave this simulator a descriptor failure that does not exist on
# the device, which is precisely the kind of lie a simulator must not tell. So
# the row below is the tag, pinned by commit like every other git row here.
# Stock has no card code and no pysatochip, so none of this touches its build.

read -r -d '' DEPENDENCIES <<'DEPS' || true
pypi|base58|base58|2.1.1|https://files.pythonhosted.org/packages/4a/45/ec96b29162a402fc4c1c5512d114d7b3787b9d1c2ec241d9568b4816ee23/base58-2.1.1-py3-none-any.whl|11a36f4d3ce51dfc1043f3218591ac4eb1ceb172919cebe05b52a5bcc8d245c2|.
pypi|certifi|certifi|2025.7.14|https://files.pythonhosted.org/packages/4f/52/34c6cf5bb9285074dc3531c437b3919e825d976fde097a7a73f79e726d03/certifi-2025.7.14-py3-none-any.whl|6b31f564a415d79ee77df69d757bb49a5bb53bd9f756cbbe24394ffd6fc1f4b2|.
pypi|ecdsa|ecdsa|0.19.1|https://files.pythonhosted.org/packages/cb/a3/460c57f094a4a165c84a1341c373b0a4f5ec6ac244b998d5021aade89b77/ecdsa-0.19.1-py2.py3-none-any.whl|30638e27cf77b7e15c4c4cc1973720149e1033827cfd00661ca5c8cc0cdb24c3|.
pypi|embit|embit|0.8.0|https://files.pythonhosted.org/packages/83/88/b054b00ade6d2a41749e15976cdcec4b7ec4656ac1cb917ce3de395528d1/embit-0.8.0.tar.gz|8bf4b10073c67400370ce523fb16f035fe759f6fdd987c579bdcc268d75ed770|embit-0.8.0/src
pypi|mnemonic|mnemonic|0.21|https://files.pythonhosted.org/packages/57/48/5abb16ce7f9d97b728e6b97c704ceaa614362e0847651f379ed0511942a0/mnemonic-0.21-py3-none-any.whl|72dc9de16ec5ef47287237b9b6943da11647a03fe7cf1f139fc3d7c4a7439288|.
pypi|ndef|ndeflib|0.3.3|https://files.pythonhosted.org/packages/c9/80/bbc9a4818cd74807f914d225611cd724d8c0e56237b952a9a4aa6d583f5c/ndeflib-0.3.3-py2.py3-none-any.whl|c634b1af2ab454754f0fdbe1debd38247ed7bdaf94587359b857726f3ee7decb|.
pypi|OpenSSL|pyOpenSSL|25.1.0|https://files.pythonhosted.org/packages/80/28/2659c02301b9500751f8d42f9a6632e1508aa5120de5e43042b8b30f8d5d/pyopenssl-25.1.0-py3-none-any.whl|2b11f239acc47ac2e5aca04fd7fa829800aeee22a2eb30d744572a157bd8a1ab|.
pypi|pyaes|pyaes|1.6.1|https://files.pythonhosted.org/packages/44/66/2c17bae31c906613795711fc78045c285048168919ace2220daa372c7d72/pyaes-1.6.1.tar.gz|02c1b1405c38d3c370b085fb952dd8bea3fadcee6411ad99f312cc129c536d8f|pyaes-1.6.1
pypi|pyasn1|pyasn1|0.6.2|https://files.pythonhosted.org/packages/44/b5/a96872e5184f354da9c84ae119971a0a4c221fe9b27a4d94bd43f2596727/pyasn1-0.6.2-py3-none-any.whl|1eb26d860996a18e9b6ed05e7aae0e9fc21619fcee6af91cca9bad4fbea224bf|.
pypi|qrcode|qrcode|7.3.1|https://files.pythonhosted.org/packages/94/9f/31f33cdf3cf8f98e64c42582fb82f39ca718264df61957f28b0bbb09b134/qrcode-7.3.1.tar.gz|375a6ff240ca9bd41adc070428b5dfc1dcfbb0f2507f1ac848f6cded38956578|qrcode-7.3.1
pypi|shamir_mnemonic|shamir-mnemonic|0.3.0|https://files.pythonhosted.org/packages/1d/38/2124e565afe40993949dbc89da6c654a2c9a1b24dd80039812ef7cdbaef3/shamir_mnemonic-0.3.0-py3-none-any.whl|188c6b5bd00d5e756e12e2b186c3cb7c98ff7ff44df608d4c1d2077f6b6e730f|.
pypi|six.py|six|1.17.0|https://files.pythonhosted.org/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl|4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274|.
pypi|typing_extensions.py|typing_extensions|4.14.1|https://files.pythonhosted.org/packages/b5/00/d631e67a838026495268c2f6884f3711a15a9a2a96cd244fdaea53b823fb/typing_extensions-4.14.1-py3-none-any.whl|d1e1e3b58374dc93031d6eda2420a48ea44a36c2b4766a4fdeb3710755731d76|.
git|pgpy|PGPy-3rdIteration-fork|7cdad000a76ced53c873211241d5ba20019a8488|https://github.com/3rdIteration/PGPy.git|7cdad000a76ced53c873211241d5ba20019a8488|.
git|pygp|PyGP-3rdIteration-fork|15682ec8fd042b5d0ae3422e9434e9734db6e55b|https://github.com/3rdIteration/pygp.git|15682ec8fd042b5d0ae3422e9434e9734db6e55b|.
git|pysatochip|pysatochip-3rdIteration|d77e311e0cd39193c9b2c03a1ab5f69421b8f4d5|https://github.com/3rdIteration/pysatochip.git|d77e311e0cd39193c9b2c03a1ab5f69421b8f4d5|.
git|specter_card|specter-card|06dcde629cdc1057934b434afc46d822c2d2425d|https://github.com/3rdIteration/specter-javacard.git|06dcde629cdc1057934b434afc46d822c2d2425d|py
git|urtypes|urtypes|7fb280eab3b3563dfc57d2733b0bf5cbc0a96a6a|https://github.com/selfcustody/urtypes.git|7fb280eab3b3563dfc57d2733b0bf5cbc0a96a6a|src
DEPS

EXPECTED_TOP_LEVEL=(
    LICENSE.md
    OpenSSL
    base58
    certifi
    ecdsa
    embit
    licenses
    main.py
    mnemonic
    ndef
    pgpy
    pyaes
    pyasn1
    pygp
    pysatochip
    qrcode
    seedsigner
    shamir_mnemonic
    six.py
    smartcard
    specter_card
    typing_extensions.py
    urtypes
)

# The simulated smartcards, shadowing pyscard's `smartcard` module. Nothing in
# stock imports it, so only this firmware stages it. Rows are
# source:name-in-the-zip:what the licences manifest should call it.
STAGE_PACKAGES=(
    "${REPO_ROOT}/src/smartcard:smartcard:fake card, not pyscard"
)

;;
stock)

# Stock's whole requirements.txt is five lines, and two of them do not belong in
# a zip:
#
#   Pillow
#       A compiled extension. Pyodide ships its own and the worker asks for it
#       with loadPackage() at boot (see src/web/wallet-worker.js); a pure-Python
#       stand-in here would shadow the real one.
#   pyzbar
#       Binds libzbar, which has no WebAssembly build. Stock's decode_qr.py
#       imports it at module scope with no try/except, so the import has to
#       succeed: src/fakes/pyzbar is staged below and browser_camera.py replaces
#       the one function that would have called it. The smartcard fork guards
#       the same import and therefore needs no such file.
#
# The other three are pinned here. embit and qrcode are the same artifacts, at
# the same versions and the same sha256, that the smartcard table pins, because
# both firmwares pin the same two versions.
#
# urtypes is the one place the two firmwares genuinely disagree. The fork pins a
# selfcustody git commit; stock pins PyPI 1.0.1. Their trees differ by a single
# line, `from .crypto import *` in __init__.py, which PyPI 1.0.1 has and the
# pinned commit does not. Reusing the fork's pin would have built and probably
# run, and it would also have meant this zip was not the thing stock's
# requirements.txt names. Each firmware gets the pin its own upstream published.

read -r -d '' DEPENDENCIES <<'DEPS' || true
pypi|embit|embit|0.8.0|https://files.pythonhosted.org/packages/83/88/b054b00ade6d2a41749e15976cdcec4b7ec4656ac1cb917ce3de395528d1/embit-0.8.0.tar.gz|8bf4b10073c67400370ce523fb16f035fe759f6fdd987c579bdcc268d75ed770|embit-0.8.0/src
pypi|qrcode|qrcode|7.3.1|https://files.pythonhosted.org/packages/94/9f/31f33cdf3cf8f98e64c42582fb82f39ca718264df61957f28b0bbb09b134/qrcode-7.3.1.tar.gz|375a6ff240ca9bd41adc070428b5dfc1dcfbb0f2507f1ac848f6cded38956578|qrcode-7.3.1
pypi|urtypes|urtypes|1.0.1|https://files.pythonhosted.org/packages/60/43/f4acb0faf63bb92070760a3039a8cae1a88c46947c71e77e99a03e196ea5/urtypes-1.0.1.tar.gz|4f1cd0ef34c21ae6f408520ecd9de0d2d157ee885b94ad9e6481cfbb3838558e|urtypes-1.0.1/src
DEPS

EXPECTED_TOP_LEVEL=(
    LICENSE.md
    RPi
    embit
    licenses
    main.py
    pyzbar
    qrcode
    seedsigner
    urtypes
)

# Two import-time stand-ins, and no `smartcard`: stock has no card code to
# import it. See src/fakes/README.md for what they are and are not. Rows are
# source:name-in-the-zip:what the licences manifest should call it.
STAGE_PACKAGES=(
    "${REPO_ROOT}/src/fakes/RPi:RPi:import stand-in, not RPi.GPIO"
    "${REPO_ROOT}/src/fakes/pyzbar:pyzbar:import stand-in, not pyzbar"
)

;;
esac

# ---------------------------------------------------------------------------
# Scratch space
# ---------------------------------------------------------------------------
#
# Deliberately outside the repository: a build must never leave anything behind
# in a tree someone is about to commit.

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/seedsigner-sim-build.XXXXXXXX")"
cleanup() {
    rm -rf -- "${WORK_DIR}"
}
trap cleanup EXIT

STAGING="${WORK_DIR}/staging"
SOURCES="${WORK_DIR}/sources"
mkdir -p "${STAGING}/licenses" "${SOURCES}"

if [ -n "${CACHE_DIR}" ]; then
    mkdir -p "${CACHE_DIR}"
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# fetch_verified URL EXPECTED_SHA256 DEST
#
# Serves the file from the cache when the cached copy hashes correctly, so a
# rebuild is fast and an offline rebuild is possible. The hash is re-checked on
# every use, cached or not, so a poisoned cache cannot get past this.
fetch_verified() {
    local url="$1" expected="$2" dest="$3"
    local cached=""

    if [ -n "${CACHE_DIR}" ]; then
        cached="${CACHE_DIR}/${expected}"
        if [ -f "${cached}" ] && [ "$(sha256_of "${cached}")" = "${expected}" ]; then
            cp -- "${cached}" "${dest}"
            return 0
        fi
    fi

    curl --fail --location --silent --show-error \
         --proto '=https' --tlsv1.2 \
         --retry 3 --retry-delay 2 \
         --output "${dest}" -- "${url}" \
        || die "download failed: ${url}"

    local actual
    actual="$(sha256_of "${dest}")"
    if [ "${actual}" != "${expected}" ]; then
        die "sha256 mismatch for ${url}
  expected ${expected}
  got      ${actual}"
    fi

    if [ -n "${cached}" ]; then
        cp -- "${dest}" "${cached}"
    fi
}

# unpack ARCHIVE DEST
#
# python3 rather than unzip/tar, so that .whl, .zip and .tar.gz all go through
# one code path and the script needs neither unzip nor bzip2 installed.
# tarfile's data filter rejects absolute paths, .. escapes, symlinks pointing
# out of the tree, devices and setuid bits.
unpack() {
    local archive="$1" dest="$2"
    mkdir -p "${dest}"
    python3 - "${archive}" "${dest}" <<'PY'
import sys, tarfile, zipfile

archive, dest = sys.argv[1], sys.argv[2]

if archive.endswith((".whl", ".zip")):
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if name.startswith("/") or ".." in name.split("/"):
                sys.exit(f"refusing to extract unsafe path: {name}")
        zf.extractall(dest)
elif archive.endswith((".tar.gz", ".tgz", ".tar.bz2")):
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")
else:
    sys.exit(f"do not know how to unpack {archive}")
PY
}

# git_checkout URL COMMIT DEST
#
# The commit sha is the integrity check: git verifies that the object graph it
# received hashes to the sha we asked for, so there is nothing extra to compare.
# This is also why the four dependencies upstream pins as GitHub archive .zip
# URLs are fetched with git here instead. Those URLs name a snapshot of a
# commit, but the zip around it is generated by GitHub on demand and its bytes
# are not promised to be stable, so hashing it would pin the archiver rather
# than the source. Checking out the same commit gets the same files by
# construction.
git_checkout() {
    local url="$1" commit="$2" dest="$3"

    mkdir -p "${dest}"
    GIT_TERMINAL_PROMPT=0 git -C "${dest}" init --quiet
    GIT_TERMINAL_PROMPT=0 git -C "${dest}" remote add origin "${url}"
    GIT_TERMINAL_PROMPT=0 git -C "${dest}" fetch --quiet --depth 1 origin "${commit}" \
        || die "could not fetch ${commit} from ${url}"
    GIT_TERMINAL_PROMPT=0 git -C "${dest}" -c advice.detachedHead=false \
        checkout --quiet FETCH_HEAD

    local head
    head="$(git -C "${dest}" rev-parse HEAD)"
    if [ "${head}" != "${commit}" ]; then
        die "checkout of ${url} landed on ${head}, expected ${commit}"
    fi
}

# find_license ROOT
#
# Prints the shallowest LICENSE/LICENCE/COPYING file under ROOT. Shallowest,
# because several dependencies also ship a licence deep inside a vendored
# subpackage, and the one at the root is the one that governs the whole
# distribution.
find_license() {
    local root="$1"
    {
        find "${root}" -type f \
            \( -iname 'LICENSE' -o -iname 'LICENSE.*' \
            -o -iname 'LICENCE' -o -iname 'LICENCE.*' \
            -o -iname 'COPYING' -o -iname 'COPYING.*' \) \
        | awk -F/ '{ print NF "\t" $0 }' \
        | LC_ALL=C sort -k1,1n -k2,2
    } | sed -n '1p' | cut -f2-
}

# ---------------------------------------------------------------------------
# 1. The wallet: upstream SeedSigner at the pinned commit
# ---------------------------------------------------------------------------
#
# The pin lives in UPSTREAM rather than in this script, so there is exactly one
# place to look and exactly one place to change it.

UPSTREAM_FILE="${REPO_ROOT}/UPSTREAM"
[ -f "${UPSTREAM_FILE}" ] || die "missing ${UPSTREAM_FILE}"

# upstream_field KEY
#
# One key out of the [FIRMWARE] section of UPSTREAM. Section-aware, because that
# file now describes two firmwares and a parser that ignored the headers would
# cheerfully hand back the other one's commit. The same awk program appears in
# .github/workflows/reproducible-build.yml and upstream-tests.yml, which read
# the same file for the same reason.
upstream_field() {
    awk -F= -v want="[${FIRMWARE}]" -v key="$1" '
        /^\[/   { inside = ($0 == want); next }
        inside && $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
            gsub(/[[:space:]]/, "", $2); print $2
        }
    ' "${UPSTREAM_FILE}"
}

UPSTREAM_REPO="$(upstream_field repo)"
UPSTREAM_COMMIT="$(upstream_field commit)"

[ -n "${UPSTREAM_REPO}" ]   || die "no 'repo =' line in the [${FIRMWARE}] section of ${UPSTREAM_FILE}"
[ -n "${UPSTREAM_COMMIT}" ] || die "no 'commit =' line in the [${FIRMWARE}] section of ${UPSTREAM_FILE}"

case "${UPSTREAM_COMMIT}" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
    *) die "commit in ${UPSTREAM_FILE} is not a sha: ${UPSTREAM_COMMIT}" ;;
esac

step "firmware ${FIRMWARE}"
step "upstream ${UPSTREAM_REPO} @ ${UPSTREAM_COMMIT}"
UPSTREAM_SRC="${SOURCES}/upstream"
git_checkout "${UPSTREAM_REPO}" "${UPSTREAM_COMMIT}" "${UPSTREAM_SRC}"

for required in src/seedsigner src/main.py LICENSE.md; do
    [ -e "${UPSTREAM_SRC}/${required}" ] || die "pinned upstream tree is missing ${required}"
done

# ---------------------------------------------------------------------------
# 2. Timestamp
# ---------------------------------------------------------------------------
#
# Zip entries carry an mtime, so an unpinned one would make every build differ.
# Defaulting to the pinned commit's own date means two people who set no
# environment variables still agree, and the date is derived from the pin rather
# than being one more magic number to trust.

if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH="$(git -C "${UPSTREAM_SRC}" show -s --format=%ct HEAD)"
    [ -n "${SOURCE_DATE_EPOCH}" ] || die "could not read the commit date of ${UPSTREAM_COMMIT}"
fi
export SOURCE_DATE_EPOCH
step "SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}"

# .git was only needed for the checkout and the date above. Removing it now
# keeps it out of the staged tree by construction rather than by filtering.
rm -rf -- "${UPSTREAM_SRC}/.git"

# Verbatim. Nothing in this repository patches the wallet -- the hardware seams
# are replaced from outside, by src/shims (which the worker writes into the
# filesystem after unpacking) and by the stand-in packages staged below.
cp -R -- "${UPSTREAM_SRC}/src/seedsigner" "${STAGING}/seedsigner"
cp    -- "${UPSTREAM_SRC}/src/main.py"    "${STAGING}/main.py"

# The MIT notice travels with the code it covers.
cp    -- "${UPSTREAM_SRC}/LICENSE.md"     "${STAGING}/LICENSE.md"
cp    -- "${UPSTREAM_SRC}/LICENSE.md"     "${STAGING}/licenses/SeedSigner.LICENSE"

# ---------------------------------------------------------------------------
# 3. This repository's stand-in packages
# ---------------------------------------------------------------------------
#
# Each one shadows a module the wallet imports and this environment cannot
# provide. They are top-level entries in the zip for exactly that reason:
# /wallet is first on sys.path, so the import in unmodified SeedSigner code
# finds ours. Which ones are staged is the firmware's business, and the list was
# chosen above:
#
#   smartcard  the simulated SeedKeeper and Satochip, shadowing pyscard. Only
#              the fork has card code to import it.
#   RPi        an import stand-in for RPi.GPIO. Only stock imports it unguarded.
#   pyzbar     an import stand-in for the zbar binding, for the same reason.
#
# Nothing the other firmware never imports is shipped to it. A zip whose claim
# is "the pin, its pinned dependencies and this repository's stand-ins, and
# nothing else" should not carry a package that is dead on arrival.

MANIFEST_STAGED=""

for entry in "${STAGE_PACKAGES[@]}"; do
    IFS=':' read -r source name description <<< "${entry}"
    [ -f "${source}/__init__.py" ] || die "missing ${source}/__init__.py"
    cp -R -- "${source}" "${STAGING}/${name}"
    MANIFEST_STAGED="${MANIFEST_STAGED}$(printf '%-22s %-26s %s' "${name}" "this repository" "${description}")
"
done

# ---------------------------------------------------------------------------
# 4. Dependencies
# ---------------------------------------------------------------------------

MANIFEST="${WORK_DIR}/licenses-manifest.txt"
{
    echo "Third-party code redistributed inside this wallet.zip."
    echo "Licence texts are the files alongside this one."
    echo "Written by build/build-wallet-zip.sh; see THIRD-PARTY.md for the full picture."
    echo
    printf '%-22s %-26s %s\n' "MODULE" "DISTRIBUTION" "RELEASE"
    printf '%-22s %-26s %s\n' "seedsigner, main.py" "SeedSigner" "commit ${UPSTREAM_COMMIT}"
    printf '%s' "${MANIFEST_STAGED}"
} > "${MANIFEST}"

while IFS='|' read -r kind module dist release url integrity subpath; do
    [ -n "${kind}" ] || continue
    case "${kind}" in \#*) continue ;; esac

    step "dependency ${module} (${dist} ${release})"

    dep_dir="${SOURCES}/dep-${module}"
    mkdir -p "${dep_dir}"

    case "${kind}" in
        pypi)
            artifact="${dep_dir}/${url##*/}"
            fetch_verified "${url}" "${integrity}" "${artifact}"
            unpack "${artifact}" "${dep_dir}/unpacked"
            rm -f -- "${artifact}"
            ;;
        git)
            git_checkout "${url}" "${integrity}" "${dep_dir}/unpacked"
            rm -rf -- "${dep_dir}/unpacked/.git"
            ;;
        *)
            die "unknown dependency kind '${kind}' for ${module}"
            ;;
    esac

    source_path="${dep_dir}/unpacked/${subpath}/${module}"
    [ -e "${source_path}" ] || die "${dist} ${release}: expected ${subpath}/${module} in the unpacked source, not found"
    cp -R -- "${source_path}" "${STAGING}/${module}"

    license_file="$(find_license "${dep_dir}/unpacked")"
    [ -n "${license_file}" ] || die "${dist} ${release}: no licence file in the source; refusing to redistribute it"
    cp -- "${license_file}" "${STAGING}/licenses/${dist}.LICENSE"

    printf '%-22s %-26s %s\n' "${module}" "${dist}" "${release}" >> "${MANIFEST}"
done <<< "${DEPENDENCIES}"

cp -- "${MANIFEST}" "${STAGING}/licenses/MANIFEST.txt"

# ---------------------------------------------------------------------------
# 5. Scrub
# ---------------------------------------------------------------------------
#
# Compiled bytecode is host-specific and timestamped, so it can never be part of
# a reproducible artifact. Nothing in this build generates any, but a dependency
# could ship some, and the check below turns that into a build failure rather
# than a silent difference between two people's zips.

find "${STAGING}" -type d -name '__pycache__' -prune -exec rm -rf -- {} +
find "${STAGING}" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

leftovers="$(find "${STAGING}" \( -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' \) -print)"
[ -z "${leftovers}" ] || die "bytecode survived the scrub:
${leftovers}"

# ---------------------------------------------------------------------------
# 6. Check the tree before writing anything
# ---------------------------------------------------------------------------
#
# Fail loudly here rather than ship a zip that unpacks into a wallet which
# cannot import. Both directions are checked: a missing entry means a dependency
# did not unpack, an unexpected one means something got in that nobody declared.

actual_top_level="$( (cd -- "${STAGING}" && find . -mindepth 1 -maxdepth 1) | sed 's|^\./||' | LC_ALL=C sort | tr '\n' ' ')"
expected_top_level="$(printf '%s\n' "${EXPECTED_TOP_LEVEL[@]}" | LC_ALL=C sort | tr '\n' ' ')"

if [ "${actual_top_level}" != "${expected_top_level}" ]; then
    die "staged tree does not have the expected top level
  expected: ${expected_top_level}
  actual:   ${actual_top_level}"
fi

[ -f "${STAGING}/seedsigner/controller.py" ] || die "staged seedsigner package looks wrong: no controller.py"

for entry in "${STAGE_PACKAGES[@]}"; do
    IFS=':' read -r _ name _ <<< "${entry}"
    [ -f "${STAGING}/${name}/__init__.py" ] || die "staged ${name} package looks wrong: no __init__.py"
done

# ---------------------------------------------------------------------------
# 7. Write the zip
# ---------------------------------------------------------------------------
#
# Written entry by entry rather than with zip(1), because the things that make a
# zip non-reproducible are all defaults zip(1) takes from the host: entry order
# from readdir, mtimes from the filesystem, permissions from the umask, and a
# "created by" byte from the platform. Every one of those is pinned below.

# Named after the firmware, so both can sit in one directory and be served side
# by side, and so a downloaded file says which pin it is meant to match.
mkdir -p "${OUT_DIR}"
OUT_ZIP="${OUT_DIR}/wallet-${FIRMWARE}.zip"
OUT_MANIFEST="${OUT_DIR}/wallet-${FIRMWARE}.zip.manifest"

step "writing ${OUT_ZIP}"
python3 - "${STAGING}" "${OUT_ZIP}" "${OUT_MANIFEST}" "${SOURCE_DATE_EPOCH}" <<'PY'
import hashlib
import os
import sys
import time
import zipfile

staging, out_zip, out_manifest, epoch = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])

# gmtime, not localtime: a zip stores a bare DOS timestamp with no zone, so
# local time would make the artifact depend on the builder's TZ.
stamp = time.gmtime(epoch)[:6]
if stamp[0] < 1980:
    sys.exit("SOURCE_DATE_EPOCH is before 1980, which a zip timestamp cannot represent")

entries = []  # (archive name, source path, or None for a directory)
for root, dirnames, filenames in os.walk(staging):
    dirnames.sort()
    filenames.sort()
    for name in dirnames:
        full = os.path.join(root, name)
        entries.append((os.path.relpath(full, staging).replace(os.sep, "/") + "/", None))
    for name in filenames:
        full = os.path.join(root, name)
        if os.path.islink(full):
            sys.exit(f"refusing to archive a symlink: {full}")
        entries.append((os.path.relpath(full, staging).replace(os.sep, "/"), full))

# One canonical order, by archive name, independent of the order in which the
# filesystem happened to hand back its directory listings.
entries.sort(key=lambda item: item[0])

manifest = []

with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    zf.comment = b""
    for arcname, source in entries:
        info = zipfile.ZipInfo(arcname, date_time=stamp)
        info.create_system = 3  # Unix, whatever the host actually is
        if source is None:
            info.external_attr = (0o40755 << 16) | 0x10
            info.compress_type = zipfile.ZIP_STORED
            zf.writestr(info, b"")
        else:
            with open(source, "rb") as handle:
                data = handle.read()
            # Fixed permissions: the umask of whoever ran the build must not
            # reach the artifact.
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
            manifest.append(f"{hashlib.sha256(data).hexdigest()}  {arcname}")

manifest_text = "\n".join(manifest) + "\n"
with open(out_manifest, "w", encoding="utf-8") as handle:
    handle.write(manifest_text)

with open(out_zip, "rb") as handle:
    zip_digest = hashlib.sha256(handle.read()).hexdigest()

print(f"    files     {len(manifest)}")
print(f"    zip       sha256 {zip_digest}")
print(f"    contents  sha256 {hashlib.sha256(manifest_text.encode('utf-8')).hexdigest()}")
PY

if [ "${KEEP_STAGING}" = "yes" ]; then
    rm -rf -- "${OUT_DIR}/staging-${FIRMWARE}"
    cp -R -- "${STAGING}" "${OUT_DIR}/staging-${FIRMWARE}"
    step "staged tree kept at ${OUT_DIR}/staging-${FIRMWARE}"
fi

# ---------------------------------------------------------------------------
# 8. What this build is, written down for the page
# ---------------------------------------------------------------------------
#
# The simulator shows a visitor what it is running: the firmware, the pin it
# came from, the hashes to compare against, what the zip carries and which
# Pyodide interprets it. That description has to be produced by the build, not
# maintained beside it, or it becomes one more thing that can drift and be wrong
# exactly when someone is checking. Every field below is read out of UPSTREAM,
# out of the dependency table in this file, or out of build/fetch-assets.sh.
#
# Beside the zip and not inside it: the zip's bytes are the thing being
# compared, and nothing added here may touch them.
#
# The two hashes are the ones UPSTREAM publishes, deliberately, rather than the
# ones this run just printed. They are what a reader is asked to compare
# against, so a build that stopped reproducing has to show up as a mismatch on
# the page rather than quietly publishing whatever it produced.

UPSTREAM_TAG="$(upstream_field tag)"
PUBLISHED_ZIP_SHA256="$(upstream_field wallet_zip_sha256)"
PUBLISHED_CONTENTS_SHA256="$(upstream_field wallet_zip_contents_sha256)"

[ -n "${UPSTREAM_TAG}" ]               || die "no 'tag =' line in the [${FIRMWARE}] section of ${UPSTREAM_FILE}"
[ -n "${PUBLISHED_ZIP_SHA256}" ]       || die "no 'wallet_zip_sha256 =' line in the [${FIRMWARE}] section of ${UPSTREAM_FILE}"
[ -n "${PUBLISHED_CONTENTS_SHA256}" ]  || die "no 'wallet_zip_contents_sha256 =' line in the [${FIRMWARE}] section of ${UPSTREAM_FILE}"

# The runtime is fetched by another script and pinned there, which makes that
# script the one place the version is written down.
ASSETS_SCRIPT="${REPO_ROOT}/build/fetch-assets.sh"
[ -f "${ASSETS_SCRIPT}" ] || die "missing ${ASSETS_SCRIPT}"
PYODIDE_VERSION="$(sed -n 's/^PYODIDE_VERSION="\([^"]*\)".*$/\1/p' "${ASSETS_SCRIPT}" | sed -n 1p)"
[ -n "${PYODIDE_VERSION}" ] || die "no PYODIDE_VERSION= line in ${ASSETS_SCRIPT}"

OUT_INFO="${OUT_DIR}/wallet-${FIRMWARE}.build-info.json"

step "writing ${OUT_INFO}"
INFO_FIRMWARE="${FIRMWARE}" \
INFO_REPO="${UPSTREAM_REPO}" \
INFO_COMMIT="${UPSTREAM_COMMIT}" \
INFO_TAG="${UPSTREAM_TAG}" \
INFO_ZIP="wallet-${FIRMWARE}.zip" \
INFO_ZIP_SHA256="${PUBLISHED_ZIP_SHA256}" \
INFO_CONTENTS_SHA256="${PUBLISHED_CONTENTS_SHA256}" \
INFO_PYODIDE="${PYODIDE_VERSION}" \
INFO_DEPENDENCIES="${DEPENDENCIES}" \
python3 - "${OUT_INFO}" <<'PY'
import json
import os
import sys

# The same rows the build just acted on, so the list a reader is shown is the
# list that was fetched rather than a description of it.
dependencies = []
for line in os.environ["INFO_DEPENDENCIES"].splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    kind, module, dist, release = line.split("|")[:4]
    dependencies.append({"name": dist, "version": release,
                         "module": module, "kind": kind})

info = {
    "firmware": os.environ["INFO_FIRMWARE"],
    "upstream": {
        "repo": os.environ["INFO_REPO"],
        "commit": os.environ["INFO_COMMIT"],
        "tag": os.environ["INFO_TAG"],
    },
    "wallet_zip": {
        "name": os.environ["INFO_ZIP"],
        "published_sha256": os.environ["INFO_ZIP_SHA256"],
        "published_contents_sha256": os.environ["INFO_CONTENTS_SHA256"],
    },
    "pyodide": os.environ["INFO_PYODIDE"],
    "dependencies": dependencies,
}

# Nothing dated, nothing about this machine, nothing from a set: two runs of
# this script write the same bytes, the same way the zip beside it does.
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(info, handle, indent=2)
    handle.write("\n")
PY

step "done"
echo
echo "  ${OUT_ZIP}"
echo "  ${OUT_MANIFEST}"
echo "  ${OUT_INFO}"
echo
echo "Compare the zip sha256 above with the wallet-${FIRMWARE}.zip you were served,"
echo "and with the [${FIRMWARE}] section of UPSTREAM."
echo "If those differ but the contents sha256 matches, the two builds hold the"
echo "same files and you are looking at a zlib difference, not a code difference."
