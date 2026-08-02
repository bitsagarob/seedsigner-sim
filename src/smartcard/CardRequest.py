from smartcard import simulated_card
from smartcard.Exceptions import CardRequestTimeoutException


class CardRequest:
    """Waiting for a card, which with an empty reader means actually waiting.

    pyscard measures its timeout in seconds, where None waits forever and 0 looks
    once and gives up. pysatochip asks for 0 and reads the timeout exception as
    "no card present", which is how an empty reader gets reported instead of hung
    on. The waiting itself is in simulated_card.wait_for_card, which parks in
    slices so the page stays free to put a card in.
    """

    def __init__(self, newcardonly=False, cardType=None, timeout=1, readers=None):
        self.newcardonly = newcardonly
        self.cardType = cardType
        self.timeout = timeout
        self.readers = readers or []

    def waitforcard(self):
        card = simulated_card.wait_for_card(self.timeout)
        simulated_card.poll()
        if card is None:
            raise CardRequestTimeoutException()
        return simulated_card.SimulatedCardService(card)

    def waitforcardevent(self):
        simulated_card.poll()
        service = simulated_card.card_service()
        return [service] if service is not None else []
