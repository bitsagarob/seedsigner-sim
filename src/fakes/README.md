# src/fakes

Two packages that exist so an `import` can succeed. Nothing in either of them is
ever called.

Stock SeedSigner reaches for two native libraries at module scope, with no
`try`/`except` around either:

- `seedsigner/hardware/buttons.py` does a bare `import RPi.GPIO as GPIO` and
  then evaluates `GPIO.RPI_INFO['P1_REVISION']` while the module is still being
  imported, to decide between the 26-pin and 40-pin numbering.
- `seedsigner/models/decode_qr.py` does `from pyzbar import pyzbar`. zbar is a C
  library and there is no WebAssembly build of it.

There is no GPIO and no zbar in a browser, and this repository does not patch
the wallet, so the imports have to find something. `/wallet` is first on
`sys.path`, so a package of the right name at the top level of the zip is what
they find.

## Why here and not in src/smartcard

`src/smartcard/` is a different kind of thing and lives in a different place for
that reason. It is a working simulation: it answers real APDUs, holds real keys,
and pysatochip runs against it unchanged, so it is part of what the smartcard
firmware *does*. These two are the opposite. They are shaped exactly like the
module the importer expects and no further, and if either one is ever reached at
runtime, something is wrong: `browser_display.py` and the worker have replaced
every button and panel path before `RPi.GPIO` could matter, and
`browser_camera.py` replaces `DecodeQR.extract_qr_data`, which is the only
function that would have called `pyzbar.decode`.

## Firmware-conditional

These two are staged into the stock zip only, and `src/smartcard/` into the
smartcard zip only. Neither firmware carries the other's stand-ins: the fork
guards its `pyzbar` import and identifies buttons by name rather than by GPIO
pin, and stock has no card code at all. See the `STAGE_PACKAGES` rows in
`build/build-wallet-zip.sh`.
