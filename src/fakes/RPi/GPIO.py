"""
Stand-in for RPi.GPIO, so that stock SeedSigner's `import RPi.GPIO` succeeds.

Stock's hardware/buttons.py imports this at module scope and, still at module
scope, reads RPI_INFO['P1_REVISION'] to choose between the 26-pin and 40-pin
header layouts. That is the only value here anything actually looks at: 3 is
what every Pi from the B+ onwards reports, which is the 40-pin layout the
firmware's own default pin assignments are written for.

Nothing else in this file is ever called. The worker replaces HardwareButtons'
methods and the display factory before the controller starts, so no code path
reaches a pin. The functions exist because a module that is imported for one
constant should still be shaped like the module it stands in for, rather than
failing with AttributeError somewhere far from here if that ever stops being
true.
"""

RPI_INFO = {"P1_REVISION": 3, "TYPE": "Pi Zero", "RAM": "512M"}

BOARD, BCM = 10, 11
IN, OUT = 1, 0
PUD_UP, PUD_DOWN, PUD_OFF = 22, 21, 20
HIGH, LOW = 1, 0
VERSION = "0.7.1"


def setmode(mode):
    pass


def setwarnings(flag):
    pass


def setup(*args, **kwargs):
    pass


def output(*args, **kwargs):
    pass


def cleanup(*args, **kwargs):
    pass


def input(pin):
    # Buttons are wired active-low with a pull-up, so HIGH is "not pressed".
    return HIGH
