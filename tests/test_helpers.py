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
        # A failed-SIP "payment not received" row carries negative units
        # but is a reversal of a provisional purchase, not a redemption.
        assert get_transaction_type(
            "SIP Purchase151/Payment not received from investor Banker "
            "Physical - Instalment No 1",
            Decimal("-1.365"),
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


class TestBalanceSignFix:
    """Cover `_apply_balance_sign_fix`, the running-balance sign
    validator that catches cosmetic-parens sign mis-parses (notably
    the KFin Franklin `Payment - Units Extinguished-Reversed` rows
    whose parenthesised units cell hides a semantically positive
    value)."""

    @staticmethod
    def _scheme(open_, transactions):
        from casparser.types import Scheme, SchemeValuation

        return Scheme(
            scheme="dummy",
            rta="CAMS",
            rta_code="X",
            type="EQUITY",
            open=open_,
            close=Decimal(0),
            close_calculated=Decimal(0),
            valuation=SchemeValuation(date="1970-01-01", nav=Decimal(0), value=Decimal(0)),
            transactions=transactions,
        )

    @staticmethod
    def _txn(units, balance, desc="Payment", amount=None):
        from casparser.types import TransactionData

        return TransactionData(
            date="2021-03-30",
            description=desc,
            amount=amount,
            units=units,
            nav=Decimal("1"),
            balance=balance,
            type=TransactionType.REDEMPTION,
        )

    def test_flips_sign_when_balance_says_so(self):
        """Franklin-shaped row: parsed units have wrong sign, but
        running balance unambiguously requires the opposite. The
        validator must flip units (and amount) AND reclassify."""
        from casparser.parsers.cams_detailed import _apply_balance_sign_fix

        scheme = self._scheme(
            open_=Decimal("558.456"),
            transactions=[
                # Mis-parsed: units shown as -171.447 (parenthesised in
                # PDF), but balance jumps UP by 171.447 — so semantic
                # sign is positive.
                self._txn(
                    units=Decimal("-171.447"),
                    balance=Decimal("729.903"),
                    desc="Payment - Units Extinguished-Reversed",
                    amount=Decimal("-5126.75"),
                ),
            ],
        )
        _apply_balance_sign_fix(scheme)
        t = scheme.transactions[0]
        assert t.units == Decimal("171.447")
        assert t.amount == Decimal("5126.75")
        # Type must be re-derived from the flipped sign; positive
        # units + a non-SIP/non-switch/non-segregat description maps
        # to PURCHASE in the classifier's positive branch.
        assert t.type == TransactionType.PURCHASE
        # close_calculated reflects the corrected sum.
        assert scheme.close_calculated == Decimal("729.903")

    def test_no_op_when_sign_already_correct(self):
        """A vanilla negative-units redemption whose balance check
        succeeds must be left untouched (sign, amount, type)."""
        from casparser.parsers.cams_detailed import _apply_balance_sign_fix

        scheme = self._scheme(
            open_=Decimal("1000"),
            transactions=[
                self._txn(
                    units=Decimal("-100"),
                    balance=Decimal("900"),
                    desc="Redemption",
                    amount=Decimal("-3000"),
                ),
            ],
        )
        _apply_balance_sign_fix(scheme)
        t = scheme.transactions[0]
        assert t.units == Decimal("-100")
        assert t.amount == Decimal("-3000")
        assert t.type == TransactionType.REDEMPTION
        assert scheme.close_calculated == Decimal("900")

    def test_skips_rows_without_units_or_balance(self):
        """STT / Stamp / TDS rows have no units and don't change the
        running balance; the validator must skip them and carry the
        previous balance forward to the next checkable row."""
        from casparser.parsers.cams_detailed import _apply_balance_sign_fix

        scheme = self._scheme(
            open_=Decimal("1000"),
            transactions=[
                # Stamp duty: no units, balance unchanged
                self._txn(
                    units=None,
                    balance=Decimal("1000"),
                    desc="*** Stamp Duty ***",
                ),
                # Then a real (sign-correct) redemption
                self._txn(
                    units=Decimal("-100"),
                    balance=Decimal("900"),
                    desc="Redemption",
                ),
            ],
        )
        _apply_balance_sign_fix(scheme)
        # Stamp row untouched
        assert scheme.transactions[0].units is None
        # Redemption row sign correct — left alone
        assert scheme.transactions[1].units == Decimal("-100")
        assert scheme.transactions[1].type == TransactionType.REDEMPTION

    def test_leaves_row_untouched_when_neither_sign_matches(self):
        """If the printed balance disagrees with both +units and
        -units, the validator must leave the row alone — something
        else upstream is wrong and we can't tell which value to
        trust."""
        from casparser.parsers.cams_detailed import _apply_balance_sign_fix

        scheme = self._scheme(
            open_=Decimal("1000"),
            transactions=[
                # 1000 + 50 != 999, 1000 - 50 != 999 — both fail
                self._txn(
                    units=Decimal("50"),
                    balance=Decimal("999"),
                    desc="Mystery",
                ),
            ],
        )
        _apply_balance_sign_fix(scheme)
        t = scheme.transactions[0]
        assert t.units == Decimal("50")
