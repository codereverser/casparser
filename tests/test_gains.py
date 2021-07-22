from datetime import date

import pytest

from casparser.enums import TransactionType
from casparser.analysis.gains import get_fund_type, FundType
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
        assert get_fund_type([]) == FundType.UNKNOWN

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
