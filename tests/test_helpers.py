"""Unit tests for the small reusable helpers that survived v1.0:

- `casparser.parsers._classify.get_transaction_type`
- `casparser.parsers._classify.get_parsed_scheme_name`
- `casparser.parsers._isin.isin_search`
"""

from decimal import Decimal

from casparser.enums import TransactionType
from casparser.parsers._classify import (
    extract_gift_folio,
    get_parsed_scheme_name,
    get_transaction_type,
)
from casparser.parsers._isin import isin_search
from casparser.parsers.cams_detailed import (
    DATE_CELL_RE,
    FOLIO_LINE_RE,
    _reconcile_balances,
)
from casparser.types import Scheme, SchemeValuation, TransactionData


def _is_folio_header(text: str) -> bool:
    """Mirror of the folio-header guard in `cams_detailed.parse`: a real
    folio header matches FOLIO_LINE_RE and is *not* a dated transaction row."""
    return bool("Folio No" in text and not DATE_CELL_RE.match(text) and FOLIO_LINE_RE.search(text))


def _scheme(open_bal, close_bal, rows):
    """Build a minimal Scheme from (units, balance) rows for reconciliation
    tests. `units`/`balance` may be None."""
    txns = [
        TransactionData(
            date="2021-01-%02d" % (i + 1),
            description="txn",
            units=u,
            balance=b,
            type=TransactionType.PURCHASE,
        )
        for i, (u, b) in enumerate(rows)
    ]
    return Scheme(
        scheme="Test Fund - Direct Growth",
        rta_code="T123",
        rta="CAMS",
        open=Decimal(open_bal),
        close=Decimal(close_bal),
        close_calculated=Decimal(close_bal),
        valuation=SchemeValuation(date="2021-12-31", nav=Decimal("10"), value=Decimal("0")),
        transactions=txns,
    )


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

    def test_gifts(self):
        # Inter-folio gift transfers. Donor side carries negative units,
        # recipient side positive. Both CAMS/KFin punctuation variants
        # ("-TO Folio No:" and " - To Folio No.") must classify on the
        # "gift" keyword + units sign, not be mistaken for redemptions /
        # purchases. (issue #134)
        assert get_transaction_type(
            "Gifting of units-TO Folio No: 12345678901",
            Decimal("-4085.662"),
        ) == (TransactionType.GIFT_OUT, None)
        assert get_transaction_type(
            "Gifting of units - To Folio No.87654321",
            Decimal("-8224.686"),
        ) == (TransactionType.GIFT_OUT, None)
        # Recipient side — positive units, phrasing-agnostic.
        assert get_transaction_type(
            "Gifting of units-FROM Folio No: 12345678901",
            Decimal("4085.662"),
        ) == (TransactionType.GIFT_IN, None)

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
        # Regression: "reinvest" appearing before the IDCW/Div anchor, or
        # separated from it by punctuation, used to leak through as PAYOUT
        # because the inline `(reinvest)*` group never backtracked to capture.
        assert get_transaction_type(
            "Reinvestment of IDCW @ Rs.0.0241 per unit",
            Decimal("1"),
        ) == (TransactionType.DIVIDEND_REINVEST, Decimal("0.0241"))
        assert get_transaction_type(
            "IDCW - Reinvest @ Rs.0.06 per unit",
            Decimal("1"),
        ) == (TransactionType.DIVIDEND_REINVEST, Decimal("0.06"))


class TestFolioHeaderGuard:
    """A gift transaction names the destination folio in its description,
    so its row contains "Folio No:" and matches FOLIO_LINE_RE. It must not
    be treated as a folio boundary — doing so dropped the row and, when a
    scheme's own folio number was redacted/blank, the whole scheme. (#134)"""

    def test_genuine_folio_headers_match(self):
        assert _is_folio_header("Folio No: 12345678901 PAN: ABCDE1234F KYC: OK PAN: OK")
        assert _is_folio_header("Folio No: 12124203 / 63 KYC: OK")

    def test_gift_rows_are_not_folio_headers(self):
        # Both punctuation variants seen in CAMS/KFin statements.
        assert not _is_folio_header(
            "14-Nov-2025 Gifting of units-TO Folio No: 12345678901 "
            "(547,682.99) (4,085.662) 134.05 0.000"
        )
        assert not _is_folio_header(
            "20-Nov-2025 Gifting of units - To Folio No.87654321 "
            "(776,558.40) (8,224.686) 94.4180 0.000"
        )


class TestExtractGiftFolio:
    """The counterparty folio is pulled from a gift description for
    cross-CAS linking. Both RTA punctuations must parse. (#134)"""

    def test_kfin_colon(self):
        assert extract_gift_folio("Gifting of units-TO Folio No: 12345678901") == "12345678901"

    def test_cams_dot(self):
        assert extract_gift_folio("Gifting of units - To Folio No.87654321") == "87654321"

    def test_incoming(self):
        assert extract_gift_folio("Gifting of units-FROM Folio No: 99887766554") == "99887766554"

    def test_no_folio(self):
        assert extract_gift_folio("Purchase") is None
        assert extract_gift_folio("") is None


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


class TestReconcileBalances:
    def test_clean_scheme_has_no_warnings(self):
        # open 0; +100 -> 100; +50 -> 150; close 150. Fully reconciled.
        s = _scheme(0, "150", [(Decimal("100"), Decimal("100")), (Decimal("50"), Decimal("150"))])
        assert _reconcile_balances(s) == []

    def test_zero_unit_rows_are_skipped(self):
        # An STT row (no units) prints the unchanged balance — not a gap.
        s = _scheme(
            0,
            "100",
            [(Decimal("100"), Decimal("100")), (None, Decimal("100"))],
        )
        assert _reconcile_balances(s) == []

    def test_dropped_mid_row_is_flagged_once(self):
        # Rows print 100 then 300, but only +100 of units is recorded
        # between them — a ~100-unit row was dropped. Exactly one warning,
        # and it resyncs (no cascade onto the final row).
        s = _scheme(
            0,
            "300",
            [(Decimal("100"), Decimal("100")), (Decimal("100"), Decimal("300"))],
        )
        warns = _reconcile_balances(s)
        assert len(warns) == 1
        assert "discontinuity" in warns[0]

    def test_closing_mismatch_flagged_when_no_row_balance_exposes_it(self):
        # Final row carries units but no printed balance; computed running
        # (150) disagrees with the printed close (200) -> closing warning.
        s = _scheme(
            0,
            "200",
            [(Decimal("100"), Decimal("100")), (Decimal("50"), None)],
        )
        warns = _reconcile_balances(s)
        assert len(warns) == 1
        assert "closing unit balance mismatch" in warns[0]


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
