#!/usr/bin/env bash
#
# Build DOOM: the boot game the SeedSigner firmware ships, compiled to
# WebAssembly so the simulator can run it.
#
#   ./build/build-doom-wasm.sh
#   ./build/build-doom-wasm.sh --check      # re-hash what is already on disk
#
# Three files come out, and the script prints a sha256 for each:
#
#   doom.js       the emscripten loader, which defines createDoomModule
#   doom.wasm     the game
#   doom-run.js   the wrapper the page talks to, which defines DoomRun
#
# The WAD is not one of them. Freedoom is nearly 30MB, it is fetched at runtime
# into the module's virtual filesystem rather than linked into the binary, and
# it is content-addressed by its own release; baking it in would triple the
# artifact and make the wasm impossible to compare against anybody else's build.
# The script prints its hash at the end so it can be checked separately.
#
# What "reproducible" means here, honestly:
#
#   * The source is a tree you can read. Nothing in this repository patches
#     DOOM; the port is boot-game/doom/ in seedsigner-os and it is 700 lines of
#     C over an unmodified doomgeneric.
#   * Two builds from the same source, the same doomgeneric commit and the same
#     emscripten release produce the same bytes. That is the comparison the
#     hashes below are for, and it is why the emscripten version is pinned in
#     this file and checked before anything is built.
#   * Change any of those three and the hashes move. A compiler is not a
#     download: a mismatch here means "you built it with something else", not
#     "somebody tampered with it", so a mismatch warns rather than stops.
#
# What the browser sees is what the panel sees. The build reuses ss_video.c
# from the firmware port unmodified, so every frame handed to the page is the
# 240x240 RGB565, big endian, letterboxed picture that goes over SPI to the
# ST7789 on a real device. It is not DOOM rendered nicely for a canvas.
#
# Requires: bash, make, python3, sha256sum (or shasum), and emscripten.

set -euo pipefail

# ---------------------------------------------------------------------------
# The pins
# ---------------------------------------------------------------------------
#
# emscripten 6.0.5 is what the published artifacts were built with. emsdk
# resolves "latest" to a moving target, so pin the number rather than the word:
#
#   git clone https://github.com/emscripten-core/emsdk.git ~/emsdk
#   cd ~/emsdk && ./emsdk install 6.0.5 && ./emsdk activate 6.0.5

EXPECTED_EMSCRIPTEN="6.0.5"

# doomgeneric is upstream and unmodified. It is not vendored here because the
# firmware port already builds against a checkout of it; this is the commit
# that checkout is expected to be at, and the script prints what it actually
# found so a difference is visible rather than silent.
EXPECTED_DOOMGENERIC="dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284"

# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# The firmware port. Not part of this repository: the simulator runs the boot
# game, it does not own it.
DOOM_SRC="${DOOM_SRC:-${HOME}/apps/seedsigner-os/boot-game/doom}"

# Next to the wallet zip, for the same reason it is there: these are build
# outputs, not source files, and committing them would invite the reader to
# trust the copy in git instead of rebuilding and comparing.
#
# Installing them is a copy, and a deliberately separate step, because the page
# decides what it serves from where:
#
#   cp build/out/doom/doom.js build/out/doom/doom.wasm build/out/doom/doom-run.js src/web/
#
# doom-run.js looks for doom.js next to itself, so all three land in the same
# directory and no path is written down twice. The WAD goes there too, under
# the name doom-boot.js asks for.
DEST_DIR="${REPO_ROOT}/build/out/doom"

EMSDK_DIR="${EMSDK:-${HOME}/emsdk}"
MODE="build"

ARTIFACTS=(doom.js doom.wasm doom-run.js)

usage() {
    cat <<'USAGE'
Usage: build-doom-wasm.sh [options]

  --src DIR    The seedsigner-os DOOM port to build
               (default: $DOOM_SRC, else ~/apps/seedsigner-os/boot-game/doom)
  --dest DIR   Where to put the artifacts
               (default: <repo>/build/out/doom; copy them next to the page's
               other assets, which is src/web/, to actually serve them)
  --emsdk DIR  An activated emsdk (default: $EMSDK, else ~/emsdk)
  --check      Hash what is already on disk and exit. Builds nothing.
  -h, --help   This message
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --src)     DOOM_SRC="$2"; shift 2 ;;
        --dest)    DEST_DIR="$2"; shift 2 ;;
        --emsdk)   EMSDK_DIR="$2"; shift 2 ;;
        --check)   MODE="check"; shift ;;
        -h|--help) usage; exit 0 ;;
        *)         echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

die() {
    echo "build-doom-wasm: $*" >&2
    exit 1
}

step() {
    echo "==> $*"
}

warn() {
    echo "    WARNING: $*" >&2
}

if command -v sha256sum >/dev/null 2>&1; then
    sha256_of() { sha256sum -- "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
    sha256_of() { shasum -a 256 -- "$1" | cut -d' ' -f1; }
else
    die "no sha256 tool found (looked for sha256sum and shasum)"
fi

report() {
    echo
    echo "Artifacts in ${DEST_DIR}:"
    echo
    for name in "${ARTIFACTS[@]}"; do
        artifact="${DEST_DIR}/${name}"
        [ -f "${artifact}" ] || die "missing artifact: ${artifact}"
        printf '  %-14s %10s bytes  %s\n' \
            "${name}" "$(wc -c < "${artifact}")" "$(sha256_of "${artifact}")"
    done
}

# ---------------------------------------------------------------------------
# --check does not need a toolchain
# ---------------------------------------------------------------------------

if [ "${MODE}" = "check" ]; then
    [ -d "${DEST_DIR}" ] || die "nothing built yet: ${DEST_DIR} does not exist"
    report
    exit 0
fi

# ---------------------------------------------------------------------------
# The toolchain
# ---------------------------------------------------------------------------

[ -d "${DOOM_SRC}" ] || die "no DOOM port at ${DOOM_SRC} (pass --src)"
[ -f "${DOOM_SRC}/Makefile" ] || die "${DOOM_SRC} has no Makefile; wrong directory?"

if ! command -v emcc >/dev/null 2>&1; then
    step "sourcing emsdk from ${EMSDK_DIR}"
    [ -f "${EMSDK_DIR}/emsdk_env.sh" ] \
        || die "no emsdk at ${EMSDK_DIR}. Install it with:
    git clone https://github.com/emscripten-core/emsdk.git ${EMSDK_DIR}
    cd ${EMSDK_DIR} && ./emsdk install ${EXPECTED_EMSCRIPTEN} && ./emsdk activate ${EXPECTED_EMSCRIPTEN}"
    # shellcheck disable=SC1091
    source "${EMSDK_DIR}/emsdk_env.sh" >/dev/null 2>&1
fi

command -v emcc >/dev/null 2>&1 || die "emcc is still not on PATH after sourcing emsdk"

EMCC_VERSION="$(emcc --version | head -1 | sed -n 's/.*clang-like replacement + linker emulating GNU ld) \([0-9.]*\).*/\1/p')"
step "emscripten ${EMCC_VERSION:-unknown}"
if [ "${EMCC_VERSION}" != "${EXPECTED_EMSCRIPTEN}" ]; then
    warn "expected emscripten ${EXPECTED_EMSCRIPTEN}; the hashes below will not match the published ones"
fi

# doomgeneric comes from wherever the port's Makefile points, which is a
# checkout rather than a submodule. Say which one, so the build is described by
# more than "it compiled here".
DG_DIR="${DG:-${HOME}/apps/doomgeneric-src/doomgeneric}"
if [ -d "${DG_DIR}/../.git" ]; then
    DG_COMMIT="$(git -C "${DG_DIR}/.." rev-parse HEAD)"
    step "doomgeneric ${DG_COMMIT}"
    if [ "${DG_COMMIT}" != "${EXPECTED_DOOMGENERIC}" ]; then
        warn "expected doomgeneric ${EXPECTED_DOOMGENERIC}; the hashes below will not match the published ones"
    fi
else
    warn "doomgeneric at ${DG_DIR} is not a git checkout; cannot say which commit was built"
fi

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

step "building ${DOOM_SRC}"
make -C "${DOOM_SRC}" wasm

mkdir -p "${DEST_DIR}"
for name in "${ARTIFACTS[@]}"; do
    src="${DOOM_SRC}/build/${name}"
    [ -f "${src}" ] || die "the build did not produce ${src}"
    cp -- "${src}" "${DEST_DIR}/${name}"
done

report

# ---------------------------------------------------------------------------
# The WAD, which is a separate artifact with a separate provenance
# ---------------------------------------------------------------------------

WAD="${DOOM_SRC}/wad/freedoom1.wad"
echo
if [ -f "${WAD}" ]; then
    echo "The game data, fetched at runtime rather than built:"
    echo
    printf '  %-14s %10s bytes  %s\n' \
        "freedoom1.wad" "$(wc -c < "${WAD}")" "$(sha256_of "${WAD}")"
    echo
    echo "  It is Freedoom Phase 1, as its own project publishes it, unmodified."
    echo "  ${DOOM_SRC}/fetch-wad.sh downloads it. Serve it next to the three"
    echo "  artifacts above, under the name the page asks start() for."
else
    echo "No WAD on disk. Fetch it with ${DOOM_SRC}/fetch-wad.sh before serving."
fi
