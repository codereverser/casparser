class ParserException(Exception):
    """Generic parser error."""


class HeaderParseError(ParserException):
    """Error while parsing header."""


class CASParseError(ParserException):
    """Error while parsing pdf file."""
