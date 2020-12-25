from decimal import Decimal

import pytest

from casparser.exceptions import HeaderParseError
from casparser.process import parse_header, get_transaction_type
from casparser.enums import TransactionType


class TestProcessClass:
    def test_header_parser(self):
        good_header = "Consolidated Account Statement\n01-Apr-2018 To 31-Mar-2019"
        bad_header = "Consolidated Account Statement\n01-Apr-2018"

        header_data = parse_header(good_header)
        assert header_data == {"from": "01-Apr-2018", "to": "31-Mar-2019"}

        with pytest.raises(HeaderParseError):
            parse_header(bad_header)

    def test_transaction_type(self):
        assert get_transaction_type("Redemption", Decimal(-100.0)) == (TransactionType.REDEMPTION, None)
        assert get_transaction_type("Address updated", None) == (TransactionType.MISC, None)
        assert get_transaction_type("***STT paid ***", None) == (TransactionType.TAX, None)
        assert get_transaction_type("***Random text***", Decimal(0.0)) == (TransactionType.UNKNOWN, None)
