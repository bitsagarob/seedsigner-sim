"""
Build the SeedQR videos that Chromium's fake camera plays.

Three of them. The plain SeedQR carries 48 ASCII digits; the CompactSeedQR
carries 16 raw entropy bytes, which is the case that breaks if anything in the
chain treats a payload as text. Both encode the same seed, so both runs must end
on the same fingerprint. The third is a blank wall, for the tests that need to
know what the wallet reports when there is nothing to see.

Chromium takes --use-file-for-fake-video-capture=<file.y4m>, and y4m is plain
uncompressed frames behind a text header, so it is written here directly rather
than shelling out to ffmpeg. That keeps the test to one dependency and keeps it
deterministic: the same seed comes out every run, which is what makes a
screenshot worth anything as proof.

The one dependency is the wallet's own vendored qrcode, taken out of the built
wallet.zip, so the QR under test is drawn by the same library SeedSigner uses to
draw one. mnemonic comes from there too, for the same reason. Nothing is
installed from the network for this.

The seed is the standard BIP39 test vector "army van defense ...". Nothing about
it is secret and nothing should ever hold value.
"""

import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

# 12 words as 4-digit wordlist indices, which is what a SeedQR encodes.
SEEDQR = "009619290459027909561866076403330559110710760423"
MNEMONIC = ("army van defense carry jealous true "
            "garbage claim echo media make crunch")

WIDTH, HEIGHT = 640, 480

# Chromium loops the file, so the run is: nothing to see, then the QR. The blank
# lead-in is what proves the scanner waits rather than reporting the first thing
# it is handed, and it leaves a window in which to photograph the live preview.
LEAD_IN_FRAMES = 50  # 2s at 25fps
QR_FRAMES = 50

# Broadcast-safe black and white. Full-range 0/255 also decodes, but staying
# inside 16-235 means nothing downstream can clip the QR into mush.
BLACK, WHITE, CHROMA = 16, 235, 128

# zipimport cannot open the wordlist file mnemonic reads at import time, so the
# two packages have to come out onto disk rather than being imported in place.
VENDORED = ("qrcode/", "mnemonic/")


def vendored_libraries(wallet_zip: str) -> str:
    """Unpack the wallet's qrcode and mnemonic into a temp dir on sys.path."""
    target = tempfile.mkdtemp(prefix="seedsigner-sim-qr-")
    with zipfile.ZipFile(wallet_zip) as archive:
        members = [n for n in archive.namelist() if n.startswith(VENDORED)]
        if not members:
            raise SystemExit(f"{wallet_zip} carries no qrcode/mnemonic; is it a wallet.zip?")
        archive.extractall(target, members)
    sys.path.insert(0, target)
    return target


def compact_payload() -> bytes:
    """The same bytes CompactSeedQrEncoder would emit: indices as a bit string,
    checksum bits dropped, packed back into bytes."""
    from mnemonic import Mnemonic

    wordlist = Mnemonic("english").wordlist
    bits = "".join(bin(wordlist.index(w))[2:].zfill(11) for w in MNEMONIC.split())
    bits = bits[:-(len(MNEMONIC.split()) // 3)]
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def qr_matrix(payload) -> list:
    import qrcode

    # bytes rather than str is what makes qrcode pick byte mode, which is the
    # whole point of a CompactSeedQR.
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.get_matrix()


def luma_plane(matrix: list) -> bytearray:
    modules = len(matrix)
    scale = min(WIDTH, HEIGHT) * 3 // 4 // modules
    size = modules * scale
    left = (WIDTH - size) // 2
    top = (HEIGHT - size) // 2

    plane = bytearray([WHITE]) * (WIDTH * HEIGHT)
    for row in range(size):
        source = matrix[row // scale]
        offset = (top + row) * WIDTH + left
        for col in range(size):
            if source[col // scale]:
                plane[offset + col] = BLACK
    return plane


def write_y4m(path: str, payload) -> None:
    qr_plane = luma_plane(qr_matrix(payload))
    blank_plane = bytearray([WHITE]) * (WIDTH * HEIGHT)
    chroma = bytes([CHROMA]) * (WIDTH // 2 * HEIGHT // 2)

    with open(path, "wb") as out:
        out.write(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F25:1 Ip A1:1 C420\n".encode())
        for plane in ([blank_plane] * LEAD_IN_FRAMES) + ([qr_plane] * QR_FRAMES):
            out.write(b"FRAME\n")
            out.write(plane)
            out.write(chroma)
            out.write(chroma)


def write_blank_y4m(path: str) -> None:
    """A camera pointed at nothing. Anything the wallet reports from this is
    something it invented."""
    blank_plane = bytearray([WHITE]) * (WIDTH * HEIGHT)
    chroma = bytes([CHROMA]) * (WIDTH // 2 * HEIGHT // 2)
    with open(path, "wb") as out:
        out.write(f"YUV4MPEG2 W{WIDTH} H{HEIGHT} F25:1 Ip A1:1 C420\n".encode())
        for _ in range(LEAD_IN_FRAMES):
            out.write(b"FRAME\n")
            out.write(blank_plane)
            out.write(chroma)
            out.write(chroma)


def main(argv) -> int:
    directory = argv[1] if len(argv) > 1 else harness.ARTIFACT_DIR
    os.makedirs(directory, exist_ok=True)

    wallet_zip = os.environ.get("WALLET_ZIP") or harness.find_asset("wallet.zip")
    if not wallet_zip or not os.path.exists(wallet_zip):
        print("no wallet.zip: run build/build-wallet-zip.sh first, or set WALLET_ZIP",
              file=sys.stderr)
        return 2

    unpacked = vendored_libraries(wallet_zip)
    try:
        compact = compact_payload()
        write_y4m(os.path.join(directory, "qr.y4m"), SEEDQR)
        write_y4m(os.path.join(directory, "qr-compact.y4m"), compact)
        write_blank_y4m(os.path.join(directory, "qr-blank.y4m"))
    finally:
        shutil.rmtree(unpacked, ignore_errors=True)

    print(f"wrote qr.y4m, qr-compact.y4m and qr-blank.y4m to {directory}")
    print(f"  {LEAD_IN_FRAMES} blank frames, then {QR_FRAMES} carrying the QR")
    print(f"  qrcode   from {wallet_zip}")
    print(f"  seedqr   {SEEDQR}")
    print(f"  compact  {compact.hex()} ({len(compact)} bytes)")
    print(f"  mnemonic {MNEMONIC}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
