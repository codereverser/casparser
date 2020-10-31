class ParserException(Exception):
    """Generic parser error."""
    pass


class HeaderParseError(ParserException):
    """Error while parsing header"""
    pass


class CASParseError(ParserException):
    """Error while parsing pdf file"""
    pass
