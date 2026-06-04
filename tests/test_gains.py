from datetime import date
from decimal import Decimal

import pytest

from casparser.analysis.gains import (
    FIFOUnits,
    Fund,
    FundType,
    GainEntry,
    GainEntry112A,
    MergedTransaction,
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


class TestStampDutyInCostOfAcquisition:
    """Purchase-side stamp duty is part of the cost of acquisition.

    Both CAMS and KFin capital-gains statements report cost "inclusive
    of stamp duty"; omitting it over-states the realised gain by the
    stamp amount. See gains.GainEntry.acquisition_value.
    """

    def _entry(self):
        # purchase 1000.00 + stamp 1.00, sale 2000.00, held > 1yr (LTCG).
        # Synthetic ISIN -> nav_search returns None, so fmv falls back
        # to purchase_value.
        fund = Fund("Equity Fund", "F1", "INF000A01001", "EQUITY")
        return GainEntry(
            fy="FY2024-25",
            fund=fund,
            type="EQUITY",
            purchase_date=date(2022, 1, 1),
            purchase_nav=Decimal("10.0"),
            purchase_value=Decimal("1000.00"),
            stamp_duty=Decimal("1.00"),
            sale_date=date(2024, 9, 1),
            sale_nav=Decimal("20.0"),
            sale_value=Decimal("2000.00"),
            stt=Decimal("2.00"),
            units=Decimal("100.000"),
        )

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
        row = GainEntry112A(
            acquired="AE",
            isin="INF000A01001",
            name="Equity Fund",
            units=Decimal("100.000"),
            sale_nav=Decimal("20.0"),
            sale_value=Decimal("2000.00"),
            purchase_value=Decimal("1000.00"),
            fmv_nav=Decimal("0.0"),
            fmv=Decimal("0.0"),
            stt=Decimal("2.00"),
            stamp_duty=Decimal("1.00"),
        )
        assert row.actual_coa == Decimal("1001.00")  # 1000 + 1 stamp
        assert row.expenditure == Decimal("0.00")  # STT excluded
        assert row.deductions == Decimal("1001.00")
        assert row.balance == Decimal("999.00")
