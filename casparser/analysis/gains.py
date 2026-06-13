import csv
import io
import itertools
import re
from collections import deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List

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


# Finance (No. 2) Act 2024 split the FY2024-25 LTCG regime on this date
# (equity LTCG 10% -> 12.5%, exemption 1L -> 1.25L). Schedule 112A from
# AY 2025-26 carries a column 1b ("Share/Unit Transferred") flagging which
# side of this date each *transfer* (sale) falls on.
LTCG_REGIME_CUTOFF = date(2024, 7, 23)

# Schedule 112A column 1b only exists on the AY 2025-26 (FY 2024-25) utility
# and later. We emit it when the report's FY starts in 2024 or later.
TRANSFER_COL_FROM_FY_START_YEAR = 2024


def _transfer_flag(sale_date: date) -> str:
    """Schedule 112A column 1b value for a transfer on `sale_date`:
    ``BE`` if before 23-Jul-2024, ``AE`` on or after."""
    return "BE" if sale_date < LTCG_REGIME_CUTOFF else "AE"


def _fy_needs_transfer_col(fy: str) -> bool:
    """True if `fy` (e.g. ``FY2024-25``) is FY2024-25 or later, i.e. the
    Schedule 112A column 1b applies."""
    m = re.match(r"FY(\d{4})", fy or "")
    return bool(m) and int(m.group(1)) >= TRANSFER_COL_FROM_FY_START_YEAR


@dataclass
class GainEntry112A:
    """GainEntry for schedule 112A of ITR."""

    acquired: str  # AE, BE — col 1a (acquired before/after 31-Jan-2018)
    transferred: str  # AE, BE — col 1b (transferred before/after 23-Jul-2024)
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
        # Stamp duty paid at purchase is part of the cost of acquisition
        # (CAMS/KFin both report cost "inclusive of stamp duty").
        return max(self.purchase_value, self.consideration_value) + self.stamp_duty

    @property
    def expenditure(self):
        # STT is explicitly NOT a deductible expense under section 112A,
        # and stamp duty is already folded into the cost of acquisition,
        # so there is no separately deductible transfer expenditure.
        return Decimal("0.00")

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
    def acquisition_value(self) -> Decimal:
        """Cost of acquisition including the stamp duty paid at purchase.

        Both CAMS and KFin capital-gains statements report the cost
        "inclusive of stamp duty", and under the Income-tax Act stamp
        duty paid on acquisition forms part of the cost of acquisition.
        Omitting it over-states the realised gain by the stamp amount.
        """
        return self.purchase_value + self.stamp_duty

    @property
    def gain(self) -> Decimal:
        return Decimal(round(self.sale_value - self.acquisition_value, 2))

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
            return Decimal(round(self.acquisition_value * self.index_ratio, 2))
        if self.purchase_date < self.__cutoff_date:
            if self.sale_date < self.__sell_cutoff_date:
                return self.sale_value
            return max(self.acquisition_value, min(self.fmv, self.sale_value))
        return self.acquisition_value

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


@dataclass
class GiftEntry:
    """An inter-folio gift transfer — informational only.

    Gifts are deliberately kept out of the capital-gains computation:
    the donor side is not a transfer (Sec 47(iii) → no gain), and the
    recipient side needs the *donor's* cost basis and holding period
    (Sec 49(1) / 2(42A)) which do not exist in a single CAS. This record
    exists purely to disclose the transfer.
    """

    fy: str
    fund: Fund
    direction: str  # "IN" or "OUT"
    date: date
    units: Decimal
    nav: Decimal
    value: Decimal
    counterparty_folio: str

    @classmethod
    def from_transaction(cls, fund: Fund, txn: TransactionData) -> "GiftEntry":
        dt = txn.date
        if isinstance(dt, str):
            dt = dateparse(dt).date()
        direction = "IN" if txn.type == TransactionType.GIFT_IN else "OUT"
        return cls(
            fy=get_fin_year(dt),
            fund=fund,
            direction=direction,
            date=dt,
            units=txn.units,
            nav=txn.nav,
            value=txn.amount,
            counterparty_folio=txn.gift_folio or "",
        )


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
            x.units is not None
            and x.units < 0
            and x.type not in (TransactionType.REVERSAL, TransactionType.GIFT_OUT)
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
                # Sale is partially matched against the last buy transactions.
                # Re-add the remaining units to the FIFO queue with the
                # *unallocated* stamp-duty remainder — not the full original.
                # Otherwise a lot consumed across N disposals would re-claim
                # the full original stamp on every disposal, over-stating the
                # transfer-expense deduction on Schedule 112A by a factor
                # that grows with split depth.
                self.transactions.appendleft(
                    (purchase_date, -1 * pending_units, purchase_nav, purchase_tax - stamp_duty)
                )


class CapitalGainsReport:
    """Generate Capital Gains Report from the parsed CAS data"""

    def __init__(self, data: CASData):
        self._data: CASData = data
        self._gains: List[GainEntry] = []
        self._gifts: List[GiftEntry] = []
        self.errors = []
        self.invested_amount = Decimal(0.0)
        self.current_value = Decimal(0.0)
        self.process_data()

    @property
    def gains(self) -> List[GainEntry]:
        return list(sorted(self._gains, key=lambda x: (x.fy, x.fund, x.sale_date)))

    @property
    def gifts(self) -> List[GiftEntry]:
        return list(sorted(self._gifts, key=lambda x: (x.fy, x.fund, x.date)))

    def has_gains(self) -> bool:
        return len(self.gains) > 0

    def has_gifts(self) -> bool:
        return len(self._gifts) > 0

    def has_error(self) -> bool:
        return len(self.errors) > 0

    def get_fy_list(self) -> List[str]:
        return list(sorted(set([f.fy for f in self.gains]), reverse=True))

    def process_data(self):
        self._gains = []
        self._gifts = []
        for folio in self._data.folios:
            for scheme in folio.schemes:
                transactions = scheme.transactions
                fund = Fund(
                    scheme=scheme.scheme,
                    folio=folio.folio,
                    isin=scheme.isin,
                    type=scheme.type,
                )
                # Disclose every gift transfer, regardless of direction.
                # These are not part of the capital-gains computation.
                gift_txns = [
                    t
                    for t in transactions
                    if t.type in (TransactionType.GIFT_IN, TransactionType.GIFT_OUT)
                ]
                for txn in gift_txns:
                    self._gifts.append(GiftEntry.from_transaction(fund, txn))
                has_gift_in = any(t.type == TransactionType.GIFT_IN for t in transactions)
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
                        # A FIFO shortfall on a scheme that received gifted-in
                        # units means a later sale is consuming units whose
                        # cost basis lives in the *donor's* statement, not
                        # here. Don't surface the generic mismatch; explain it.
                        if has_gift_in:
                            self.errors.append(
                                (
                                    fund.name,
                                    "Scheme received gifted-in units; capital "
                                    "gains on their later sale require the "
                                    "donor's cost basis and holding period "
                                    "(Sec 49(1) / 2(42A)), which are not present "
                                    "in this statement. Scheme excluded from "
                                    "gains — see the Gift transactions section.",
                                )
                            )
                        else:
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

    def get_gifts_csv_data(self) -> str:
        """Return the informational gift-transfer list as a csv string."""
        headers = [
            "FY",
            "Fund",
            "ISIN",
            "Direction",
            "Date",
            "Units",
            "NAV",
            "Value",
            "Counterparty Folio",
        ]
        with io.StringIO() as csv_fp:
            writer = csv.writer(csv_fp)
            writer.writerow(headers)
            for gift in self.gifts:
                writer.writerow(
                    [
                        gift.fy,
                        gift.fund.name,
                        gift.fund.isin,
                        gift.direction,
                        gift.date,
                        gift.units,
                        gift.nav,
                        gift.value,
                        gift.counterparty_folio,
                    ]
                )
            csv_fp.seek(0)
            return csv_fp.read()

    def generate_112a(self, fy) -> List[GainEntry112A]:
        fy_transactions = sorted(
            list(
                filter(
                    lambda x: x.fy == fy
                    and x.fund.type == "EQUITY"
                    and x.gain_type == GainType.LTCG,
                    self.gains,
                )
            ),
            key=lambda x: x.fund,
        )
        rows: List[GainEntry112A] = []
        for fund, txns in itertools.groupby(fy_transactions, key=lambda x: x.fund):
            entries: List[GainEntry112A] = []  # grandfathered (1a=BE), one per txn
            # AE-acquired lots are consolidated, but keyed on the transfer
            # flag (1b) so a fund sold both before and on/after the
            # 23-Jul-2024 cutoff yields one row per side (the utility
            # taxes the two sides at different rates).
            consolidated: dict[str, GainEntry112A] = {}
            for txn in txns:
                transferred = _transfer_flag(txn.sale_date)
                if txn.purchase_date <= date(2018, 1, 31):
                    entries.append(
                        GainEntry112A(
                            "BE",
                            transferred,
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
                elif transferred not in consolidated:
                    consolidated[transferred] = GainEntry112A(
                        "AE",
                        transferred,
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
                    ce = consolidated[transferred]
                    ce.purchase_value += txn.purchase_value
                    ce.stt += txn.stt
                    ce.stamp_duty += txn.stamp_duty
                    ce.units += txn.units
                    ce.sale_value += txn.sale_value
                    ce.sale_nav = Decimal(round(txn.sale_value / txn.units, 3))
            rows.extend(entries)
            rows.extend(consolidated.values())
        return rows

    def generate_112a_csv_data(self, fy):
        # Schedule 112A column 1b ("Share/Unit Transferred") was added on the
        # AY 2025-26 (FY 2024-25) utility for the 23-Jul-2024 LTCG-regime
        # split. Emit it only from FY2024-25 onward so older returns keep
        # the 14-column layout their utility expects.
        with_transfer_col = _fy_needs_transfer_col(fy)
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
        if with_transfer_col:
            headers.insert(1, "Share/Unit Transferred(1b)")
        with io.StringIO() as csv_fp:
            writer = csv.writer(csv_fp)
            writer.writerow(headers)

            for row in self.generate_112a(fy):
                values = [
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
                if with_transfer_col:
                    values.insert(1, row.transferred)
                writer.writerow(values)
            csv_fp.seek(0)
            csv_data = csv_fp.read()
            return csv_data
