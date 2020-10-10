class ParserException(Exception):
    pass


class HeaderParseError(ParserException):
    pass


class CASParseError(ParserException):
    pass
