import csv
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from datetime import date
import io
import itertools
from typing import List

from dateutil.parser import parse as dateparse

from casparser.exceptions import IncompleteCASError
from casparser.enums import FundType, GainType, TransactionType
from casparser.types import CASParserDataType, TransactionDataType


@dataclass
class MergedTransaction:
    """Represent net transaction on a given date"""

    dt: date
    units: Decimal = Decimal(0.0)
    nav: Decimal = Decimal(0.0)
    amount: Decimal = Decimal(0.0)
    tax: Decimal = Decimal(0.0)


@dataclass
class Fund:
    """Fund details"""

    name: str
    isin: str

    def __le__(self, other: "Fund"):
        return self.name <= other.name

    def __ge__(self, other: "Fund"):
        return self.name >= other.name

    def __lt__(self, other: "Fund"):
        return self.name < other.name

    def __gt__(self, other: "Fund"):
        return self.name > other.name


@dataclass
class GainEntry:
    """Gain data of a realised transaction"""

    fy: str
    fund: Fund
    type: str
    buy_date: date
    buy_price: Decimal
    stamp_duty: Decimal
    sell_date: date
    sell_price: Decimal
    stt: Decimal
    units: Decimal
    ltcg: Decimal = Decimal(0.0)
    stcg: Decimal = Decimal(0.0)


def get_fund_type(transactions: List[TransactionDataType]) -> FundType:
    """
    Detect Fund Type.
    - UNKNOWN if there are no redemption transactions
    - EQUITY if STT_TAX transactions are present in the portfolio
    - DEBT if no STT_TAX transactions are present along with redemptions

    :param transactions: list of transactions for a single fund parsed from the CAS
    :return: type of fund
    """
    valid = any(
        [
            x["units"] is not None and x["units"] < 0 and x["type"] != TransactionType.REVERSAL.name
            for x in transactions
        ]
    )
    if not valid:
        return FundType.UNKNOWN
    return (
        FundType.EQUITY
        if any([x["type"] == TransactionType.STT_TAX.name for x in transactions])
        else FundType.DEBT
    )


class FIFOUnits:
    """First-In First-Out units calculator."""

    def __init__(self, fund: Fund, transactions: List[TransactionDataType]):
        """
        :param fund: name of fund, mainly for reporting purposes.
        :param transactions: list of transactions for the fund
        """
        self._fund: Fund = fund
        self._original_transactions = transactions
        self.fund_type = get_fund_type(transactions)
        self._merged_transactions = self.merge_transactions()

        self.transactions = deque()
        self.gains: List[GainEntry] = []

        self.process()

    @property
    def clean_transactions(self):
        """remove redundant transactions, without amount"""
        return filter(lambda x: x["amount"] is not None, self._original_transactions)

    def merge_transactions(self):
        """Group transactions by date with taxes and investments/redemptions separated."""
        merged_transactions = {}
        for txn in sorted(self.clean_transactions, key=lambda x: (x["date"], -x["amount"])):
            dt = txn["date"]

            if isinstance(dt, str):
                dt = dateparse(dt).date()

            if dt not in merged_transactions:
                merged_transactions[dt] = MergedTransaction(dt)
            if txn["type"] in (
                TransactionType.STT_TAX.name,
                TransactionType.STAMP_DUTY_TAX.name,
            ):
                merged_transactions[dt].tax += txn["amount"]
            else:
                merged_transactions[dt].nav = txn["nav"]
                merged_transactions[dt].units += txn["units"]
                merged_transactions[dt].amount += txn["amount"]
        return merged_transactions

    @staticmethod
    def get_fin_year(dt: date):
        """Get financial year representation."""
        if dt.month > 3:
            year1, year2 = dt.year, dt.year + 1
        else:
            year1, year2 = dt.year - 1, dt.year

        if year1 % 100 != 99:
            year2 %= 100

        return f"FY{year1}-{year2}"

    def get_gain_type(self, buy_date: date, sell_date: date):
        """Identify gain type based on the current fund type, buy and sell dates."""
        ltcg = {
            FundType.EQUITY: date(buy_date.year + 1, buy_date.month, buy_date.day),
            FundType.DEBT: date(buy_date.year + 3, buy_date.month, buy_date.day),
        }

        return GainType.LTCG if sell_date > ltcg[self.fund_type] else GainType.STCG

    def process(self):
        self.gains = []
        for dt in sorted(self._merged_transactions.keys()):
            txn = self._merged_transactions[dt]
            if txn.amount > 0:
                self.buy(dt, txn.units, txn.nav, txn.tax)
            elif txn.amount < 0:
                self.sell(dt, txn.units, txn.nav, txn.tax)
        return self.gains

    def buy(self, txn_date: date, quantity: Decimal, nav: Decimal, tax: Decimal):
        self.transactions.append((txn_date, quantity, nav, tax))

    def sell(self, sell_date: date, quantity: Decimal, nav: Decimal, tax: Decimal):
        fin_year = self.get_fin_year(sell_date)
        original_quantity = abs(quantity)
        pending_units = original_quantity
        while pending_units > 0:
            buy_date, units, buy_nav, buy_tax = self.transactions.popleft()

            gain_type = self.get_gain_type(buy_date, sell_date)
            if units <= pending_units:
                gain_units = units
            else:
                gain_units = pending_units

            buy_price = round(gain_units * buy_nav, 2)
            sell_price = round(gain_units * nav, 2)
            stamp_duty = round(buy_tax * gain_units / units, 2)
            stt = round(tax * gain_units / original_quantity, 2)

            pending_units -= units

            ge = GainEntry(
                fy=fin_year,
                fund=self._fund,
                type=self.fund_type.name,
                buy_date=buy_date,
                buy_price=buy_price,
                stamp_duty=stamp_duty,
                sell_date=sell_date,
                sell_price=sell_price,
                stt=stt,
                units=gain_units,
            )
            if gain_type == GainType.LTCG:
                ge.ltcg = round(sell_price - buy_price - stt, 2)
            elif gain_type == GainType.STCG:
                ge.stcg = round(sell_price - buy_price - stt, 2)
            self.gains.append(ge)
            if pending_units < 0 and buy_nav is not None:
                # Sale is partially matched against the last buy transactions
                # Re-add the remaining units to the FIFO queue
                self.transactions.appendleft((buy_date, -1 * pending_units, buy_nav, buy_tax))


class CapitalGainsReport:
    """Generate Capital Gains Report from the parsed CAS data"""

    def __init__(self, data: CASParserDataType):
        self._data: CASParserDataType = data
        self._gains: List[GainEntry] = []
        self.process_data()

    @property
    def gains(self) -> List[GainEntry]:
        return list(sorted(self._gains, key=lambda x: (x.fy, x.fund, x.sell_date)))

    def process_data(self):
        self._gains = []
        for folio in self._data.get("folios", []):
            for scheme in folio.get("schemes", []):
                name = f"{scheme['scheme']} [{folio['folio']}]"
                transactions = scheme["transactions"]
                if len(transactions) > 0:
                    if scheme["open"] >= 0.01:
                        raise IncompleteCASError(
                            "Incomplete CAS found. For gains computation, "
                            "all folios should have zero opening balance"
                        )
                    fifo = FIFOUnits(Fund(name=name, isin=scheme["isin"]), transactions)
                    self._gains.extend(fifo.gains)

    def get_summary(self):
        """Calculate capital gains summary"""
        summary = []
        for (fy, fund), txns in itertools.groupby(self.gains, key=lambda x: (x.fy, x.fund)):
            ltcg = stcg = Decimal(0.0)
            for txn in txns:
                ltcg += txn.ltcg
                stcg += txn.stcg
            summary.append([fy, fund.name, fund.isin, ltcg, stcg])
        return summary

    def get_summary_csv_data(self) -> str:
        """Return summary data as a csv string."""
        headers = ["FY", "Fund", "ISIN", "LTCG", "STCG"]
        with io.StringIO() as csv_fp:
            writer = csv.writer(csv_fp)
            writer.writerow(headers)
            for entry in self.get_summary():
                writer.writerow(entry)
            csv_fp.seek(0)
            csv_data = csv_fp.read()
            return csv_data

    def get_gains_csv_data(self) -> str:
        """Return details gains data as a csv string."""
        headers = [
            "FY",
            "Fund",
            "ISIN",
            "Type",
            "Units",
            "Buy Date",
            "Buy NAV",
            "Stamp Duty",
            "Sell Date",
            "Sell NAV",
            "STT",
            "LTCG",
            "STCG",
        ]
        with io.StringIO() as csv_fp:
            writer = csv.writer(csv_fp)
            writer.writerow(headers)
            for gain in self.gains:
                writer.writerow(
                    [
                        gain.fy,
                        gain.fund.name,
                        gain.fund.isin,
                        gain.type,
                        gain.units,
                        gain.buy_date,
                        gain.buy_price,
                        gain.stamp_duty,
                        gain.sell_date,
                        gain.sell_price,
                        gain.stt,
                        gain.ltcg,
                        gain.stcg,
                    ]
                )
            csv_fp.seek(0)
            csv_data = csv_fp.read()
            return csv_data
