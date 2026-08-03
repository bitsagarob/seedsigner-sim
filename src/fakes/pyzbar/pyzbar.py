"""
Stand-in for pyzbar, so that stock SeedSigner's `from pyzbar import pyzbar`
succeeds.

pyzbar binds the zbar C library and there is no WebAssembly build of it. The
smartcard fork wraps this same import in try/except and sets the name to None;
stock does not, so the import has to find something.

decode() is never reached. It is called from exactly one place, DecodeQR's
extract_qr_data, and browser_camera.py replaces that method: decoding happens in
JavaScript, against the page's own copy of the frame, and the payload comes back
over the SharedArrayBuffer. Returning an empty list rather than raising is the
honest answer for a decoder that was handed an image and found nothing, so if
the replacement ever failed to install, the scanner would report seeing no QR
rather than inventing one.
"""


class ZBarSymbol:
    """Only QRCODE is ever named, and only to be passed straight back to us."""

    QRCODE = 64


def decode(image, symbols=None, binary=False):
    return []
