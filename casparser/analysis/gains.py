import csv
import io
import itertools
from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional

from dateutil.parser import parse as dateparse
from dateutil.relativedelta import relativedelta

from casparser.enums import FundType, GainType, TransactionType
from casparser.exceptions import GainsError, IncompleteCASError
from casparser.types import CASData, TransactionData

from .utils import CII, get_fin_year, nav_search

PURCHASE_TXNS = {
    TransactionType.DIVIDEND_REINVEST,
    TransactionType.PURCHASE,
    TransactionType.PURCHASE_SIP,
    TransactionType.REVERSAL,
    # Segregated folios are not supported
    # TransactionType.SEGREGATION.name,
    TransactionType.SWITCH_IN,
    TransactionType.SWITCH_IN_MERGER,
}

SALE_TXNS = {
    TransactionType.REDEMPTION.name,
    TransactionType.SWITCH_OUT.name,
    TransactionType.SWITCH_OUT_MERGER.name,
}


@dataclass
class GainEntry112A:
    """GainEntry for schedule 112A of ITR."""

    acquired: str  # AE, BE
    isin: str
    name: str
    units: Decimal
    sale_nav: Decimal
    sale_value: Decimal
    purchase_value: Decimal
    fmv_nav: Decimal
    fmv: Decimal
    stt: Decimal
    stamp_duty: Decimal

    @property
    def consideration_value(self):
        if self.acquired == "BE":
            return min(self.fmv, self.sale_value)
        else:
            return Decimal("0.00")  # FMV not considered

    @property
    def actual_coa(self):
        return max(self.purchase_value, self.consideration_value)

    @property
    def expenditure(self):
        return self.stt + self.stamp_duty

    @property
    def deductions(self):
        return self.actual_coa + self.expenditure

    @property
    def balance(self):
        return self.sale_value - self.deductions


@dataclass
class MergedTransaction:
    """Represent net transaction on a given date"""

    dt: date
    nav: Decimal = Decimal(0.0)
    purchase: Decimal = Decimal(0.0)
    purchase_units: Decimal = Decimal(0.0)
    sale: Decimal = Decimal(0.0)
    sale_units: Decimal = Decimal(0.0)
    stamp_duty: Decimal = Decimal(0.0)
    stt: Decimal = Decimal(0.0)
    tds: Decimal = Decimal(0.0)

    def add(self, txn: TransactionData):
        txn_type = txn.type
        if txn_type in PURCHASE_TXNS and txn.units is not None:
            self.nav = txn.nav
            self.purchase_units += txn.units
            self.purchase += txn.amount
        elif txn_type in SALE_TXNS and txn.units is not None:
            self.nav = txn.nav
            self.sale_units += txn.units
            self.sale += txn.amount
        elif txn_type == TransactionType.STT_TAX:
            self.stt += txn.amount
        elif txn_type == TransactionType.STAMP_DUTY_TAX:
            self.stamp_duty += txn.amount
        elif txn_type == TransactionType.TDS_TAX:
            self.tds += txn.amount
        elif txn_type == TransactionType.SEGREGATION:
            self.nav = Decimal(0.0)
            self.purchase_units += txn.units
            self.purchase = Decimal(0.0)


@dataclass
class Fund:
    """Fund details"""

    scheme: str
    folio: str
    isin: str
    type: str

    @property
    def name(self):
        return f"{self.scheme} [{self.folio}]"

    def __lt__(self, other: "Fund"):
        return self.scheme < other.scheme


@dataclass
class GainEntry:
    """Gain data of a realised transaction"""

    fy: str
    fund: Fund
    type: str
    purchase_date: date
    purchase_nav: Decimal
    purchase_value: Decimal
    stamp_duty: Decimal
    sale_date: date
    sale_nav: Decimal
    sale_value: Decimal
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
            FundType.EQUITY.name: self.purchase_date + relativedelta(years=1),
            FundType.DEBT.name: self.purchase_date + relativedelta(years=3),
        }

        return GainType.LTCG if self.sale_date > ltcg[self.type] else GainType.STCG

    @property
    def gain(self) -> Decimal:
        return Decimal(round(self.sale_value - self.purchase_value, 2))

    @property
    def fmv_nav(self) -> Decimal:
        if self.fund.isin != self._cached_isin:
            self.__update_nav()
        return self._cached_nav

    @property
    def fmv(self) -> Decimal:
        if self.fmv_nav is None:
            return self.purchase_value
        return self.fmv_nav * self.units

    @property
    def index_ratio(self) -> Decimal:
        return Decimal(
            round(CII[get_fin_year(self.sale_date)] / CII[get_fin_year(self.purchase_date)], 2)
        )

    @property
    def coa(self) -> Decimal:
        if self.fund.type == FundType.DEBT:
            return Decimal(round(self.purchase_value * self.index_ratio, 2))
        if self.purchase_date < self.__cutoff_date:
            if self.sale_date < self.__sell_cutoff_date:
                return self.sale_value
            return max(self.purchase_value, min(self.fmv, self.sale_value))
        return self.purchase_value

    @property
    def ltcg_taxable(self) -> Decimal:
        if self.gain_type == GainType.LTCG:
            return Decimal(round(self.sale_value - self.coa, 2))
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


def get_fund_type(transactions: List[TransactionData]) -> FundType:
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
            x.units is not None and x.units < 0 and x.type != TransactionType.REVERSAL
            for x in transactions
        ]
    )
    if not valid:
        return FundType.UNKNOWN
    return (
        FundType.EQUITY
        if any([x.type == TransactionType.STT_TAX for x in transactions])
        else FundType.DEBT
    )


class FIFOUnits:
    """First-In First-Out units calculator."""

    def __init__(self, fund: Fund, transactions: List[TransactionData]):
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
        self.invested = Decimal(0.0)
        self.balance = Decimal(0.0)
        self.gains: List[GainEntry] = []

        self.process()

    @property
    def clean_transactions(self):
        """remove redundant transactions, without amount"""
        return filter(lambda x: x.amount is not None, self._original_transactions)

    def merge_transactions(self):
        """Group transactions by date with taxes and investments/redemptions separated."""
        merged_transactions = {}
        for txn in sorted(self.clean_transactions, key=lambda x: (x.date, -x.amount)):
            dt = txn.date

            if isinstance(dt, str):
                dt = dateparse(dt).date()

            if dt not in merged_transactions:
                merged_transactions[dt] = MergedTransaction(dt)
            merged_transactions[dt].add(txn)
        return merged_transactions

    def process(self):
        self.gains = []
        for dt in sorted(self._merged_transactions.keys()):
            txn = self._merged_transactions[dt]
            if txn.purchase_units > 0:
                self.buy(dt, txn.purchase_units, txn.nav, txn.stamp_duty)
            if txn.sale_units < 0:
                self.sell(dt, txn.sale_units, txn.nav, txn.stt)
        return self.gains

    def buy(self, txn_date: date, quantity: Decimal, nav: Decimal, tax: Decimal):
        self.transactions.append((txn_date, quantity, nav, tax))
        self.invested += quantity * nav
        self.balance += quantity

    def sell(self, sell_date: date, quantity: Decimal, nav: Decimal, tax: Decimal):
        fin_year = get_fin_year(sell_date)
        original_quantity = abs(quantity)
        pending_units = original_quantity
        while pending_units >= 1e-2:
            try:
                purchase_date, units, purchase_nav, purchase_tax = self.transactions.popleft()
            except IndexError:
                raise GainsError(
                    f"FIFOUnits mismatch for {self._fund.name}. Please contact support."
                )
            if units <= pending_units:
                gain_units = units
            else:
                gain_units = pending_units

            purchase_value = round(gain_units * purchase_nav, 2)
            sale_value = round(gain_units * nav, 2)
            stamp_duty = round(purchase_tax * gain_units / units, 2)
            stt = round(tax * gain_units / original_quantity, 2)

            ge = GainEntry(
                fy=fin_year,
                fund=self._fund,
                type=self.fund_type.name,
                purchase_date=purchase_date,
                purchase_nav=purchase_nav,
                purchase_value=purchase_value,
                stamp_duty=stamp_duty,
                sale_date=sell_date,
                sale_nav=nav,
                sale_value=sale_value,
                stt=stt,
                units=gain_units,
            )
            self.gains.append(ge)

            self.balance -= gain_units
            self.invested -= purchase_value

            pending_units -= units
            if pending_units < 0 and purchase_nav is not None:
                # Sale is partially matched against the last buy transactions
                # Re-add the remaining units to the FIFO queue
                self.transactions.appendleft(
                    (purchase_date, -1 * pending_units, purchase_nav, purchase_tax)
                )


class CapitalGainsReport:
    """Generate Capital Gains Report from the parsed CAS data"""

    def __init__(self, data: CASData):
        self._data: CASData = data
        self._gains: List[GainEntry] = []
        self.errors = []
        self.invested_amount = Decimal(0.0)
        self.current_value = Decimal(0.0)
        self.process_data()

    @property
    def gains(self) -> List[GainEntry]:
        return list(sorted(self._gains, key=lambda x: (x.fy, x.fund, x.sale_date)))

    def has_gains(self) -> bool:
        return len(self.gains) > 0

    def has_error(self) -> bool:
        return len(self.errors) > 0

    def get_fy_list(self) -> List[str]:
        return list(sorted(set([f.fy for f in self.gains]), reverse=True))

    def process_data(self):
        self._gains = []
        for folio in self._data.folios:
            for scheme in folio.schemes:
                transactions = scheme.transactions
                fund = Fund(
                    scheme=scheme.scheme,
                    folio=folio.folio,
                    isin=scheme.isin,
                    type=scheme.type,
                )
                if len(transactions) > 0:
                    if scheme.open >= 0.01:
                        raise IncompleteCASError(
                            "Incomplete CAS found. For gains computation, "
                            "all folios should have zero opening balance"
                        )
                    try:
                        fifo = FIFOUnits(fund, transactions)
                        self.invested_amount += fifo.invested
                        self.current_value += scheme.valuation.value
                        self._gains.extend(fifo.gains)
                    except GainsError as exc:
                        self.errors.append((fund.name, str(exc)))

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
        headers = ["FY", "Fund", "ISIN", "Type", "LTCG(Realized)", "LTCG(Taxable)", "STCG"]
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
            "Purchase Date",
            "Purchase Value",
            "Stamp Duty",
            "Acquisition Value",
            "Sale Date",
            "Sale Value",
            "STT",
            "LTCG Realized",
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
                        gain.purchase_date,
                        gain.purchase_value,
                        gain.stamp_duty,
                        gain.coa,
                        gain.sale_date,
                        gain.sale_value,
                        gain.stt,
                        gain.ltcg,
                        gain.ltcg_taxable,
                        gain.stcg,
                    ]
                )
            csv_fp.seek(0)
            csv_data = csv_fp.read()
            return csv_data

    def generate_112a(self, fy) -> List[GainEntry112A]:
        fy_transactions = sorted(
            list(filter(lambda x: x.fy == fy and x.fund.type == "EQUITY", self.gains)),
            key=lambda x: x.fund,
        )
        rows: List[GainEntry112A] = []
        for fund, txns in itertools.groupby(fy_transactions, key=lambda x: x.fund):
            consolidated_entry: Optional[GainEntry112A] = None
            entries = []
            for txn in txns:
                if txn.purchase_date <= date(2018, 1, 31):
                    entries.append(
                        GainEntry112A(
                            "BE",
                            fund.isin,
                            fund.scheme,
                            txn.units,
                            txn.sale_nav,
                            txn.sale_value,
                            txn.purchase_value,
                            txn.fmv_nav,
                            txn.fmv,
                            txn.stt,
                            txn.stamp_duty,
                        )
                    )
                else:
                    if consolidated_entry is None:
                        consolidated_entry = GainEntry112A(
                            "AE",
                            fund.isin,
                            fund.scheme,
                            txn.units,
                            txn.sale_nav,
                            txn.sale_value,
                            txn.purchase_value,
                            Decimal(0.0),
                            Decimal(0.0),
                            txn.stt,
                            txn.stamp_duty,
                        )
                    else:
                        consolidated_entry.purchase_value += txn.purchase_value
                        consolidated_entry.stt += txn.stt
                        consolidated_entry.stamp_duty += txn.stamp_duty
                        consolidated_entry.units += txn.units
                        consolidated_entry.sale_value += txn.sale_value
                        consolidated_entry.sale_nav = Decimal(round(txn.sale_value / txn.units, 3))
            rows.extend(entries)
            if consolidated_entry is not None:
                rows.append(consolidated_entry)
        return rows

    def generate_112a_csv_data(self, fy):
        headers = [
            "Share/Unit acquired(1a)",
            "ISIN Code(2)",
            "Name of the Share/Unit(3)",
            "No. of Shares/Units(4)",
            "Sale-price per Share/Unit(5)",
            "Full Value of Consideration(Total Sale Value)(6) = 4 * 5",
            "Cost of acquisition without indexation(7)",
            "Cost of acquisition(8)",
            "If the long term capital asset was acquired before 01.02.2018(9)",
            "Fair Market Value per share/unit as on 31st January 2018(10)",
            "Total Fair Market Value of capital asset as per section 55(2)(ac)(11) = 4 * 10",
            "Expenditure wholly and exclusively in connection with transfer(12)",
            "Total deductions(13) = 7 + 12",
            "Balance(14) = 6 - 13",
        ]
        with io.StringIO() as csv_fp:
            writer = csv.writer(csv_fp)
            writer.writerow(headers)

            for row in self.generate_112a(fy):
                writer.writerow(
                    [
                        row.acquired,
                        row.isin,
                        row.name,
                        str(row.units),
                        str(row.sale_nav),
                        str(row.sale_value),
                        str(row.actual_coa),
                        str(row.purchase_value),
                        str(row.consideration_value),
                        str(row.fmv_nav),
                        str(row.fmv),
                        str(row.expenditure),
                        str(row.deductions),
                        str(row.balance),
                    ]
                )
            csv_fp.seek(0)
            csv_data = csv_fp.read()
            return csv_data
