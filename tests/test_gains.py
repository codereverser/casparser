from datetime import date
from decimal import Decimal

import pytest

from casparser.enums import TransactionType
from casparser.exceptions import GainsError
from casparser.analysis.gains import get_fund_type, FundType, MergedTransaction, FIFOUnits, Fund
from casparser.analysis.utils import CII, get_fin_year


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
            {
                "date": "2020-01-01",
                "description": "Purchase",
                "amount": 10000.00,
                "units": 1000,
                "nav": 10,
                "balance": 1000.00,
                "type": TransactionType.PURCHASE.name,
                "dividend_rate": None,
            },
        ]
        assert get_fund_type(transactions) == FundType.UNKNOWN

        transactions.append(
            {
                "date": "2020-01-01",
                "description": "Redemption",
                "amount": -5100.00,
                "units": -100,
                "nav": 11,
                "balance": 900.00,
                "type": TransactionType.REDEMPTION.name,
                "dividend_rate": None,
            },
        )
        assert get_fund_type(transactions) == FundType.DEBT

        transactions.append(
            {
                "date": "2020-02-01",
                "description": "***STT paid***",
                "amount": 0.26,
                "units": None,
                "nav": None,
                "balance": None,
                "type": TransactionType.STT_TAX.name,
                "dividend_rate": None,
            }
        )
        assert get_fund_type(transactions) == FundType.EQUITY

    def test_merge_transaction(self):
        dt = date(2000, 1, 1)
        mt = MergedTransaction(dt)

        mt.add(
            {
                "date": dt,
                "description": "Segregation",
                "amount": None,
                "units": Decimal("1000.000"),
                "nav": None,
                "balance": Decimal("1000.000"),
                "type": TransactionType.SEGREGATION.value,
                "dividend_rate": None,
            }
        )
        assert mt.sale_units == Decimal("0.00")
        assert mt.purchase_units == Decimal("1000.000")
        assert mt.nav == Decimal("0.00")
        assert mt.purchase == Decimal("0.00")
        assert mt.sale == Decimal("0.00")
        assert mt.tds == Decimal("0.00")

        mt.add(
            {
                "date": dt,
                "description": "***TDS on above***",
                "amount": Decimal("1.25"),
                "units": None,
                "nav": None,
                "balance": Decimal("1000.000"),
                "type": TransactionType.TDS_TAX.value,
                "dividend_rate": None,
            }
        )
        assert mt.tds == Decimal("1.25")

    def test_gains_error(self):
        test_fund = Fund("demo fund", "INF123456789", "EQUITY")
        dt = date(2000, 1, 1)
        transactions = [
            {
                "date": dt,
                "description": "***Redemption***",
                "amount": Decimal("-5000.00"),
                "units": Decimal("-100.000"),
                "nav": Decimal("50.000"),
                "balance": Decimal("500.00"),
                "type": TransactionType.REDEMPTION.value,
                "dividend_rate": None,
            }
        ]
        with pytest.raises(GainsError):
            FIFOUnits(test_fund, transactions)
