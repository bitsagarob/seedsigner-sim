"""
Stand-in for pyscard.

pyscard is a C extension that binds to the host's PC/SC daemon. Neither exists
in a browser, so this package provides the same import surface, backed by
simulated SeedKeeper and Satochip cards that answer real APDUs. See
simulated_card.py, which holds the cards and the reader they go in and out of;
the modules here are the pyscard-shaped surface over it.

Everything above this layer, the whole of pysatochip and the whole of SeedSigner,
runs unmodified. Faking the transport rather than the wallet's own card calls is
what makes the demo exercise the real flows.

Deliberately shadows pyscard. This package is named `smartcard` because that is
the module name pyscard occupies, and the point is that pysatochip imports it
without knowing the difference. That is safe inside a browser, where pyscard
cannot exist, and unsafe anywhere else: on a real machine with a real reader,
importing this instead of pyscard would hand a caller a simulated card that
answers every APDU convincingly. Somebody could set a PIN believing they were
initialising the Satochip in their hand.

So it says so, loudly, unless it is running where it belongs. Set
SEEDSIGNER_SIM_ALLOW_FAKE_SMARTCARD=1 to silence it, which the test suite does;
nothing else should need to.
"""

import os
import sys
import warnings

if sys.platform != "emscripten" and not os.environ.get(
    "SEEDSIGNER_SIM_ALLOW_FAKE_SMARTCARD"
):
    warnings.warn(
        "seedsigner-sim's simulated 'smartcard' package has been imported outside "
        "the browser, shadowing pyscard. Any card it reports is fake and any PIN "
        "you set goes nowhere. If you meant to talk to a real reader, remove this "
        "package from sys.path.",
        RuntimeWarning,
        stacklevel=2,
    )
