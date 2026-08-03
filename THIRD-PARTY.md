# Third-party components

Almost none of the code that runs in this simulator was written for it. The
wallet is upstream SeedSigner, unmodified. The Python interpreter is Pyodide.
The QR decoder is jsQR. Everything the wallet imports is somebody else's
library, pinned to a version and fetched from its own upstream.

This file lists all of it: what it is, which version or commit, where it comes
from, and under what licence. Anything not listed here was written for this
repository and is covered by this repository's own licence.

Third-party code reaches the browser by exactly three routes, and each one is
checkable in a different way:

| Route | What it is | How to check it |
| --- | --- | --- |
| Committed to this repository | `src/web/jsQR.js`, and nothing else | `sha256sum -c build/checksums.txt` |
| Fetched at deploy time | The Pyodide runtime and the compiled wheels it loads | `./build/fetch-assets.sh --check` |
| Built into `wallet.zip` | SeedSigner and its pure-Python dependencies | `./build/build-wallet-zip.sh`, then compare the sha256 |

The third route is the one that matters most, because `wallet.zip` is the code
that touches your seed. It is not committed. Build it yourself and compare the
hash to the one being served; if they match, the served file is what this
document says it is.

---

## 1. Committed to this repository

### jsQR 1.4.0, Apache-2.0

* File: `src/web/jsQR.js`
* Source: npm `jsqr@1.4.0`, the file published as `package/dist/jsQR.js`
* sha256: `bc40c8a15196236b2314db0856f72ca0b49980cd5413b8c852a7349f5fee0859`
* Upstream: https://github.com/cozmo/jsQR

Unmodified. Confirm it independently, rather than just against
`build/checksums.txt`:

```
curl -sL https://registry.npmjs.org/jsqr/-/jsqr-1.4.0.tgz \
  | tar xzO package/dist/jsQR.js | sha256sum
```

This is the camera seam. Upstream SeedSigner decodes QR codes with `pyzbar`,
which binds the C library libzbar and therefore cannot exist in this
environment; jsQR does the decoding in JavaScript instead and hands the result
to the wallet through `src/shims/browser_camera.py`.

---

## 2. Fetched by `build/fetch-assets.sh`

### Pyodide 0.26.4, MPL-2.0

* Upstream: https://github.com/pyodide/pyodide
* Artifact: `pyodide-core-0.26.4.tar.bz2` from the 0.26.4 GitHub release
* sha256: `70dba93432f3653155998cc9001f9c200182343c2f95165a2f9e9e4673fa35e8`
* CPython 3.12.1, emscripten 3.1.58, Pyodide ABI `2024_0`

About 26 MB of prebuilt WebAssembly, deliberately not committed. The version is
not a guess: it is the `info.version` field of the `pyodide-lock.json` that the
deployed simulator serves, and `fetch-assets.sh` re-reads that field after
unpacking and refuses to continue if it disagrees with its pin.

### The packages Pyodide loads at boot

`src/web/wallet-worker.js` calls `loadPackage(["Pillow", "pycryptodome",
"cryptography"])`. Those three, and everything they depend on, are compiled
extensions: they cannot be vendored into `wallet.zip`, because only Pyodide can
build a CPython extension for emscripten. `fetch-assets.sh` resolves the
dependency edges out of `pyodide-lock.json` rather than hardcoding them, and
verifies each file against the sha256 recorded there.

| Package | Version | Licence |
| --- | --- | --- |
| Pillow | 10.2.0 | HPND |
| pycryptodome | 3.20.0 | BSD-2-Clause, with parts in the public domain |
| cryptography | 42.0.5 | Apache-2.0 OR BSD-3-Clause |
| cffi | 1.16.0 | MIT |
| pycparser | 2.22 | BSD-3-Clause |
| openssl | 1.1.1n (see below) | OpenSSL License / SSLeay License (dual) |
| six | 1.16.0 | MIT |

Two things about that table are worth knowing rather than assuming:

* **The openssl version label is Pyodide's, and it is wrong.** The lock file
  and the filename both say `1.1.1n`, but Pyodide's own build recipe for 0.26.4
  (`packages/openssl/meta.yaml`) fetches `openssl-1.1.1w.tar.gz`. The artifact
  is a build of 1.1.1w carrying a 1.1.1n label. The licence is the same either
  way (the dual OpenSSL/SSLeay licence used throughout the 1.1.1 series), but
  the version string in `pyodide-lock.json` should not be quoted as fact. The
  artifact itself contains only `libcrypto.so` and `libssl.so`, so there is no
  licence file inside it to read; this entry is from the OpenSSL project's own
  terms for that series.
* **`cryptography` here is 42.0.5, not the 45.0.5 upstream SeedSigner pins.**
  Pyodide 0.26.4 ships what it ships. See section 4.

---

## 3. Built into `wallet.zip` by `build/build-wallet-zip.sh`

Everything in this section is pure Python and is redistributed inside
`wallet.zip`. Each one's licence text travels with it, in `licenses/` at the top
level of the zip, alongside a `licenses/MANIFEST.txt` that repeats the table
below. The build script's own dependency table carries the URL and sha256 of
every artifact it fetches.

### The wallet

**SeedSigner, MIT**

* Repository: https://github.com/3rdIteration/seedsigner
* Commit: `662d9dba2327eb77d6924ae9bd62d4902bf24634` (tag `SeSi-0.8.7+ShSi-B11`)
* In the zip as: `seedsigner/`, `main.py`, `LICENSE.md`

Verbatim, byte for byte, from `src/seedsigner` and `src/main.py` at that commit.
Nothing in this repository patches it. The pin lives in `UPSTREAM`; the build
script reads it from there and aborts if the checkout lands anywhere else.

To check the copy in a built zip against upstream directly:

```
git clone https://github.com/3rdIteration/seedsigner.git upstream
git -C upstream checkout 662d9dba2327eb77d6924ae9bd62d4902bf24634
mkdir extracted && cd extracted && unzip -q ../wallet.zip && cd ..
diff -rq upstream/src/seedsigner extracted/seedsigner
```

### The dependencies

Versions follow upstream's `requirements.txt` at the pinned commit, except where
noted.

| Module in the zip | Distribution | Version / commit | Licence |
| --- | --- | --- | --- |
| `base58` | base58 | 2.1.1 | MIT |
| `certifi` | certifi | 2025.7.14 | MPL-2.0 |
| `ecdsa` | ecdsa | 0.19.1 | MIT |
| `embit` | embit | 0.8.0 | MIT |
| `mnemonic` | mnemonic | 0.21 | MIT |
| `ndef` | ndeflib | 0.3.3 | ISC |
| `OpenSSL` | pyOpenSSL | 25.1.0 | Apache-2.0 |
| `pgpy` | PGPy, 3rdIteration fork | `7cdad000a76ced53c873211241d5ba20019a8488` | BSD-3-Clause |
| `pyaes` | pyaes | 1.6.1 | MIT |
| `pyasn1` | pyasn1 | 0.6.2 | BSD-2-Clause |
| `pygp` | PyGP, 3rdIteration fork | `15682ec8fd042b5d0ae3422e9434e9734db6e55b` | LGPL-3.0 |
| `pysatochip` | pysatochip, 3rdIteration fork | `d77e311e0cd39193c9b2c03a1ab5f69421b8f4d5` (tag `0.6a`) | LGPL-3.0 |
| `qrcode` | qrcode | 7.3.1 | BSD-3-Clause |
| `shamir_mnemonic` | shamir-mnemonic | 0.3.0 | MIT |
| `six.py` | six | 1.17.0 | MIT |
| `specter_card` | specter-card | `06dcde629cdc1057934b434afc46d822c2d2425d` | MIT |
| `typing_extensions.py` | typing_extensions | 4.14.1 | PSF-2.0 |
| `urtypes` | urtypes | `7fb280eab3b3563dfc57d2733b0bf5cbc0a96a6a` | MIT |

Sources for the commit-pinned ones:

* PGPy: https://github.com/3rdIteration/PGPy
* PyGP: https://github.com/3rdIteration/pygp
* pysatochip: https://github.com/3rdIteration/pysatochip, tag `0.6a`
* specter-card: https://github.com/3rdIteration/specter-javacard, subdirectory `py/`
* urtypes: https://github.com/selfcustody/urtypes, subdirectory `src/`

Upstream pins four of those (PGPy, PyGP, specter-card and urtypes) as GitHub
archive `.zip` URLs. The build script checks out the same commits with git
instead. A GitHub archive URL names a snapshot of a commit, but the zip wrapped
around it is generated on demand and its bytes are not promised to be stable, so
hashing that zip would pin GitHub's archiver rather than the source. A commit sha
pins the source itself, and git verifies it on arrival.

**pysatochip is the exception in the table above**, and the only dependency whose
pin does not come from `requirements.txt`. That file asks PyPI for
`pysatochip==0.17.0`; the device does not use it. The SeedSigner OS image builds
`3rdIteration/pysatochip` from GitHub at the tag `0.6a` through buildroot
(`opt/external-packages/python-pysatochip/python-pysatochip.mk`, at the
seedsigner-os tag whose name matches this firmware's) and deletes
`requirements.txt` from the rootfs. The two are not the same code: that tag calls
itself pysatochip 0.17.4 in its own `version.py`, and it has the
`0xC1: 'Descriptor'` entry the PyPI release lacks. This simulator ships what the
device ships, so it fails where the device fails and not somewhere else.

`qrcode`'s licence file is BSD-3-Clause for the package and additionally carries
the MIT notice of `pyqrnative`, which parts of it were forked from.

### Two pins that are ours, not upstream's

`base58` and `mnemonic` are imported by the wallet but are not in upstream's
`requirements.txt` at all:

* `base58`: not imported directly by the wallet (`bip38.py` uses embit's own `base58` submodule). Shipped because upstream's environment provides it and removing it has not been tested.
* `mnemonic`: `seedsigner/views/seed_views.py` and
  `seedsigner/views/smartcard_views.py` do `from mnemonic import Mnemonic`

On a real SeedSigner these arrive as transitive dependencies. Nothing declares
them, so nothing pins them. This build pins them explicitly at the versions in
the table; if you are diffing against upstream's requirements file, that is why
they are not there.

### Not a dependency: the fake smartcard package

`wallet.zip` also contains `smartcard/`, which is `src/smartcard/` from this
repository. It is not third-party and it is not pyscard: it is a fake card that
deliberately shadows pyscard's module name, so that unmodified SeedSigner code
calling `import smartcard` reaches it. It is one of the four hardware seams.

---

## 4. Pinned by upstream, deliberately not shipped

Leaving something out is a decision, so here is the list and the reason for each.

| Upstream pin | Why it is not in `wallet.zip` |
| --- | --- |
| `Pillow` | Compiled. Pyodide's build is loaded at boot instead. |
| `pycryptodomex` 3.23.0 | Compiled, and Pyodide has no `pycryptodomex`. The worker maps the `Cryptodome` namespace onto Pyodide's `pycryptodome`, so imports resolve. |
| `cryptography` 45.0.5 | Compiled. Pyodide 0.26.4 ships 42.0.5 and that is what runs. |
| `cffi` 1.17.1, `pycparser` 2.22 | Compiled, or a dependency of something compiled. Pyodide supplies both. |
| `pyscard` 2.3.1 | A C extension binding PC/SC. Replaced by this repo's fake `smartcard/`. |
| `pyzbar` | Binds libzbar. `decode_qr.py` imports it inside a `try` and sets it to `None`; jsQR does the decoding. |
| `smbus2` 0.4.3, `periphery` | I2C and GPIO. There is no `/dev/i2c` in a browser. `battery_hat.py` guards both imports, so omitting them is what makes the simulator correctly report "no battery HAT" rather than fail later trying to talk to one. |
| `colorama` 0.4.6 | Marked Windows-only by upstream. |
| `pyasn1` 0.6.2 | Actually **is** shipped, listed here only because it is easy to miss: `seedsigner/helpers/smartpgp/highlevel.py` imports it at module scope. |

Three of upstream's pins are met at a different version than upstream asks for,
because Pyodide decides: `cryptography` 42.0.5 rather than 45.0.5, `cffi` 1.16.0
rather than 1.17.1, and `Pillow` 10.2.0 rather than `>=10.4.0`. There is no way
to satisfy those pins in this environment without building Pyodide from source.
Anything that depends on behaviour introduced after those versions will behave
differently here than on a real SeedSigner.

---

## 5. Copyleft obligations

Two of the redistributed dependencies are LGPL-3.0: **pysatochip** and **PyGP**.
Both are shipped as unmodified Python source inside `wallet.zip`, which is what
the licence asks for: the corresponding source is the artifact. Neither is
modified by this project, and both remain replaceable: the zip is a plain
archive, and swapping either package for your own build needs nothing more than
rebuilding it.

**certifi** (MPL-2.0) and **Pyodide** (MPL-2.0) are likewise unmodified. Under
MPL-2.0 the obligation attaches to the covered files themselves, and those files
are shipped verbatim.

---

## 6. Known limitation: `pygp` cannot import

`pygp` is shipped because upstream pins it, but it cannot currently be imported
in the simulator. `pygp/connection/pcsc/__init__.py` does
`from smartcard.scard import *`, and `scard` is pyscard's C extension; the fake
`smartcard/` package in this repository has no such submodule, so `import pygp`
raises `ImportError`.

This is contained rather than fatal: every site in `seedsigner/views/
smartcard_views.py` that imports `pygp` does so inside a function, within a
`try`/`except Exception`, so the GlobalPlatform card-management screens report an
error instead of crashing the wallet. Nothing else in the wallet is affected.

It is left in the zip on purpose. The gap is in the smartcard seam, not in the
build, and if `src/smartcard/` grows a `scard` submodule then `pygp` starts
working with no change to the build at all.
