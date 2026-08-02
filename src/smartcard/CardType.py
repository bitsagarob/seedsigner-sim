from smartcard.simulated_card import SATOCHIP_ATR


class CardType:
    def matches(self, atr, reader=None):
        return list(atr) == SATOCHIP_ATR


class AnyCardType(CardType):
    def matches(self, atr, reader=None):
        return True


class ATRCardType(CardType):
    def __init__(self, atr=None, mask=None):
        self.atr = atr
        self.mask = mask

    def matches(self, atr, reader=None):
        return list(atr) == list(self.atr or [])
