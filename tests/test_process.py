from decimal import Decimal

import pytest

from casparser.exceptions import CASParseError, HeaderParseError
from casparser.process import process_cas_text
from casparser.process.cas_detailed import parse_header, get_transaction_type
from casparser.process.cas_summary import parse_header as parse_summary_header
from casparser.enums import TransactionType


class TestProcessClass:
    def test_detailed_header_parser(self):
        good_header = "Consolidated Account Statement\n01-Apr-2018 To 31-Mar-2019"
        bad_header = "Consolidated Account Statement\n01-Apr-2018"

        header_data = parse_header(good_header)
        assert header_data == {"from": "01-Apr-2018", "to": "31-Mar-2019"}

        with pytest.raises(HeaderParseError):
            parse_header(bad_header)

    def test_summary_header_parser(self):
        good_header = "Consolidated Account Summary\nAs On 01-Apr-2018"
        bad_header = "Consolidated Account Summary\n01-Apr-2018"

        header_data = parse_summary_header(good_header)
        assert header_data == {"date": "01-Apr-2018"}

        with pytest.raises(HeaderParseError):
            parse_summary_header(bad_header)

    def test_process_bad_cas(self):
        with pytest.raises(CASParseError):
            process_cas_text("")

    def test_transaction_type(self):
        assert get_transaction_type("Redemption", Decimal(-100.0)) == (
            TransactionType.REDEMPTION,
            None,
        )
        assert get_transaction_type("Address updated", None) == (TransactionType.MISC, None)
        assert get_transaction_type("***STT paid ***", None) == (TransactionType.TAX, None)
        assert get_transaction_type("***Random text***", Decimal(0.0)) == (
            TransactionType.UNKNOWN,
            None,
        )
