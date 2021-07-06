import csv
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from datetime import date
import io
import itertools
from typing import List

from dateutil.parser import parse as dateparse
from dateutil.relativedelta import relativedelta

from casparser.exceptions import IncompleteCASError
from casparser.enums import FundType, GainType, TransactionType
from casparser.types import CASParserDataType, TransactionDataType
from .utils import CII, get_fin_year, nav_search


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
    type: str

    def __lt__(self, other: "Fund"):
        return self.name < other.name


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

    def __post_init__(self):
        self.__cutoff_date = date(2018, 1, 31)
        self.__sell_cutoff_date = date(2018, 4, 1)
        self.__update_nav()

    def __update_nav(self):
        self._cached_isin = self.fund.isin
        self._cached_nav = nav_search(self._cached_isin)

    @property
    def gain_type(self):
        """Identify gain type based on the current fund type, buy and sell dates."""
        ltcg = {
            FundType.EQUITY.name: self.buy_date + relativedelta(years=1),
            FundType.DEBT.name: self.buy_date + relativedelta(years=3),
        }

        return GainType.LTCG if self.sell_date > ltcg[self.type] else GainType.STCG

    @property
    def gain(self) -> Decimal:
        return Decimal(round(self.sell_price - self.buy_price, 2))

    @property
    def fmv(self) -> Decimal:
        if self.fund.isin != self._cached_isin:
            self.__update_nav()
        if self._cached_nav is None:
            return self.buy_price
        return self._cached_nav * self.units

    @property
    def index_ratio(self) -> Decimal:
        return Decimal(
            round(CII[get_fin_year(self.sell_date)] / CII[get_fin_year(self.buy_date)], 2)
        )

    @property
    def coa(self) -> Decimal:
        if self.fund.type == FundType.DEBT.name:
            return Decimal(round(self.buy_price * self.index_ratio, 2))
        if self.buy_date < self.__cutoff_date:
            if self.sell_date < self.__sell_cutoff_date:
                return self.sell_price
            return max(self.buy_price, min(self.fmv, self.sell_price))
        return self.buy_price

    @property
    def ltcg_taxable(self) -> Decimal:
        if self.gain_type == GainType.LTCG:
            return Decimal(round(self.sell_price - self.coa, 2))
        return Decimal(0.0)

    @property
    def ltcg(self) -> Decimal:
        if self.gain_type == GainType.LTCG:
            return self.gain
        return Decimal(0.0)

    @property
    def stcg(self) -> Decimal:
        if self.gain_type == GainType.STCG:
            return self.gain
        return Decimal(0.0)


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
        if fund.type not in ("EQUITY", "DEBT"):
            self.fund_type = get_fund_type(transactions)
        else:
            self.fund_type = getattr(FundType, fund.type)
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
        fin_year = get_fin_year(sell_date)
        original_quantity = abs(quantity)
        pending_units = original_quantity
        while pending_units > 0:
            buy_date, units, buy_nav, buy_tax = self.transactions.popleft()

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
                    fifo = FIFOUnits(
                        Fund(name=name, isin=scheme["isin"], type=scheme["type"]), transactions
                    )
                    self._gains.extend(fifo.gains)

    def get_summary(self):
        """Calculate capital gains summary"""
        summary = []
        for (fy, fund), txns in itertools.groupby(self.gains, key=lambda x: (x.fy, x.fund)):
            ltcg = stcg = ltcg_taxable = Decimal(0.0)
            for txn in txns:
                ltcg += txn.ltcg
                stcg += txn.stcg
                ltcg_taxable += txn.ltcg_taxable
            summary.append([fy, fund.name, fund.isin, fund.type, ltcg, ltcg_taxable, stcg])
        return summary

    def get_summary_csv_data(self) -> str:
        """Return summary data as a csv string."""
        headers = ["FY", "Fund", "ISIN", "Type", "LTCG", "LTCG(Taxable)", "STCG"]
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
            "Buy Value",
            "Stamp Duty",
            "Acquisition Value",
            "Sell Date",
            "Sell Value",
            "STT",
            "LTCG",
            "LTCG Taxable",
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
                        gain.coa,
                        gain.sell_date,
                        gain.sell_price,
                        gain.stt,
                        gain.ltcg,
                        gain.ltcg_taxable,
                        gain.stcg,
                    ]
                )
            csv_fp.seek(0)
            csv_data = csv_fp.read()
            return csv_data
