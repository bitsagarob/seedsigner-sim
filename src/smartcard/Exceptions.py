class SmartcardException(Exception):
    pass


class CardConnectionException(SmartcardException):
    pass


class CardRequestTimeoutException(SmartcardException):
    pass


class NoCardException(SmartcardException):
    pass
