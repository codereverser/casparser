"""Unit tests for the small reusable helpers that survived v1.0:

- `casparser.parsers._classify.get_transaction_type`
- `casparser.parsers._classify.get_parsed_scheme_name`
- `casparser.parsers._isin.isin_search`
"""

from decimal import Decimal

from casparser.enums import TransactionType
from casparser.parsers._classify import (
    get_parsed_scheme_name,
    get_transaction_type,
)
from casparser.parsers._isin import isin_search


class TestTransactionType:
    def test_basic_types(self):
        assert get_transaction_type("Redemption", Decimal("-100")) == (
            TransactionType.REDEMPTION,
            None,
        )
        assert get_transaction_type("Address updated", None) == (
            TransactionType.MISC,
            None,
        )
        assert get_transaction_type("***STT paid ***", None) == (
            TransactionType.STT_TAX,
            None,
        )
        assert get_transaction_type("***stamp duty***", None) == (
            TransactionType.STAMP_DUTY_TAX,
            None,
        )
        assert get_transaction_type("*** TDS on Above ***", None) == (
            TransactionType.TDS_TAX,
            None,
        )
        assert get_transaction_type(
            "Creation of units - Segregated portfolio",
            Decimal("1"),
        ) == (TransactionType.SEGREGATION, None)

    def test_unknown_zero_units(self):
        assert get_transaction_type("***Random text***", Decimal("0")) == (
            TransactionType.UNKNOWN,
            None,
        )

    def test_reversal(self):
        assert get_transaction_type(
            "Purchase SIPCheque Dishonoured - Instalment No 108",
            Decimal("-1"),
        ) == (TransactionType.REVERSAL, None)

    def test_dividends(self):
        assert get_transaction_type(
            "IDCW Reinvestment @ Rs.2.00 per unit",
            Decimal("1"),
        ) == (TransactionType.DIVIDEND_REINVEST, Decimal("2.00"))
        assert get_transaction_type(
            "IDCW Reinvested @ Rs.0.0241 per unit",
            Decimal("1"),
        ) == (TransactionType.DIVIDEND_REINVEST, Decimal("0.0241"))
        assert get_transaction_type(
            "IDCW Paid @ Rs.0.06 per unit",
            Decimal("1"),
        ) == (TransactionType.DIVIDEND_PAYOUT, Decimal("0.06"))
        assert get_transaction_type(
            "Div. Reinvested @ Rs.0.0241 per unit",
            Decimal("1"),
        ) == (TransactionType.DIVIDEND_REINVEST, Decimal("0.0241"))


class TestParsedSchemeName:
    def test_passthrough(self):
        assert (
            get_parsed_scheme_name("Axis Long Term Equity Fund - Direct Growth")
            == "Axis Long Term Equity Fund - Direct Growth"
        )

    def test_trailing_whitespace(self):
        assert (
            get_parsed_scheme_name("Axis Bluechip Fund - Regular Growth ")
            == "Axis Bluechip Fund - Regular Growth"
        )

    def test_formerly_known_as_stripped(self):
        assert (
            get_parsed_scheme_name(
                "HSBC Corporate Bond Fund - Regular Growth "
                "(Formerly known as L&T Triple Ace Bond Fund - Growth)"
            )
            == "HSBC Corporate Bond Fund - Regular Growth"
        )

    def test_erstwhile_stripped(self):
        assert (
            get_parsed_scheme_name(
                "Bandhan ELSS Tax saver Fund-Growth-(Regular Plan)"
                "(erstwhile Bandhan Tax Advantage ELSS Fund-Growth-Regular Plan)"
            )
            == "Bandhan ELSS Tax saver Fund-Growth-(Regular Plan)"
        )

    def test_non_demat_stripped(self):
        assert (
            get_parsed_scheme_name(
                "Bandhan Liquid Fund-Growth-(Regular Plan) "
                "(erstwhile IDFC Cash Fund-Growth-Regular Plan) (Non-Demat) "
            )
            == "Bandhan Liquid Fund-Growth-(Regular Plan)"
        )


class TestISINSearch:
    def test_kfintech_lookup(self):
        isin, amfi, scheme_type = isin_search(
            "Axis Long Term Equity Fund - Direct Growth",
            "KFINTECH",
            "128TSDGG",
        )
        assert isin == "INF846K01EW2"
        assert amfi == "120503"
        assert scheme_type == "EQUITY"

    def test_no_match(self):
        isin, amfi, scheme_type = isin_search("", "KARVY", "")
        assert isin is None
        assert amfi is None
        assert scheme_type is None
