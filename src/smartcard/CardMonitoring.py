from smartcard import simulated_card


class CardObserver:
    def update(self, observable, actions):
        pass


class CardMonitor:
    """Reports cards arriving in and leaving the simulated reader.

    pyscard watches the readers from a background thread and notifies observers
    from it. There is no such thread here -- see simulated_card.poll(), which
    does the same job on the caller's thread -- but the registration behaviour is
    the same: a newly added observer is handed the cards that are already in the
    readers, which is how pysatochip's RemovalObserver picks up a card that was
    inserted before the wallet started.
    """

    def __init__(self):
        self.observers = []

    def addObserver(self, observer):
        if observer not in self.observers:
            self.observers.append(observer)
        simulated_card.register_monitor(self)
        simulated_card.announce_present(self, observer)

    def deleteObserver(self, observer):
        if observer in self.observers:
            self.observers.remove(observer)
        if not self.observers:
            simulated_card.unregister_monitor(self)

    def deleteObservers(self):
        self.observers = []
        simulated_card.unregister_monitor(self)
