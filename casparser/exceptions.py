class ParserException(Exception):
    """Generic parser error."""


class HeaderParseError(ParserException):
    """Error while parsing header."""


class CASParseError(ParserException):
    """Error while parsing pdf file."""


class IncorrectPasswordError(CASParseError):
    """Incorrect password error."""


class CASIntegrityError(ParserException):
    """Error while processing transactions"""


class IncompleteCASError(ParserException):
    """CAS is not complete for running analysis modules"""
