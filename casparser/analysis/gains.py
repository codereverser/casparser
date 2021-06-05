from collections import deque
from decimal import Decimal
from datetime import date
import itertools
from typing import List

from casparser.enums import FundType, GainType, TransactionType
from casparser.types import CASParserDataType, TransactionDataType


def get_fund_type(transactions: List[TransactionDataType]):
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
    def __init__(self, fund, transactions: List[TransactionDataType]):
        self._fund = fund
        self._original_transactions = transactions
        self.fund_type = get_fund_type(transactions)
        self._merged_transactions = self.merge_transactions()

        self.transactions = deque()
        self.gains = []

        self.process()

    @property
    def clean_transactions(self):
        return filter(lambda x: x["amount"] is not None, self._original_transactions)

    def merge_transactions(self):
        merged_transactions = {}
        for txn in sorted(self.clean_transactions, key=lambda x: (x["date"], -x["amount"])):
            if txn["date"] not in merged_transactions:
                merged_transactions[txn["date"]] = {
                    "date": txn["date"],
                    "units": Decimal(0.0),
                    "nav": Decimal(0.0),
                    "amount": Decimal(0.0),
                    "tax": Decimal(0.0),
                }
            if txn["type"] in (
                TransactionType.STT_TAX.name,
                TransactionType.STAMP_DUTY_TAX.name,
            ):
                merged_transactions[txn["date"]]["tax"] += txn["amount"]
            else:
                merged_transactions[txn["date"]]["nav"] = txn["nav"]
                merged_transactions[txn["date"]]["units"] += txn["units"]
                merged_transactions[txn["date"]]["amount"] += txn["amount"]
        return merged_transactions

    @staticmethod
    def get_fin_year(dt: date):
        if dt.month > 3:
            year1, year2 = dt.year, dt.year + 1
        else:
            year1, year2 = dt.year - 1, dt.year

        if year1 % 100 != 99:
            year2 %= 100

        return f"FY{year1}-{year2}"

    def get_gain_type(self, buy_date: date, sell_date: date):
        ltcg = {
            FundType.EQUITY: date(buy_date.year + 1, buy_date.month, buy_date.day),
            FundType.DEBT: date(buy_date.year + 3, buy_date.month, buy_date.day),
        }

        return GainType.LTCG if sell_date > ltcg[self.fund_type] else GainType.STCG

    def process(self):
        self.gains = []
        for dt in sorted(self._merged_transactions.keys()):
            txn = self._merged_transactions[dt]
            if txn["amount"] > 0:
                self.buy(dt, txn["units"], txn["nav"], txn["tax"])
            elif txn["amount"] < 0:
                self.sell(dt, txn["units"], txn["nav"], txn["tax"])
        return self.gains

    def buy(self, txn_date: date, quantity: Decimal, nav: Decimal, tax: Decimal):
        self.transactions.append((txn_date, quantity, nav, tax))

    def sell(self, sell_date: date, quantity: Decimal, nav: Decimal, tax: Decimal):
        fin_year = self.get_fin_year(sell_date)
        original_quantity = abs(quantity)
        pending_units = original_quantity
        while pending_units > 0:
            buy_date, units, buy_nav, buy_tax = self.transactions.popleft()
            if units <= pending_units:
                gain_units = units
                buy_price = round(units * buy_nav, 2)
                sell_price = round(units * nav, 2)
                stamp_duty = buy_tax
                stt = tax * units / original_quantity
            else:
                gain_units = pending_units
                buy_price = round(pending_units * buy_nav, 2)
                sell_price = round(pending_units * nav, 2)
                stamp_duty = buy_tax * pending_units / units
                stt = tax * pending_units / original_quantity
            gain_type = self.get_gain_type(buy_date, sell_date)
            pending_units -= units
            self.gains.append(
                {
                    "fy": fin_year,
                    "fund": self._fund,
                    "buy_date": buy_date,
                    "buy_price": round(buy_price, 2),
                    "stamp_duty": round(stamp_duty, 2),
                    "sell_date": sell_date,
                    "sell_price": round(sell_price, 2),
                    "stt": round(stt, 2),
                    "units": gain_units,
                    gain_type.name: round(sell_price - buy_price - stt, 2),
                }
            )
            if pending_units < 0 and buy_nav is not None:
                # Re-add the remaining units to the FIFO queue
                self.transactions.appendleft((buy_date, -1 * pending_units, buy_nav, buy_tax))


class CapitalGainReport:
    def __init__(self, data: CASParserDataType):
        self._data = data
        self._gains = []
        self.process()

    def process(self):
        self._gains = []
        for folio in self._data.get("folios", []):
            for scheme in folio.get("schemes", []):
                name = f"{scheme['scheme']} [{folio['folio']}]"
                transactions = scheme["transactions"]
                if len(transactions) > 0:
                    fifo = FIFOUnits(name, transactions)
                    self._gains.extend(fifo.gains)

    def get_summary(self):
        sorted_gains = list(sorted(self._gains, key=lambda x: (x["fy"], x["fund"], x["sell_date"])))
        summary = []
        for (fy, fund), txns in itertools.groupby(sorted_gains, key=lambda x: (x["fy"], x["fund"])):
            ltcg = stcg = Decimal(0.0)
            for txn in txns:
                ltcg += txn.get(GainType.LTCG.name, Decimal(0.0))
                stcg += txn.get(GainType.STCG.name, Decimal(0.0))
            summary.append([fy, fund, ltcg, stcg])
        return summary
