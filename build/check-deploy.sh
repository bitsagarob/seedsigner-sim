#!/usr/bin/env bash
#
# Compare what a deployment actually serves with what this repository says it
# should serve. Reports; never deploys, never touches a served file.
#
#   ./build/check-deploy.sh
#
# This exists because a deployment here is a copy of files into a directory on
# more than one machine, and a copy is easy to do halfway. Three ways it went
# wrong in one day, all of which this catches:
#
#   * the page was changed to fetch wallet-<firmware>.zip and only the page and
#     the worker were copied, so the fetch landed on the site's 404 page and
#     Pyodide reported "not a zip file". Nothing said so until somebody looked;
#   * files reached one box and not the other, twice, so which copy a visitor
#     got depended on which box answered;
#   * a rename turned a URL a published article tells readers to curl into a
#     404, which is worse than it sounds: that command is the one thing that
#     proves the served zip is the pinned build.
#
# So there are four questions here, and each is asked of every box:
#
#   1. is every file the page needs there, and are its bytes this repository's?
#   2. do the two served zips hash to what UPSTREAM publishes?
#   3. does every URL the served pages and scripts name actually resolve?
#   4. do the boxes serve the same bytes as each other?
#
# Each box is asked over HTTPS from inside itself (curl --resolve to loopback),
# not by reading its filesystem: what is on disk is not the question, what nginx
# hands a visitor is. That also means a vhost pointed at the wrong directory, or
# a file the web server cannot read, shows up here.
#
# Requires: bash, curl, sha256sum (or shasum), and ssh to any box that is not
# this one. No venv, nothing to install, nothing to build -- except the two
# build-info.json files, which are build output and are compared against
# build/out. Run build/build-wallet-zip.sh first, or those two report that they
# have nothing to compare against.
#
# Pyodide is deliberately not compared byte for byte: it is 26 MB per box of
# somebody else's release, build/fetch-assets.sh already hash-checks it where it
# is fetched, and an absent or half-copied one shows up in question 3 anyway,
# because the worker names pyodide/pyodide.js.

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# Where the deployment is, and which machines hold a copy of it. Both are
# overridable so that this is useful to anyone who self-hosts: point
# SIM_DEPLOY_URL at your own site and list your own boxes.
#
# A box is NAME:HOW. HOW is "local" for the machine this script runs on, or
# "ssh" for one reached with `ssh NAME` -- so the name has to be one ssh can
# resolve, from a config or from DNS. The first box listed is the one the others
# are compared against in question 4.
SITE_URL="${SIM_DEPLOY_URL:-https://bitsaga.be/seedsigner-simulator}"
read -r -a BOXES <<< "${SIM_DEPLOY_BOXES:-vps2:local vps1:ssh}"

SITE_URL="${SITE_URL%/}"

usage() {
    cat <<'USAGE'
Usage: check-deploy.sh [-h]

Compares a deployment with this repository and reports. Deploys nothing.

Environment:
  SIM_DEPLOY_URL     Where the deployment is
                     (default: https://bitsaga.be/seedsigner-simulator)
  SIM_DEPLOY_BOXES   Space-separated NAME:HOW, HOW being local or ssh
                     (default: "vps2:local vps1:ssh")
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    "")        ;;
    *)         echo "unexpected argument: $1" >&2; usage >&2; exit 2 ;;
esac

if command -v sha256sum >/dev/null 2>&1; then
    sha256_of() { sha256sum -- "$1" | cut -d' ' -f1; }
else
    command -v shasum >/dev/null 2>&1 || { echo "no sha256 tool found" >&2; exit 2; }
    sha256_of() { shasum -a 256 -- "$1" | cut -d' ' -f1; }
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/seedsigner-sim-check.XXXXXXXX")"
trap 'rm -rf -- "${WORK_DIR}"' EXIT

# ---------------------------------------------------------------------------
# What has to be there
# ---------------------------------------------------------------------------
#
# This is the deploy table in docs/SELF-HOSTING.md, written so a machine can
# check it. Columns, pipe-separated:
#
#   served    the path under the site URL
#   source    what in this repository it must equal, if anything
#   mode      repo      byte identical to `source`
#             upstream  hash equal to the [SECTION] wallet_zip_sha256 in
#                       UPSTREAM, named in `source` -- the zips are build
#                       output, so UPSTREAM is what publishes them and what a
#                       visitor is asked to compare against
#             local     may be a deployment's own, so it is not compared to the
#                       repository; it still has to be there, its references
#                       still have to resolve, and the boxes still have to agree
#
# index.html is the one `local` row. bitsaga.be serves its own landing page
# there, on purpose, and a check that failed on that forever would be a check
# nobody reads. Everything the wallet itself loads is `repo`: a customised one
# of those is not a re-skin, it is a different simulator.

FILES='
index.html|src/web/index.html|local
wallet.html|src/web/wallet.html|repo
wallet-worker.js|src/web/wallet-worker.js|repo
wallet-camera.js|src/web/wallet-camera.js|repo
wallet-cards.js|src/web/wallet-cards.js|repo
wallet-tutorial.js|src/web/wallet-tutorial.js|repo
signet-coordinator.js|src/web/signet-coordinator.js|repo
qr-encode.js|src/web/qr-encode.js|repo
ur-decode.js|src/web/ur-decode.js|repo
seedsigner-device.js|src/web/seedsigner-device.js|repo
jsQR.js|src/web/jsQR.js|repo
sw.js|src/web/sw.js|repo
manifest.json|src/web/manifest.json|repo
icon-192.png|src/web/icon-192.png|repo
icon-512.png|src/web/icon-512.png|repo
apple-touch-icon.png|src/web/apple-touch-icon.png|repo
browser_display.py|src/shims/browser_display.py|repo
browser_camera.py|src/shims/browser_camera.py|repo
browser_qr.py|src/shims/browser_qr.py|repo
wallet-smartcard.zip|smartcard|upstream
wallet-stock.zip|stock|upstream
wallet-smartcard.build-info.json|build/out/wallet-smartcard.build-info.json|repo
wallet-stock.build-info.json|build/out/wallet-stock.build-info.json|repo
'

# The served files whose own references are followed. HTML and the scripts the
# page loads; not jsQR.js, which is a quarter of a megabyte of minified vendor
# code and names nothing.
SCAN='index.html wallet.html wallet-worker.js wallet-camera.js wallet-cards.js wallet-tutorial.js signet-coordinator.js seedsigner-device.js sw.js'

# The zips and the build-info files are fetched by name per firmware, and the
# page builds those names in a template literal, so a reference extracted from
# it still has the placeholder in it.
FIRMWARES='smartcard stock'

# ---------------------------------------------------------------------------
# Talking to a box
# ---------------------------------------------------------------------------
#
# Three one-liners run on the box itself, so that "what does this machine
# serve" is answered by that machine's own web server rather than by DNS, which
# only ever points at one of them. The --resolve pins the request to loopback
# while leaving the hostname alone, so TLS and the vhost still match.

SH_HASHES='set -u; r="$1"; shift; for u in "$@"; do t=$(mktemp); c=$(curl -sS --resolve "$r" -o "$t" -w "%{http_code}" "$u"); printf "%s %s %s\n" "$c" "$(sha256sum < "$t" | cut -d" " -f1)" "$u"; rm -f "$t"; done'
SH_BODY='set -u; curl -sS --resolve "$1" "$2"'
SH_HEADS='set -u; r="$1"; shift; for u in "$@"; do printf "%s %s\n" "$(curl -sS -I --resolve "$r" -o /dev/null -w "%{http_code}" "$u")" "$u"; done'

# The host and port to pin, out of the site URL.
SITE_HOSTPORT="${SITE_URL#*://}"
SITE_HOSTPORT="${SITE_HOSTPORT%%/*}"
case "${SITE_HOSTPORT}" in
    *:*) SITE_HOST="${SITE_HOSTPORT%%:*}"; SITE_PORT="${SITE_HOSTPORT##*:}" ;;
    *)   SITE_HOST="${SITE_HOSTPORT}"
         case "${SITE_URL}" in
             https://*) SITE_PORT=443 ;;
             http://*)  SITE_PORT=80 ;;
             *) echo "SIM_DEPLOY_URL must start with http:// or https://" >&2; exit 2 ;;
         esac ;;
esac
RESOLVE="${SITE_HOST}:${SITE_PORT}:127.0.0.1"

# on_box HOW NAME SCRIPT ARG...
on_box() {
    local how="$1" name="$2" script="$3"
    shift 3
    if [ "${how}" = "local" ]; then
        bash -s -- "$@" <<< "${script}"
    else
        ssh -o BatchMode=yes -o ConnectTimeout=10 "${name}" bash -s -- "$@" <<< "${script}"
    fi
}

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

PASSED=0
FAILED=0

pass() { PASSED=$((PASSED + 1)); printf '  PASS  %-34s %s\n' "$1" "${2:-}"; }
fail() { FAILED=$((FAILED + 1)); printf '  FAIL  %-34s %s\n' "$1" "${2:-}"; }

short() { printf '%.12s' "$1"; }

# ---------------------------------------------------------------------------
# What this repository says
# ---------------------------------------------------------------------------

UPSTREAM_FILE="${REPO_ROOT}/UPSTREAM"
[ -f "${UPSTREAM_FILE}" ] || { echo "missing ${UPSTREAM_FILE}" >&2; exit 2; }

# Section-aware, and the same awk program as build/build-wallet-zip.sh: UPSTREAM
# describes two firmwares and a parser that ignored the headers would hand back
# the other one's hash.
upstream_field() {
    awk -F= -v want="[$1]" -v key="$2" '
        /^\[/   { inside = ($0 == want); next }
        inside && $1 ~ "^[[:space:]]*" key "[[:space:]]*$" {
            gsub(/[[:space:]]/, "", $2); print $2
        }
    ' "${UPSTREAM_FILE}"
}

# What each served path must hash to, or "-" when nothing here can say.
expected_sha() {
    local source="$1" mode="$2"
    case "${mode}" in
        local)    echo "-" ;;
        upstream) upstream_field "${source}" wallet_zip_sha256 ;;
        repo)     if [ -f "${REPO_ROOT}/${source}" ]; then
                      sha256_of "${REPO_ROOT}/${source}"
                  else
                      echo "-"
                  fi ;;
    esac
}

# ---------------------------------------------------------------------------
# The references a served page names
# ---------------------------------------------------------------------------
#
# Every quoted string that ends in a file extension, minus the ones that are not
# a path on this site: absolute URLs, absolute paths, and the paths inside
# Pyodide's own filesystem that the worker's embedded Python writes to. The
# placeholder in wallet-${firmware}.zip is expanded to both firmwares, because
# the page really does fetch both names, one per visit.

QUOTES="\"'\`"

references_in() {
    local file="$1" ref
    grep -ohE "[${QUOTES}][^${QUOTES}]*\.(html|js|json|py|zip|png|wasm|woff2|css)[${QUOTES}]" "${file}" \
    | sed "s/^.//; s/.$//; s|^\./||" \
    | grep -vE '^(/|[a-zA-Z][a-zA-Z0-9+.-]*:)' \
    | while read -r ref; do
        case "${ref}" in
            *'${'*) for fw in ${FIRMWARES}; do
                        echo "${ref}" | sed -E "s/\\\$\{[A-Za-z_]+\}/${fw}/g"
                    done ;;
            *)      echo "${ref}" ;;
        esac
      done \
    | LC_ALL=C sort -u
}

# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

echo "seedsigner-sim deploy check"
echo "  site   ${SITE_URL}"
if git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    dirty=""
    [ -z "$(git -C "${REPO_ROOT}" status --porcelain)" ] || dirty=", uncommitted changes"
    echo "  repo   ${REPO_ROOT} at $(git -C "${REPO_ROOT}" rev-parse --short HEAD)${dirty}"
else
    echo "  repo   ${REPO_ROOT}"
fi
echo "  boxes  ${BOXES[*]}"

for box in "${BOXES[@]}"; do
    name="${box%%:*}"
    how="${box##*:}"

    echo
    echo "=== ${name} (${how}) ============================================"

    # --- one round trip for every file's status and hash ---------------------
    urls=()
    while IFS='|' read -r served source mode; do
        [ -n "${served}" ] || continue
        urls+=("${SITE_URL}/${served}")
    done <<< "${FILES}"

    if ! on_box "${how}" "${name}" "${SH_HASHES}" "${RESOLVE}" "${urls[@]}" \
            > "${WORK_DIR}/${name}.raw" 2>"${WORK_DIR}/${name}.err"; then
        fail "${name} is not answering" "$(head -n 2 "${WORK_DIR}/${name}.err" | tr '\n' ' ')"
        continue
    fi

    : > "${WORK_DIR}/${name}.hashes"

    echo
    echo "files, against this repository"
    while IFS='|' read -r served source mode; do
        [ -n "${served}" ] || continue

        line="$(awk -v u="${SITE_URL}/${served}" '$3 == u { print $1, $2 }' "${WORK_DIR}/${name}.raw")"
        code="${line%% *}"
        got="${line##* }"

        if [ "${code}" != "200" ]; then
            fail "${served}" "http ${code:-no answer}"
            continue
        fi

        # Recorded before any verdict, so question 4 can still compare two boxes
        # on a file neither of them matches the repository on.
        printf '%s  %s\n' "${got}" "${served}" >> "${WORK_DIR}/${name}.hashes"

        want="$(expected_sha "${source}" "${mode}")"
        case "${mode}" in
            local)
                pass "${served}" "$(short "${got}")  served, not compared: local override"
                ;;
            *)
                if [ "${want}" = "-" ]; then
                    fail "${served}" "nothing to compare against: no ${source} here (build it first)"
                elif [ "${want}" = "${got}" ]; then
                    if [ "${mode}" = "upstream" ]; then
                        pass "${served}" "$(short "${got}")  = UPSTREAM [${source}]"
                    else
                        pass "${served}" "$(short "${got}")"
                    fi
                else
                    fail "${served}" "served $(short "${got}"), want $(short "${want}")"
                fi
                ;;
        esac
    done <<< "${FILES}"

    # --- and the references those files name ---------------------------------
    echo
    echo "references, followed on the served pages"
    for page in ${SCAN}; do
        body="${WORK_DIR}/${name}.${page}"
        if ! on_box "${how}" "${name}" "${SH_BODY}" "${RESOLVE}" "${SITE_URL}/${page}" \
                > "${body}" 2>/dev/null; then
            fail "${page}" "could not be fetched"
            continue
        fi

        refs=()
        while read -r ref; do
            [ -n "${ref}" ] && refs+=("${SITE_URL}/${ref}")
        done < <(references_in "${body}")

        if [ "${#refs[@]}" -eq 0 ]; then
            pass "${page}" "names nothing on this site"
            continue
        fi

        broken="$(on_box "${how}" "${name}" "${SH_HEADS}" "${RESOLVE}" "${refs[@]}" \
                  | awk -v base="${SITE_URL}/" '$1 != "200" { sub(base, "", $2); print $2 " -> " $1 }')"
        if [ -z "${broken}" ]; then
            pass "${page}" "${#refs[@]} references, all resolve"
        else
            fail "${page}" "$(echo "${broken}" | tr '\n' ' ')"
        fi
    done
done

# ---------------------------------------------------------------------------
# And whether the boxes agree
# ---------------------------------------------------------------------------
#
# Separate from the comparisons above because it answers a different question.
# Two boxes can both be wrong about the repository and still be consistent with
# each other, and two boxes that disagree are worse than either being wrong:
# then what a visitor gets depends on which one answered.

echo
echo "=== the boxes against each other ================================"
echo
first="${BOXES[0]%%:*}"
for box in "${BOXES[@]:1}"; do
    name="${box%%:*}"
    if [ ! -s "${WORK_DIR}/${first}.hashes" ] || [ ! -s "${WORK_DIR}/${name}.hashes" ]; then
        fail "${first} vs ${name}" "one of them served nothing to compare"
        continue
    fi
    differ="$(diff "${WORK_DIR}/${first}.hashes" "${WORK_DIR}/${name}.hashes" \
              | awk '/^[<>]/ { print $3 }' | LC_ALL=C sort -u | tr '\n' ' ')"
    if [ -z "${differ}" ]; then
        pass "${first} vs ${name}" "$(wc -l < "${WORK_DIR}/${first}.hashes" | tr -d ' ') files, identical bytes"
    else
        fail "${first} vs ${name}" "differ: ${differ}"
    fi
done

echo
if [ "${FAILED}" -eq 0 ]; then
    echo "${PASSED} checks passed."
    exit 0
fi

echo "${FAILED} of $((PASSED + FAILED)) checks failed."
echo
echo "Nothing here has been changed: this only reports. Fix a mismatch by"
echo "copying the file this repository has to every box, all of them, and"
echo "running this again -- see docs/SELF-HOSTING.md."
exit 1
