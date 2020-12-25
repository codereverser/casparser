import pytest

from casparser.exceptions import HeaderParseError
from casparser.process import parse_header


class TestProcessClass:

    def test_header_parser(self):
        good_header = 'Consolidated Account Statement\n01-Apr-2018 To 31-Mar-2019'
        bad_header = 'Consolidated Account Statement\n01-Apr-2018'

        header_data = parse_header(good_header)
        assert header_data == {"from": "01-Apr-2018", "to": "31-Mar-2019"}

        with pytest.raises(HeaderParseError):
            parse_header(bad_header)