from datetime import date
from decimal import Decimal

import pytest

from casparser.analysis.gains import (
    CapitalGainsReport,
    FIFOUnits,
    Fund,
    FundType,
    GainEntry,
    GiftEntry,
    MergedTransaction,
    _fy_needs_transfer_col,
    _transfer_flag,
    get_fund_type,
)
from casparser.analysis.utils import CII, get_fin_year
from casparser.enums import CASFileType, FileType, TransactionType
from casparser.exceptions import GainsError
from casparser.types import (
    CASData,
    Folio,
    InvestorInfo,
    Scheme,
    SchemeValuation,
    StatementPeriod,
    TransactionData,
)


def _cas_with_transactions(transactions, *, scheme_type="EQUITY"):
    """Build a minimal CASData wrapping a single scheme with the given
    transactions (zero opening balance) for CapitalGainsReport tests."""
    scheme = Scheme(
        scheme="Gift Test Fund - Direct Growth",
        rta_code="G123",
        rta="KFINTECH",
        type=scheme_type,
        isin="INF123456789",
        open=Decimal("0"),
        close=Decimal("0"),
        close_calculated=Decimal("0"),
        valuation=SchemeValuation(date="2026-03-31", nav=Decimal("10"), value=Decimal("0")),
        transactions=transactions,
    )
    folio = Folio(folio="12345", amc="Test Mutual Fund", schemes=[scheme])
    cas = CASData(
        statement_period=StatementPeriod(from_="2021-04-01", to="2026-03-31"),
        folios=[folio],
        investor_info=InvestorInfo(name="X", email="x@x", address="x", mobile="x"),
        cas_type=CASFileType.DETAILED,
        file_type=FileType.KFINTECH,
    )
    return CapitalGainsReport(cas)


class TestGainsClass:
    def test_cii(self):
        # Invalid FY
        with pytest.raises(ValueError):
            CII["2000-01"]
        with pytest.raises(KeyError):
            CII["FY2001-05"]

        # Tests
        assert abs(CII["FY2020-21"] / CII["FY2001-02"] - 3.01) <= 1e-3

        # Officially CBDT-notified values for the most recent years.
        assert CII["FY2023-24"] == 348
        assert CII["FY2024-25"] == 363
        assert CII["FY2025-26"] == 376

        # Checks for out-of-range FYs
        today = date.today()
        future_date = date(today.year + 3, today.month, today.day)
        assert CII["FY1990-91"] == 100
        assert CII[get_fin_year(future_date)] == CII[CII._max_year]

    def test_fund_type(self):
        transactions = [
            TransactionData(
                date="2020-01-01",
                description="Purchase",
                amount=10000.00,
                units=1000,
                nav=10,
                balance=1000.00,
                type=TransactionType.PURCHASE,
                dividend_rate=None,
            )
        ]
        assert get_fund_type(transactions) == FundType.UNKNOWN

        transactions.append(
            TransactionData(
                date="2020-01-01",
                description="Redemption",
                amount=-5100.00,
                units=-100,
                nav=11,
                balance=900.00,
                type=TransactionType.REDEMPTION,
                dividend_rate=None,
            ),
        )
        assert get_fund_type(transactions) == FundType.DEBT

        transactions.append(
            TransactionData(
                date="2020-02-01",
                description="***STT paid***",
                amount=0.26,
                units=None,
                nav=None,
                balance=None,
                type=TransactionType.STT_TAX,
                dividend_rate=None,
            )
        )
        assert get_fund_type(transactions) == FundType.EQUITY

    def test_gift_out_is_not_a_redemption(self):
        """An outgoing gift carries negative units but is not a taxable
        transfer for the donor — it must not flag the fund as having
        redemptions (FundType stays UNKNOWN) nor produce capital gains. (#134)"""
        transactions = [
            TransactionData(
                date="2022-01-01",
                description="Purchase",
                amount=Decimal("10000.00"),
                units=Decimal("1000.000"),
                nav=Decimal("10"),
                balance=Decimal("1000.000"),
                type=TransactionType.PURCHASE,
                dividend_rate=None,
            ),
            TransactionData(
                date="2025-11-14",
                description="Gifting of units-TO Folio No: 12345678901",
                amount=Decimal("-50000.00"),
                units=Decimal("-1000.000"),
                nav=Decimal("50"),
                balance=Decimal("0.000"),
                type=TransactionType.GIFT_OUT,
                dividend_rate=None,
            ),
        ]
        # No real redemption -> fund type cannot be inferred.
        assert get_fund_type(transactions) == FundType.UNKNOWN
        # FIFO must not record any disposal for the gift.
        fund = Fund("Gift Fund", "123", "INF123456789", "EQUITY")
        fifo = FIFOUnits(fund, transactions)
        assert fifo.gains == []

    def test_merge_transaction(self):
        dt = date(2000, 1, 1)
        mt = MergedTransaction(dt)

        mt.add(
            TransactionData(
                date=dt,
                description="Segregation",
                amount=None,
                units=Decimal("1000.000"),
                nav=None,
                balance=Decimal("1000.000"),
                type=TransactionType.SEGREGATION.value,
                dividend_rate=None,
            )
        )
        assert mt.sale_units == Decimal("0.00")
        assert mt.purchase_units == Decimal("1000.000")
        assert mt.nav == Decimal("0.00")
        assert mt.purchase == Decimal("0.00")
        assert mt.sale == Decimal("0.00")
        assert mt.tds == Decimal("0.00")

        mt.add(
            TransactionData(
                date=dt,
                description="***TDS on above***",
                amount=Decimal("1.25"),
                units=None,
                nav=None,
                balance=Decimal("1000.000"),
                type=TransactionType.TDS_TAX,
                dividend_rate=None,
            )
        )
        assert mt.tds == Decimal("1.25")

    def test_gains_error(self):
        test_fund = Fund("demo fund", "123", "INF123456789", "EQUITY")
        dt = date(2000, 1, 1)
        transactions = [
            TransactionData(
                date=dt,
                description="***Redemption***",
                amount=Decimal("-5000.00"),
                units=Decimal("-100.000"),
                nav=Decimal("50.000"),
                balance=Decimal("500.00"),
                type=TransactionType.REDEMPTION,
                dividend_rate=None,
            )
        ]
        with pytest.raises(GainsError):
            FIFOUnits(test_fund, transactions)

    def test_stamp_duty_split_lot_does_not_exceed_paid(self):
        """When a single purchase lot is consumed across multiple
        disposals, the sum of stamp duty allocated across those
        disposals must not exceed the original stamp paid.

        Regression guard for the FIFOUnits.sell bug where a
        partially-consumed lot was re-queued with the FULL original
        purchase_tax (instead of the unallocated remainder), causing
        the per-disposal proportional allocation to re-claim the
        same stamp on every subsequent disposal from the same lot.
        Worked example with this 3-way 100/100/100 split of a
        300-unit lot with ₹1.25 stamp:

        - Disposal 1: round(1.25 × 100/300, 2) = 0.42 (lot re-queued
          with remainder 0.83)
        - Disposal 2: round(0.83 × 100/200, 2) = 0.42 (lot re-queued
          with remainder 0.41)
        - Disposal 3: round(0.41 × 100/100, 2) = 0.41
        - Total claimed = 1.25 = exactly stamp paid ✓

        Under the pre-fix code the lot was re-queued with the full
        1.25 each time, producing a total of ~2.29 — an 84% over-
        claim that grows worse with split depth.
        """
        fund = Fund("Split-Lot Fund", "SL", "INF000SL0001", "EQUITY")
        purchase_dt = date(2020, 1, 1)
        transactions = [
            TransactionData(
                date=purchase_dt,
                description="Purchase",
                amount=Decimal("3000.00"),
                units=Decimal("300.000"),
                nav=Decimal("10.000"),
                balance=Decimal("300.000"),
                type=TransactionType.PURCHASE,
                dividend_rate=None,
            ),
            TransactionData(
                date=purchase_dt,
                description="*** Stamp Duty ***",
                amount=Decimal("1.25"),
                units=None,
                nav=None,
                balance=None,
                type=TransactionType.STAMP_DUTY_TAX,
                dividend_rate=None,
            ),
            # Three 100-unit redemptions on distinct dates (held > 1yr,
            # so they're LTCG; specific dates don't matter for the
            # stamp-allocation invariant).
            TransactionData(
                date=date(2022, 1, 1),
                description="Redemption",
                amount=Decimal("-2000.00"),
                units=Decimal("-100.000"),
                nav=Decimal("20.000"),
                balance=Decimal("200.000"),
                type=TransactionType.REDEMPTION,
                dividend_rate=None,
            ),
            TransactionData(
                date=date(2022, 2, 1),
                description="Redemption",
                amount=Decimal("-2000.00"),
                units=Decimal("-100.000"),
                nav=Decimal("20.000"),
                balance=Decimal("100.000"),
                type=TransactionType.REDEMPTION,
                dividend_rate=None,
            ),
            TransactionData(
                date=date(2022, 3, 1),
                description="Redemption",
                amount=Decimal("-2000.00"),
                units=Decimal("-100.000"),
                nav=Decimal("20.000"),
                balance=Decimal("0.000"),
                type=TransactionType.REDEMPTION,
                dividend_rate=None,
            ),
        ]
        fifo = FIFOUnits(fund, transactions)

        # Three disposals, three gain entries.
        assert len(fifo.gains) == 3

        total_stamp = sum((ge.stamp_duty for ge in fifo.gains), Decimal("0"))

        # Section 48 invariant: deductible transfer expense ≤ paid.
        assert total_stamp <= Decimal("1.25"), (
            f"Total stamp claimed ({total_stamp}) exceeds stamp paid (1.25); "
            f"per-disposal stamps were {[ge.stamp_duty for ge in fifo.gains]}"
        )
        # Stronger: with the remainder-aware re-queue the rounding
        # residual gets absorbed into the final disposal, so the
        # total equals the paid stamp exactly.
        assert total_stamp == Decimal("1.25")

    def test_failed_sip_payment_not_received_nets_to_zero(self):
        """A failed SIP (``Payment not received``) reverses both the
        units and the stamp duty on the same date. Modelled on a real
        CAMS DETAILED statement::

            SIP Purchase - Instalment 1/7        2,999.85   1.365  2,198.255
            *** Stamp Duty ***                        0.15
            SIP Purchase151/Payment not received  (2,999.85) (1.365) 2,198.255
            *** Stamp Duty ***                       (0.15)

        Two guarantees:

        - The reversal is classified ``REVERSAL`` (not ``REDEMPTION``),
          so net ``purchase_units`` for the date is 0 → no buy, no sell,
          and therefore **no phantom redemption** in the gains report.
        - The matching ``(0.15)`` stamp reversal nets the date's stamp
          duty to exactly 0, so **no stamp is ever claimed** for a
          purchase that never settled (a failed payment is not a §48
          transfer).
        """
        fund = Fund("Failed-SIP Fund", "FS", "INF000FS0001", "EQUITY")
        dt = date(2025, 9, 26)
        transactions = [
            TransactionData(
                date=dt,
                description="SIP Purchase - Instalment 1/7 - via myCAMS Online",
                amount=Decimal("2999.85"),
                units=Decimal("1.365"),
                nav=Decimal("2198.255"),
                balance=Decimal("3.843"),
                type=TransactionType.PURCHASE_SIP,
                dividend_rate=None,
            ),
            TransactionData(
                date=dt,
                description="*** Stamp Duty ***",
                amount=Decimal("0.15"),
                units=None,
                nav=None,
                balance=None,
                type=TransactionType.STAMP_DUTY_TAX,
                dividend_rate=None,
            ),
            TransactionData(
                date=dt,
                description=(
                    "SIP Purchase151/Payment not received from investor "
                    "Banker Physical - Instalment No 1"
                ),
                amount=Decimal("-2999.85"),
                units=Decimal("-1.365"),
                nav=Decimal("2198.255"),
                balance=Decimal("2.478"),
                type=TransactionType.REVERSAL,
                dividend_rate=None,
            ),
            TransactionData(
                date=dt,
                description="*** Stamp Duty ***",
                amount=Decimal("-0.15"),
                units=None,
                nav=None,
                balance=None,
                type=TransactionType.STAMP_DUTY_TAX,
                dividend_rate=None,
            ),
        ]
        fifo = FIFOUnits(fund, transactions)

        # Units and stamp both net to zero for the date.
        merged = fifo._merged_transactions[dt]
        assert merged.purchase_units == Decimal("0.000")
        assert merged.sale_units == Decimal("0")
        assert merged.stamp_duty == Decimal("0.00")

        # No phantom redemption, and nothing left in the FIFO queue.
        assert fifo.gains == []
        assert fifo.balance == Decimal("0")

        # No stamp duty claimed anywhere.
        total_stamp = sum((ge.stamp_duty for ge in fifo.gains), Decimal("0"))
        assert total_stamp == Decimal("0")


def _ltcg_entry(fy, fund, purchase_date, sale_date, units="100.000"):
    """Build a minimal LTCG GainEntry (EQUITY, held > 1yr) for the
    Schedule-112A tests. NAV lookup on the synthetic ISIN returns None,
    so fmv falls back to purchase_value."""
    return GainEntry(
        fy=fy,
        fund=fund,
        type="EQUITY",
        purchase_date=purchase_date,
        purchase_nav=Decimal("10.0"),
        purchase_value=Decimal("1000.00"),
        stamp_duty=Decimal("1.00"),
        sale_date=sale_date,
        sale_nav=Decimal("20.0"),
        sale_value=Decimal("2000.00"),
        stt=Decimal("2.00"),
        units=Decimal(units),
    )


def _report_with_gains(gains):
    """A CapitalGainsReport with `_gains` injected directly, bypassing
    the FIFO engine (exercised separately above)."""
    rep = CapitalGainsReport.__new__(CapitalGainsReport)
    rep._gains = gains
    rep.errors = []
    return rep


class TestSchedule112A:
    """Schedule 112A column-1b (23-Jul-2024 transfer split) compliance."""

    def test_transfer_flag(self):
        assert _transfer_flag(date(2024, 7, 22)) == "BE"
        assert _transfer_flag(date(2024, 7, 23)) == "AE"
        assert _transfer_flag(date(2024, 9, 1)) == "AE"
        assert _transfer_flag(date(2020, 1, 1)) == "BE"

    def test_fy_needs_transfer_col(self):
        assert _fy_needs_transfer_col("FY2024-25") is True
        assert _fy_needs_transfer_col("FY2025-26") is True
        assert _fy_needs_transfer_col("FY2023-24") is False
        assert _fy_needs_transfer_col("FY2020-21") is False
        assert _fy_needs_transfer_col("") is False

    def test_ae_lots_split_across_cutoff(self):
        """An after-31-Jan-2018-acquired fund sold both before and on/after
        23-Jul-2024 within FY2024-25 yields TWO consolidated 112A rows —
        one per transfer flag — instead of one merged row."""
        fund = Fund("Equity Fund", "F1", "INF000A01001", "EQUITY")
        gains = [
            # acquired 2022 (1a=AE); sold 01-Jun-2024 (1b=BE)
            _ltcg_entry("FY2024-25", fund, date(2022, 1, 1), date(2024, 6, 1)),
            # acquired 2022 (1a=AE); sold 01-Sep-2024 (1b=AE)
            _ltcg_entry("FY2024-25", fund, date(2022, 2, 1), date(2024, 9, 1)),
        ]
        rows = _report_with_gains(gains).generate_112a("FY2024-25")
        assert len(rows) == 2
        by_transferred = {r.transferred: r for r in rows}
        assert set(by_transferred) == {"BE", "AE"}
        assert all(r.acquired == "AE" for r in rows)

    def test_grandfathered_lot_keeps_per_row_transfer_flag(self):
        """A before-31-Jan-2018 (grandfathered) lot stays a separate row
        and carries its own transfer flag."""
        fund = Fund("Equity Fund", "F1", "INF000A01001", "EQUITY")
        gains = [
            _ltcg_entry("FY2024-25", fund, date(2017, 1, 1), date(2024, 9, 1)),
        ]
        rows = _report_with_gains(gains).generate_112a("FY2024-25")
        assert len(rows) == 1
        assert rows[0].acquired == "BE"
        assert rows[0].transferred == "AE"

    def test_csv_includes_1b_column_for_fy2024_25(self):
        fund = Fund("Equity Fund", "F1", "INF000A01001", "EQUITY")
        gains = [_ltcg_entry("FY2024-25", fund, date(2022, 1, 1), date(2024, 9, 1))]
        csv_data = _report_with_gains(gains).generate_112a_csv_data("FY2024-25")
        header = csv_data.splitlines()[0]
        assert "Share/Unit Transferred(1b)" in header
        # 1b sits between 1a and ISIN.
        cols = header.split(",")
        assert cols[0] == "Share/Unit acquired(1a)"
        assert cols[1] == "Share/Unit Transferred(1b)"
        assert cols[2] == "ISIN Code(2)"
        # First data row's 1b value is populated.
        first_row = csv_data.splitlines()[1].split(",")
        assert first_row[1] == "AE"

    def test_csv_omits_1b_column_for_older_fy(self):
        fund = Fund("Equity Fund", "F1", "INF000A01001", "EQUITY")
        gains = [_ltcg_entry("FY2021-22", fund, date(2019, 1, 1), date(2021, 6, 1))]
        csv_data = _report_with_gains(gains).generate_112a_csv_data("FY2021-22")
        header = csv_data.splitlines()[0]
        assert "Transferred(1b)" not in header
        cols = header.split(",")
        assert cols[0] == "Share/Unit acquired(1a)"
        assert cols[1] == "ISIN Code(2)"


class TestStampDutyInCostOfAcquisition:
    """Purchase-side stamp duty is part of the cost of acquisition.

    Both CAMS and KFin capital-gains statements report cost "inclusive
    of stamp duty"; omitting it over-states the realised gain by the
    stamp amount. See gains.GainEntry.acquisition_value.
    """

    def _entry(self):
        # purchase 1000.00 + stamp 1.00, sale 2000.00, held > 1yr (LTCG).
        fund = Fund("Equity Fund", "F1", "INF000A01001", "EQUITY")
        return _ltcg_entry("FY2024-25", fund, date(2022, 1, 1), date(2024, 9, 1))

    def test_gain_is_net_of_purchase_stamp_duty(self):
        ge = self._entry()
        # cost = purchase_value + stamp_duty = 1001; gain = 2000 - 1001.
        assert ge.acquisition_value == Decimal("1001.00")
        assert ge.gain == Decimal("999.00")
        assert ge.ltcg == Decimal("999.00")

    def test_coa_includes_stamp_duty(self):
        ge = self._entry()
        # AE-acquired equity: coa is the stamp-inclusive cost.
        assert ge.coa == Decimal("1001.00")
        assert ge.ltcg_taxable == Decimal("999.00")

    def test_112a_balance_includes_stamp_excludes_stt(self):
        """Schedule 112A: stamp duty folded into cost of acquisition,
        STT not deducted (it is not an allowable transfer expense)."""
        rows = _report_with_gains([self._entry()]).generate_112a("FY2024-25")
        assert len(rows) == 1
        row = rows[0]
        assert row.actual_coa == Decimal("1001.00")  # 1000 + 1 stamp
        assert row.expenditure == Decimal("0.00")  # STT excluded
        assert row.deductions == Decimal("1001.00")
        assert row.balance == Decimal("999.00")


class TestGifts:
    """Inter-folio gift transfers — disclosed informationally, never
    folded into capital gains. (issue #134)"""

    def _purchase(self):
        return TransactionData(
            date="2022-01-01",
            description="Purchase",
            amount=Decimal("10000.00"),
            units=Decimal("1000.000"),
            nav=Decimal("10"),
            balance=Decimal("1000.000"),
            type=TransactionType.PURCHASE,
            dividend_rate=None,
        )

    def _gift_out(self):
        return TransactionData(
            date="2025-11-14",
            description="Gifting of units-TO Folio No: 12345678901",
            amount=Decimal("-50000.00"),
            units=Decimal("-1000.000"),
            nav=Decimal("50"),
            balance=Decimal("0.000"),
            type=TransactionType.GIFT_OUT,
            dividend_rate=None,
            gift_folio="12345678901",
        )

    def test_gift_entry_from_transaction(self):
        fund = Fund("F", "12345", "INF123456789", "EQUITY")
        out = GiftEntry.from_transaction(fund, self._gift_out())
        assert out.direction == "OUT"
        # counterparty_folio is carried from the parsed transaction field.
        assert out.counterparty_folio == "12345678901"
        assert out.date == date(2025, 11, 14)
        assert out.fy == "FY2025-26"
        # Incoming direction.
        gift_in = TransactionData(
            date="2025-11-14",
            description="Gifting of units - From Folio No.87654321",
            amount=Decimal("50000.00"),
            units=Decimal("1000.000"),
            nav=Decimal("50"),
            balance=Decimal("1000.000"),
            type=TransactionType.GIFT_IN,
            dividend_rate=None,
            gift_folio="87654321",
        )
        ein = GiftEntry.from_transaction(fund, gift_in)
        assert ein.direction == "IN"
        assert ein.counterparty_folio == "87654321"

    def test_gift_out_disclosed_not_in_gains(self):
        report = _cas_with_transactions([self._purchase(), self._gift_out()])
        assert report.has_gifts() is True
        assert len(report.gifts) == 1
        assert report.gifts[0].direction == "OUT"
        # A pure gift-out produces no realised gain and no error.
        assert report.has_gains() is False
        assert report.has_error() is False
        assert "Direction" in report.get_gifts_csv_data()

    def test_recipient_resale_warns_and_excludes(self):
        """Gifted-in units later redeemed: FIFO can't price them (donor's
        basis is elsewhere), so the scheme is excluded with a specific,
        gift-aware message — never the generic FIFO mismatch."""
        gift_in = TransactionData(
            date="2024-01-01",
            description="Gifting of units-FROM Folio No: 12345678901",
            amount=Decimal("50000.00"),
            units=Decimal("1000.000"),
            nav=Decimal("50"),
            balance=Decimal("1000.000"),
            type=TransactionType.GIFT_IN,
            dividend_rate=None,
        )
        redemption = TransactionData(
            date="2025-06-01",
            description="Redemption",
            amount=Decimal("-60000.00"),
            units=Decimal("-1000.000"),
            nav=Decimal("60"),
            balance=Decimal("0.000"),
            type=TransactionType.REDEMPTION,
            dividend_rate=None,
        )
        report = _cas_with_transactions([gift_in, redemption])
        assert report.has_error() is True
        assert len(report.errors) == 1
        _, msg = report.errors[0]
        assert "gifted-in units" in msg
        assert "donor" in msg
        # The gift itself is still disclosed.
        assert report.has_gifts() is True
