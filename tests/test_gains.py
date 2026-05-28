from datetime import date
from decimal import Decimal

import pytest

from casparser.analysis.gains import (
    CapitalGainsReport,
    FIFOUnits,
    Fund,
    FundType,
    GainEntry,
    MergedTransaction,
    _fy_needs_transfer_col,
    _transfer_flag,
    get_fund_type,
)
from casparser.analysis.utils import CII, get_fin_year
from casparser.enums import TransactionType
from casparser.exceptions import GainsError
from casparser.types import TransactionData


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
