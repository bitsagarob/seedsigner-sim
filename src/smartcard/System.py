from smartcard import simulated_card
from smartcard.simulated_card import SimulatedReader


def readers(groups=None):
    # One reader, always attached, whether or not there is a card in it.
    simulated_card.poll()
    return [SimulatedReader()]
